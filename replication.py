import json
import base64
import time
import threading
import requests

import db
import handlers
from state import connections_snapshot, timestamp
from logger import wps_logger

# replication.py is the DAPPS-facing half of instance-to-instance replication. db.py captures
# one replication event per user-driven write (see _replicate_capture there), in the same
# SQLite transaction as the write itself; this module owns everything downstream of that:
# turning captured events into DAPPS submissions (outbox pump), turning inbound DAPPS
# messages back into local writes + live broadcasts (inbox pump), and periodically checking
# every peer is still level (reconcile pump).
#
# Deliberately NOT warm-reloadable like db.py/handlers.py - these are process-lifetime
# background threads, started once from wps.py. They call handlers.<func>(...) and
# db.<func>(...) by module attribute, same as wps.py does, so a warm reload of either of
# those modules is still picked up on the next tick without restarting these threads.

env_source = open("env.json")
env = json.load(env_source)
env_source.close()

REPLICATION_CONFIG = env.get('replication', {})
ENABLED = REPLICATION_CONFIG.get('enabled', False)
DAPPS_CALLSIGN = REPLICATION_CONFIG.get('dappsCallsign')  # this node's DAPPS callsign - the address peers send to
ORIGIN = REPLICATION_CONFIG.get('originCallsign') or DAPPS_CALLSIGN  # this node's replication identity: envelope `origin`, and the `o` key on posts at receivers

# peers: [{"originCallsign": "GB7ABC", "dappsCallsign": "GB7ABC-7"}, ...]. A bare string is
# accepted as a peer whose origin and DAPPS callsigns are the same.
def _normalise_peers(raw):
    peers = []
    for p in raw:
        if isinstance(p, str):
            peers.append((p, p))
        elif isinstance(p, dict) and p.get('dappsCallsign'):
            peers.append((p.get('originCallsign') or p['dappsCallsign'], p['dappsCallsign']))
    return peers

_PEER_PAIRS = _normalise_peers(REPLICATION_CONFIG.get('peers', []))
PEERS = [dapps for _, dapps in _PEER_PAIRS]  # DAPPS callsigns: send targets, and the keys of replication_peer_ack
_ORIGIN_TO_DAPPS = {origin.upper(): dapps for origin, dapps in _PEER_PAIRS}
_DAPPS_TO_ORIGIN = {dapps.upper(): origin for origin, dapps in _PEER_PAIRS}


def _dapps_for_origin(origin):
    return _ORIGIN_TO_DAPPS.get(origin.upper(), origin)
APP_SLUG = REPLICATION_CONFIG.get('appSlug', 'wps-repl')
DAPPS_REST_URL = REPLICATION_CONFIG.get('dappsRestUrl', 'http://127.0.0.1:5000').rstrip('/')
STREAM_TTL_SECONDS = REPLICATION_CONFIG.get('streamTtlSeconds', 604800)  # 7 days
OUTBOX_POLL_SECONDS = REPLICATION_CONFIG.get('outboxPollSeconds', 5)
INBOX_POLL_SECONDS = REPLICATION_CONFIG.get('inboxPollSeconds', 5)
# After any replicated activity (a local write, or a data event from a peer) the inbox is
# polled faster for a while, since that's when a reply is likely and poll delay is felt.
INBOX_FAST_POLL_SECONDS = REPLICATION_CONFIG.get('inboxFastPollSeconds', 1)
INBOX_FAST_POLL_WINDOW_SECONDS = REPLICATION_CONFIG.get('inboxFastPollWindowSeconds', 300)
# notify_local_event() fires from inside the writer's still-open transaction, so the outbox
# pump waits this long after waking for the commit to land before it reads the outbox.
OUTBOX_WAKE_SETTLE_SECONDS = 0.1
RECONCILE_INTERVAL_SECONDS = REPLICATION_CONFIG.get('reconcileIntervalSeconds', 300)
# Application acks are held this long and combined: one ack covers every seq up to it (see
# _handle_app_ack), so a burst of events costs one DAPPS transfer back instead of one each.
ACK_DELAY_SECONDS = REPLICATION_CONFIG.get('ackDelaySeconds', 30)
# Up to this many consecutive events for a peer go in one DAPPS message when the outbox (or a
# sync.request resend) has a backlog. 1 sends every event on its own, which peers running an
# older version need: they can't read a batch, and it would sit unacked at the head of the stream.
BATCH_SIZE = max(1, int(REPLICATION_CONFIG.get('batchSize', 1)))
# Set only on a brand-new instance that should join mid-history rather than replay everything:
# epoch-ms timestamp. Consulted once, at the first start with no replication_origin_cursor rows
# yet - see _start_bootstrap_if_configured. Inert (and safe to leave in env.json) afterwards.
BOOTSTRAP_FROM_TS = REPLICATION_CONFIG.get('bootstrapFromTs')
ACTIVITY_RETENTION_DAYS = REPLICATION_CONFIG.get('activityRetentionDays', 7)


def _now_ms():
    return round(time.time() * 1000)


# --- Activity recording (read by replication_dashboard.py) ---------------------------------

def _record(direction, category, op, status, peer=None, origin=None, seq=None, detail=None, dapps_id=None, event=None):
    '''
    Appends one row to replication_activity on its own connection, so it never joins (or
    commits) a caller's transaction. Observational only - a failure here is logged and
    swallowed, never allowed to disturb replication itself.
    '''
    try:
        conn = db.get_db_connection()
        try:
            conn.execute(
                "INSERT INTO replication_activity (at, direction, category, op, peer, origin, seq, status, detail, dapps_id, event) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now_ms(), direction, category, op, peer, origin, seq, status, detail, dapps_id,
                 json.dumps(event, separators=(',', ':')) if event is not None else None)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        wps_logger("REPLICATION ACTIVITY", ORIGIN, f"Failed to record activity ({direction} {op} {status}): {e}", "ERROR")


# Pumps retry failures every tick; these remember what has already been recorded so a peer or
# DAPPS being down produces one activity row per failure, not one every few seconds.
_last_outbox_failure = {}       # peer -> seq whose submit failure was last recorded
_recorded_inbound_errors = {}   # dapps_id -> error text last recorded for it
_dapps_poll_ok = True


def _prune_activity():
    conn = db.get_db_connection()
    try:
        conn.execute("DELETE FROM replication_activity WHERE at < ?", (_now_ms() - ACTIVITY_RETENTION_DAYS * 86400000,))
        conn.commit()
    finally:
        conn.close()


# --- Wire format: short keys over the air, long names everywhere else ----------------------

# Envelopes and control messages go over the air with short keys, WPS-protocol style, to save
# bytes on slow links. Everything local - replication_log, replication_pending, the activity
# log and the dashboard - keeps the long names, so only the DAPPS boundary translates. Only
# top-level keys (and those of each batched event) are renamed, never inside `key` or `data`.
_WIRE_KEYS = {"origin": "o", "seq": "s", "epoch": "e", "op": "a"}
_WIRE_OPS = {"post.insert": "p.i", "post.edit": "p.ed", "post.emoji": "p.em"}
_LONG_KEYS = {short: long for long, short in _WIRE_KEYS.items()}
_LONG_OPS = {short: long for long, short in _WIRE_OPS.items()}


def _to_wire(message):
    wire = {_WIRE_KEYS.get(k, k): v for k, v in message.items()}
    if "a" in wire:
        wire["a"] = _WIRE_OPS.get(wire["a"], wire["a"])
    if isinstance(wire.get("events"), list):
        wire["events"] = [_to_wire(e) if isinstance(e, dict) else e for e in wire["events"]]
    return wire


def _from_wire(message):
    '''Inverse of _to_wire. A message in the old long-key format (no `a`) passes through as is.'''
    if not isinstance(message, dict) or "a" not in message:
        return message
    long = {_LONG_KEYS.get(k, k): v for k, v in message.items()}
    long["op"] = _LONG_OPS.get(long["op"], long["op"])
    if isinstance(long.get("events"), list):
        long["events"] = [_from_wire(e) for e in long["events"]]
    return long


def _decode_payload(payload):
    return _from_wire(json.loads(base64.b64decode(payload)))


# --- DAPPS REST client --------------------------------------------------------------------

def _dapps_submit(dest_callsign, payload_dict, stream_id=None, gap_timeout_seconds=None, ttl=None):
    body = {
        "app": APP_SLUG,
        "destCallsign": dest_callsign,
        "payload": base64.b64encode(json.dumps(_to_wire(payload_dict), separators=(',', ':')).encode()).decode(),
    }
    if ttl:
        body["ttl"] = ttl
    if stream_id:
        body["streamId"] = stream_id
        body["streamGapTimeoutSeconds"] = gap_timeout_seconds if gap_timeout_seconds is not None else 0

    resp = requests.post(f"{DAPPS_REST_URL}/AppApi/outbound", json=body, timeout=10)
    resp.raise_for_status()
    return resp.json()["id"]


def _dapps_inbound():
    resp = requests.get(f"{DAPPS_REST_URL}/AppApi/inbound/{APP_SLUG}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _dapps_ack(dapps_id):
    resp = requests.post(f"{DAPPS_REST_URL}/AppApi/inbound/{APP_SLUG}/{dapps_id}/ack", timeout=10)
    resp.raise_for_status()


def _stream_id_for(origin, epoch):
    return f"{APP_SLUG}:{origin}.e{epoch}"


# --- Wake-ups: submit new events promptly, poll the inbox faster while a conversation is on -

_outbox_wake = threading.Event()
_inbox_fast_until = 0.0  # time.monotonic() deadline; the inbox polls at the fast rate until then


def notify_local_event():
    '''
    Called by db._replicate_capture whenever a local write captures a replication event. Wakes
    the outbox pump now rather than at its next tick, and starts a fast inbox window because a
    reply from a peer is now likely.
    '''
    _outbox_wake.set()
    _extend_fast_inbox()


def _extend_fast_inbox():
    global _inbox_fast_until
    _inbox_fast_until = time.monotonic() + INBOX_FAST_POLL_WINDOW_SECONDS


# --- Outbox pump: replication_log/outbox (captured by db.py) -> DAPPS ---------------------

def _outbox_pump_loop():
    while True:
        try:
            _outbox_pump_tick()
        except Exception as e:
            wps_logger("REPLICATION OUTBOX", ORIGIN, f"Tick error: {e}", "ERROR")
        # The timeout still matters: it retries submissions that DAPPS refused last tick.
        if _outbox_wake.wait(OUTBOX_POLL_SECONDS):
            _outbox_wake.clear()
            time.sleep(OUTBOX_WAKE_SETTLE_SECONDS)


def _outbox_pump_tick():
    conn = db.get_db_connection()
    cur = conn.cursor()

    # Each peer has its own submitted_seq cursor, so a peer that is down (or slow to accept)
    # stalls only its own submissions - never another peer's - and events are always handed
    # to DAPPS for a given peer in seq order. On the first failure for a peer we stop and
    # retry from that same seq next tick.
    for peer in PEERS:
        cur.execute("SELECT submitted_seq FROM replication_peer_ack WHERE peer = ?", (peer,))
        row = cur.fetchone()
        submitted_seq = row[0] if row else 0

        cur.execute(
            "SELECT o.seq, l.event, o.dapps_ids FROM replication_outbox o "
            "JOIN replication_log l ON l.origin = ? AND l.seq = o.seq "
            "WHERE o.seq > ? ORDER BY o.seq ASC LIMIT ?",
            (ORIGIN, submitted_seq, max(50, BATCH_SIZE))
        )
        rows = [(seq, json.loads(event_json), dapps_ids_json) for seq, event_json, dapps_ids_json in cur.fetchall()]
        for chunk in _batches(rows):
            envelopes = [envelope for _, envelope, _ in chunk]
            first_seq, last_seq = chunk[0][0], chunk[-1][0]
            label = _events_label(ORIGIN, first_seq, last_seq)
            # Any ack held for this peer rides along instead of costing its own DAPPS message
            # later. Added per submission only - replication_log keeps the envelope without it.
            carried = _take_held_acks(peer)
            payload = _events_payload(envelopes, {origin: held_seq for origin, (held_seq, _) in carried.items()})
            try:
                dapps_id = _dapps_submit(peer, payload, stream_id=_stream_id_for(ORIGIN, envelopes[0]["epoch"]), gap_timeout_seconds=0, ttl=STREAM_TTL_SECONDS)
            except Exception as e:
                _restore_held_acks(carried)
                wps_logger("REPLICATION OUTBOX", ORIGIN, f"Submit {label} to {peer} failed, will retry: {e}", "ERROR")
                if _last_outbox_failure.get(peer) != first_seq:
                    _last_outbox_failure[peer] = first_seq
                    _record("out", "data", envelopes[0].get("op"), "failed", peer=peer, origin=ORIGIN, seq=first_seq, detail=f"Submit to DAPPS failed, retrying every tick: {e}")
                break

            now = _now_ms()
            for seq, _, dapps_ids_json in chunk:
                dapps_ids = json.loads(dapps_ids_json) if dapps_ids_json else {}
                dapps_ids[peer] = dapps_id
                cur.execute(
                    "UPDATE replication_outbox SET dapps_ids = ?, submitted_at = ? WHERE seq = ?",
                    (json.dumps(dapps_ids), now if all(p in dapps_ids for p in PEERS) else None, seq)
                )
            cur.execute("UPDATE replication_peer_ack SET submitted_seq = ? WHERE peer = ?", (last_seq, peer))
            conn.commit()
            _last_outbox_failure.pop(peer, None)
            _last_data_sent_to[peer.upper()] = time.monotonic()
            note = _batch_note(chunk)
            for seq, envelope, _ in chunk:
                _record("out", "data", envelope.get("op"), "sent", peer=peer, origin=ORIGIN, seq=seq, detail=note, dapps_id=dapps_id)
            for origin, (held_seq, _) in carried.items():
                _acked_up_to[origin] = max(_acked_up_to.get(origin, 0), held_seq)
                ack = {"op": "ack", "origin": origin, "seq": held_seq, "by": DAPPS_CALLSIGN}
                _record("out", "sync", "ack", "sent", peer=peer, origin=origin, seq=held_seq,
                        detail=f"{_describe_control(ack)} - carried on {label}", dapps_id=dapps_id, event=ack)


def _batches(rows):
    '''
    Splits (seq, envelope, ...) rows, already in seq order, into runs of up to BATCH_SIZE that
    can each go as one DAPPS message. A run never spans an epoch change, since the stream id
    (and so DAPPS's ordering) is per epoch.
    '''
    chunk = []
    for row in rows:
        if chunk and (len(chunk) >= BATCH_SIZE or row[1]["epoch"] != chunk[0][1]["epoch"]):
            yield chunk
            chunk = []
        chunk.append(row)
    if chunk:
        yield chunk


def _events_payload(envelopes, acks=None):
    '''
    The DAPPS payload for a run of our own consecutive events: the envelope itself for one,
    otherwise a batch {"op": "batch", "origin", "epoch", "events": [...]}. `acks` held for the
    recipient are added at this level either way.
    '''
    if len(envelopes) == 1:
        payload = envelopes[0]
    else:
        payload = {"v": 1, "op": "batch", "origin": ORIGIN, "epoch": envelopes[0]["epoch"], "events": envelopes}
    return dict(payload, acks=acks) if acks else payload


def _events_label(origin, first_seq, last_seq):
    return f"{origin}/{first_seq}" if first_seq == last_seq else f"{origin}/{first_seq}-{last_seq}"


def _batch_note(chunk):
    return f"In batch of {len(chunk)} ({chunk[0][0]}-{chunk[-1][0]})" if len(chunk) > 1 else None


def _retire_acked_outbox_rows(cur, conn):
    '''
    An outbox row is fully replicated (and can be deleted) once every configured peer has
    acknowledged it at the application level - see _handle_app_ack. replication_peer_ack is
    pre-seeded with one row per configured peer at 0 (see init()) precisely so a peer that
    hasn't acked anything yet still counts, rather than being silently excluded from MIN().
    '''
    if not PEERS:
        return
    placeholders = ",".join("?" * len(PEERS))
    cur.execute(f"SELECT MIN(peer_acked_seq) FROM replication_peer_ack WHERE peer IN ({placeholders})", PEERS)
    row = cur.fetchone()
    min_acked = row[0] if row and row[0] is not None else 0
    if min_acked > 0:
        cur.execute("DELETE FROM replication_outbox WHERE seq <= ?", (min_acked,))
        conn.commit()


# --- Applying an inbound event: db write + reuse of WPS's own live-session broadcast ------

def _apply_and_broadcast(cur, envelope):
    op = envelope["op"]
    key = envelope.get("key")  # absent on inserts, which apply from data alone
    data = envelope["data"]

    if op == "post.insert":
        post = dict(data)  # copy: the envelope itself is recorded as received, without the local `o`
        post["o"] = envelope["origin"]
        # rt (replication time): ms between the origin stamping dts and this instance applying the post
        if isinstance(post.get("dts"), (int, float)):
            post["rt"] = round(time.time() * 1000) - post["dts"]
        insert_resp = db.dbInsertPost(cur, post)
        if insert_resp["result"] == "failure":
            raise RuntimeError(f"dbInsertPost failed: {insert_resp['error']}")

        subscribers_resp = db.dbChannelSubscribers(cur, post["fc"], post["cid"])
        if subscribers_resp["result"] == "success":
            handlers.broadcast_post_handler(cur, subscribers_resp["data"], post, post["fc"], None)

    elif op == "post.edit":
        existing = db.dbPostSearch(cur, key["cid"], key["ts"])
        if existing["result"] != "success" or existing["data"] is None:
            raise RuntimeError(f"post.edit for unknown post cid={key['cid']} ts={key['ts']}")
        if existing["data"].get("edts", 0) >= data["edts"]:
            wps_logger("REPLICATION APPLY", ORIGIN, f"Stale post.edit for cid={key['cid']} ts={key['ts']}, ignoring")
            return "stale"

        update_resp = db.dbUpdatePost(cur, key["cid"], key["ts"], {"edts": data["edts"], "p": data["p"], "ed": 1})
        if update_resp["result"] == "failure":
            raise RuntimeError(f"dbUpdatePost failed: {update_resp['error']}")
        updated_post = update_resp["data"]

        subscribers_resp = db.dbChannelSubscribers(cur, updated_post["fc"], key["cid"])
        if subscribers_resp["result"] == "success":
            subscribing_callsigns = [s["callsign"] for s in subscribers_resp["data"]]
            broadcast_payload = {"t": "cped", "ts": key["ts"], "edts": data["edts"], "cid": key["cid"], "p": data["p"]}
            for C in connections_snapshot():
                if C["callsign"] in subscribing_callsigns:
                    handlers.socket_send_handler_other_connected_user(
                        cur, sending_callsign=updated_post["fc"], sending_connection=None,
                        receiving_callsign=C["callsign"], receiving_connection=C["socket"],
                        payload=broadcast_payload
                    )

    elif op == "post.emoji":
        existing = db.dbPostSearch(cur, key["cid"], key["ts"])
        if existing["result"] != "success" or existing["data"] is None:
            raise RuntimeError(f"post.emoji for unknown post cid={key['cid']} ts={key['ts']}")
        if existing["data"].get("ets", 0) >= data["ets"]:
            wps_logger("REPLICATION APPLY", ORIGIN, f"Stale post.emoji for cid={key['cid']} ts={key['ts']}, ignoring")
            return "stale"

        update_resp = db.dbUpdatePost(cur, key["cid"], key["ts"], {"e": data["e"], "ets": data["ets"]})
        if update_resp["result"] == "failure":
            raise RuntimeError(f"dbUpdatePost failed: {update_resp['error']}")
        # v1 deliberately does not push a live update to connected clients for emoji - the
        # captured event only carries the merged reaction set, not the single add/remove
        # delta the 'cpem' wire type expects, and emoji are already documented as best-effort/
        # no-ack in WPS. The DB still converges; connected clients pick it up next resync.

    elif op == "msg.insert":
        insert_resp = db.dbInsertMessage(cur, data)
        if insert_resp["result"] == "failure":
            raise RuntimeError(f"dbInsertMessage failed: {insert_resp['error']}")

        for C in connections_snapshot():
            if C["callsign"] == data["tc"]:
                handlers.socket_send_handler_other_connected_user(
                    cur, sending_callsign=data["fc"], sending_connection=None,
                    receiving_callsign=C["callsign"], receiving_connection=C["socket"],
                    payload=data
                )

    elif op == "msg.edit":
        existing = db.dbMessageSearch(cur, key["_id"])
        if existing["result"] != "success" or existing["data"] is None:
            raise RuntimeError(f"msg.edit for unknown message _id={key['_id']}")
        if existing["data"].get("edts", 0) >= data["edts"]:
            wps_logger("REPLICATION APPLY", ORIGIN, f"Stale msg.edit for _id={key['_id']}, ignoring")
            return "stale"

        update_resp = db.dbUpdateMessage(cur, key["_id"], {"edts": data["edts"], "m": data["m"], "ed": 1})
        if update_resp["result"] == "failure":
            raise RuntimeError(f"dbUpdateMessage failed: {update_resp['error']}")
        edited_message = update_resp["data"]

        broadcast_payload = {"t": "med", "_id": key["_id"], "m": data["m"]}
        for C in connections_snapshot():
            if C["callsign"] == edited_message["tc"]:
                handlers.socket_send_handler_other_connected_user(
                    cur, sending_callsign=edited_message["fc"], sending_connection=None,
                    receiving_callsign=C["callsign"], receiving_connection=C["socket"],
                    payload=broadcast_payload
                )

    elif op == "msg.emoji":
        existing = db.dbMessageSearch(cur, key["_id"])
        if existing["result"] != "success" or existing["data"] is None:
            raise RuntimeError(f"msg.emoji for unknown message _id={key['_id']}")
        if existing["data"].get("ets", 0) >= data["ets"]:
            wps_logger("REPLICATION APPLY", ORIGIN, f"Stale msg.emoji for _id={key['_id']}, ignoring")
            return "stale"

        update_resp = db.dbUpdateMessage(cur, key["_id"], {"e": data["e"], "ets": data["ets"]})
        if update_resp["result"] == "failure":
            raise RuntimeError(f"dbUpdateMessage failed: {update_resp['error']}")
        # Same v1 scope note as post.emoji above - DB converges, no live push.

    elif op == "user.update":
        callsign = key["callsign"]
        existing = db.dbUserSearch(cur, callsign)
        if existing["result"] != "success" or existing["data"] is None:
            # Users are created by their own first connect on each instance, not by replication.
            wps_logger("REPLICATION APPLY", ORIGIN, f"user.update for {callsign}, not a user on this instance, ignoring")
            return "ignored"
        # name_last_updated is both the last-writer-wins guard and the watermark clients use to
        # pick up name changes (dbGetUpdatedHams), so it replicates alongside name.
        if (existing["data"].get("name_last_updated") or 0) >= (data.get("name_last_updated") or 0):
            wps_logger("REPLICATION APPLY", ORIGIN, f"Stale user.update for {callsign}, ignoring")
            return "stale"
        update_resp = db.dbUserUpdate(cur, callsign, {k: v for k, v in data.items() if k in db.REPLICATED_USER_FIELDS})
        if update_resp["result"] == "failure":
            raise RuntimeError(f"dbUserUpdate failed: {update_resp['error']}")

    else:
        wps_logger("REPLICATION APPLY", ORIGIN, f"Unknown op '{op}', ignoring", "ERROR")
        return "ignored"

    return "applied"


def _apply_one(conn, cur, origin, seq, envelope):
    '''Returns the outcome from _apply_and_broadcast: "applied", "stale" or "ignored".'''
    db.set_applying_remote(True)
    try:
        outcome = _apply_and_broadcast(cur, envelope)
        cur.execute(
            "INSERT INTO replication_origin_cursor (origin, last_applied_seq) VALUES (?, ?) "
            "ON CONFLICT(origin) DO UPDATE SET last_applied_seq = excluded.last_applied_seq",
            (origin, seq)
        )
        conn.commit()
        return outcome
    except Exception:
        conn.rollback()
        raise
    finally:
        db.set_applying_remote(False)


def _drain_pending(conn, cur, origin):
    while True:
        cur.execute("SELECT last_applied_seq FROM replication_origin_cursor WHERE origin = ?", (origin,))
        row = cur.fetchone()
        last_applied = row[0] if row else 0

        cur.execute("SELECT seq, event FROM replication_pending WHERE origin = ? AND seq = ?", (origin, last_applied + 1))
        row = cur.fetchone()
        if row is None:
            return
        seq, event_json = row
        envelope = json.loads(event_json)

        outcome = _apply_one(conn, cur, origin, seq, envelope)
        cur.execute("DELETE FROM replication_pending WHERE origin = ? AND seq = ?", (origin, seq))
        conn.commit()
        _record("in", "data", envelope.get("op"), outcome, peer=_dapps_for_origin(origin), origin=origin, seq=seq,
                detail="Applied from the out-of-order buffer", event=envelope)
        _queue_app_ack(origin, seq)


# --- Inbox pump: DAPPS -> apply / control messages -----------------------------------------

def _inbox_pump_loop():
    while True:
        try:
            _inbox_pump_tick()
            _flush_due_acks()
        except Exception as e:
            wps_logger("REPLICATION INBOX", ORIGIN, f"Tick error: {e}", "ERROR")
        fast = time.monotonic() < _inbox_fast_until
        time.sleep(INBOX_FAST_POLL_SECONDS if fast else INBOX_POLL_SECONDS)


def _inbox_pump_tick():
    global _dapps_poll_ok
    try:
        inbound = _dapps_inbound()
    except Exception as e:
        wps_logger("REPLICATION INBOX", ORIGIN, f"Failed to poll DAPPS inbound: {e}", "ERROR")
        if _dapps_poll_ok:
            _dapps_poll_ok = False
            _record("in", "system", "dapps.poll", "failed", detail=f"Cannot poll local DAPPS at {DAPPS_REST_URL}: {e}")
        return

    if not _dapps_poll_ok:
        _dapps_poll_ok = True
        _record("in", "system", "dapps.poll", "recovered", detail=f"Local DAPPS at {DAPPS_REST_URL} reachable again")

    for msg in inbound:
        try:
            _handle_inbound(msg)
            _recorded_inbound_errors.pop(msg.get("id"), None)
        except Exception as e:
            # Deliberately don't ack - DAPPS will redeliver next poll and we'll try again.
            wps_logger("REPLICATION INBOX", ORIGIN, f"Failed to handle inbound {msg.get('id')}: {e}", "ERROR")
            _record_inbound_error(msg, e)


def _record_inbound_error(msg, error):
    dapps_id = msg.get("id")
    if _recorded_inbound_errors.get(dapps_id) == str(error):
        return  # same message failing the same way on redelivery - already in the log
    if len(_recorded_inbound_errors) > 1000:
        _recorded_inbound_errors.clear()
    _recorded_inbound_errors[dapps_id] = str(error)
    try:
        envelope = _decode_payload(msg["payload"])
    except Exception:
        envelope = None
    op = envelope.get("op") if isinstance(envelope, dict) else None
    _record("in", "sync" if op in _CONTROL_OPS else "data", op, "error", peer=msg.get("sourceCallsign"),
            origin=envelope.get("origin") if isinstance(envelope, dict) else None,
            seq=envelope.get("seq") if isinstance(envelope, dict) and op not in _CONTROL_OPS else None,
            detail=f"Not acked, DAPPS will redeliver: {error}", dapps_id=dapps_id, event=envelope)


_CONTROL_OPS = {"ack", "digest", "sync.request", "seq_at.request", "seq_at.response"}


def _describe_control(envelope):
    op = envelope.get("op")
    if op == "ack":
        return f"{envelope.get('by')} applied {envelope.get('origin')}/{envelope.get('seq')}"
    if op == "digest":
        return f"{envelope.get('origin')} latest_seq={envelope.get('latest_seq')}"
    if op == "sync.request":
        return f"{envelope.get('requested_by')} asks {envelope.get('origin')} for {envelope.get('from_seq')}-{envelope.get('to_seq')}"
    if op == "seq_at.request":
        return f"{envelope.get('requested_by')} asks {envelope.get('origin')} for seq at ts={envelope.get('ts')}"
    if op == "seq_at.response":
        return f"{envelope.get('origin')} answers seq={envelope.get('seq')} for {envelope.get('requested_by')}"
    return None


def _submit_control(dest, payload, ttl=None):
    '''_dapps_submit for a control message, recording the attempt either way. Re-raises on failure
    so each caller keeps its own logging and retry behaviour.'''
    try:
        dapps_id = _dapps_submit(dest, payload, ttl=ttl)
    except Exception as e:
        _record("out", "sync", payload.get("op"), "failed", peer=dest, origin=payload.get("origin"),
                seq=payload.get("seq") if payload.get("op") == "ack" else None,
                detail=f"{_describe_control(payload)} - {e}", event=payload)
        raise
    _record("out", "sync", payload.get("op"), "sent", peer=dest, origin=payload.get("origin"),
            seq=payload.get("seq") if payload.get("op") == "ack" else None,
            detail=_describe_control(payload), dapps_id=dapps_id, event=payload)
    return dapps_id


def _is_configured_peer(callsign):
    return isinstance(callsign, str) and callsign.upper() in _DAPPS_TO_ORIGIN


def _is_configured_peer_origin(origin):
    return isinstance(origin, str) and origin.upper() in _ORIGIN_TO_DAPPS


def _handle_inbound(msg):
    dapps_id = msg["id"]
    envelope = _decode_payload(msg["payload"])
    op = envelope.get("op")

    # Anyone who can reach this node's DAPPS can address wps-repl@<our callsign>, and DAPPS
    # doesn't authenticate senders beyond the callsign it stamps on the message. So only
    # accept events from configured peers: both the DAPPS-stamped source callsign (when
    # present) and the identity claimed inside the envelope must be a peer. Anything else is
    # logged and dropped (acked, so it doesn't sit in the queue and get re-polled forever).
    # `by` and `requested_by` carry the sender's DAPPS callsign; `origin` carries its origin callsign.
    claim_key = {"ack": "by", "sync.request": "requested_by", "seq_at.request": "requested_by"}.get(op, "origin")
    claimed = envelope.get(claim_key)
    source = msg.get("sourceCallsign")
    claimed_ok = _is_configured_peer(claimed) if claim_key != "origin" else _is_configured_peer_origin(claimed)
    if not claimed_ok or (source is not None and not _is_configured_peer(source)):
        wps_logger("REPLICATION INBOX", ORIGIN, f"Rejecting message {dapps_id} from unconfigured peer (source={source}, claimed={claimed}, op={op})", "ERROR")
        _record("in", "sync" if op in _CONTROL_OPS else "data", op, "rejected", peer=source, origin=envelope.get("origin"),
                seq=envelope.get("seq") if op not in _CONTROL_OPS else None,
                detail=f"Unconfigured peer (source={source}, claimed {claim_key}={claimed})", dapps_id=dapps_id, event=envelope)
        _dapps_ack(dapps_id)
        return

    # Everything below is from a configured peer; record it against that peer's DAPPS callsign.
    peer = source or (claimed if claim_key != "origin" else _dapps_for_origin(claimed))
    _last_heard_from[peer.upper()] = time.monotonic()

    if op in _CONTROL_OPS:
        _record("in", "sync", op, "received", peer=peer, origin=envelope.get("origin"),
                seq=envelope.get("seq") if op == "ack" else None, detail=_describe_control(envelope),
                dapps_id=dapps_id, event=envelope)

    if op == "ack":
        _handle_app_ack(envelope)
        _dapps_ack(dapps_id)
        return

    if op == "digest":
        _handle_digest(envelope)
        _dapps_ack(dapps_id)
        return

    if op == "sync.request":
        _handle_sync_request(envelope)
        _dapps_ack(dapps_id)
        return

    if op == "seq_at.request":
        _handle_seq_at_request(envelope)
        _dapps_ack(dapps_id)
        return

    if op == "seq_at.response":
        _handle_seq_at_response(envelope)
        _dapps_ack(dapps_id)
        return

    # A normal replicated data event (post.insert, post.edit, msg.insert, ...) or a batch of
    # them, possibly carrying acks the sender held for us. Those are handled first, whatever
    # becomes of the events. The DAPPS message is acked only once every event in it is dealt
    # with: if one fails it is redelivered whole, and the ones already applied drop out as duplicates.
    _handle_carried_acks(envelope, peer, dapps_id)

    if op == "batch":
        _handle_batch(envelope, peer, dapps_id)
    else:
        _handle_data_event(envelope, peer, dapps_id)
    _dapps_ack(dapps_id)


def _handle_batch(envelope, peer, dapps_id):
    events = envelope.get("events")
    if not isinstance(events, list) or not events:
        _record("in", "data", "batch", "rejected", peer=peer, origin=envelope.get("origin"),
                detail="Batch with no events list", dapps_id=dapps_id, event=envelope)
        return
    seqs = [e.get("seq") for e in events if isinstance(e, dict) and isinstance(e.get("seq"), int)]
    note = f"In batch of {len(events)} ({min(seqs)}-{max(seqs)})" if seqs else f"In batch of {len(events)}"
    for event in events:
        # The batch's own origin was checked against the configured peers in _handle_inbound;
        # every event in it must belong to that same stream and be a data event.
        if (not isinstance(event, dict) or event.get("origin") != envelope.get("origin")
                or not isinstance(event.get("seq"), int) or event.get("op") in _CONTROL_OPS or event.get("op") == "batch"):
            _record("in", "data", event.get("op") if isinstance(event, dict) else None, "rejected", peer=peer,
                    origin=envelope.get("origin"), seq=event.get("seq") if isinstance(event, dict) else None,
                    detail=f"Not a data event of {envelope.get('origin')} - {note}", dapps_id=dapps_id,
                    event=event if isinstance(event, dict) else None)
            continue
        _handle_data_event(event, peer, dapps_id, note)


def _handle_data_event(envelope, peer, dapps_id, batch_note=None):
    '''Applies, buffers or drops one data event. The caller acks the DAPPS message it came in.'''
    op = envelope.get("op")
    origin = envelope["origin"]
    seq = envelope["seq"]

    if origin == ORIGIN:
        # Our own event came back somehow (e.g. a peer relayed it) - nothing to apply.
        _record("in", "data", op, "ignored", peer=peer, origin=origin, seq=seq, detail="Our own event echoed back",
                dapps_id=dapps_id, event=envelope)
        return

    # Data from a peer means someone there is active - keep polling fast for their follow-ups.
    # Control messages (acks, digests) deliberately don't count, or digests alone would keep it fast.
    _extend_fast_inbox()

    conn = db.get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM replication_bootstrap_pending WHERE origin = ?", (origin,))
    if cur.fetchone() is not None:
        # Still waiting to learn where to start this origin's stream (see
        # _start_bootstrap_if_configured). Buffer verbatim rather than treating this as a gap
        # from seq 0 - that would trigger a sync.request for the entire history, exactly what
        # bootstrapFromTs exists to avoid. _handle_seq_at_response drains this once resolved.
        wps_logger("REPLICATION INBOX", ORIGIN, f"{origin}/{seq} arrived while bootstrap is still pending - buffering")
        cur.execute(
            "INSERT OR REPLACE INTO replication_pending (origin, seq, event) VALUES (?, ?, ?)",
            (origin, seq, json.dumps(envelope, separators=(',', ':')))
        )
        conn.commit()
        _record("in", "data", op, "buffered", peer=peer, origin=origin, seq=seq,
                detail="Bootstrap for this origin still pending - held until seq_at.response arrives", dapps_id=dapps_id, event=envelope)
        return

    cur.execute("SELECT last_applied_seq FROM replication_origin_cursor WHERE origin = ?", (origin,))
    row = cur.fetchone()
    last_applied = row[0] if row else 0

    if seq <= last_applied:
        wps_logger("REPLICATION INBOX", ORIGIN, f"Duplicate delivery of {origin}/{seq}, already applied - ack and drop")
        _record("in", "data", op, "duplicate", peer=peer, origin=origin, seq=seq,
                detail=f"Already applied up to {last_applied} - dropped", dapps_id=dapps_id, event=envelope)
        return

    if seq > last_applied + 1:
        wps_logger("REPLICATION INBOX", ORIGIN, f"Gap from {origin}: have {last_applied}, got {seq} - buffering and requesting resend")
        cur.execute(
            "INSERT OR REPLACE INTO replication_pending (origin, seq, event) VALUES (?, ?, ?)",
            (origin, seq, json.dumps(envelope, separators=(',', ':')))
        )
        conn.commit()
        _record("in", "data", op, "buffered", peer=peer, origin=origin, seq=seq,
                detail=f"Gap: have {last_applied}, requesting {last_applied + 1}-{seq - 1}", dapps_id=dapps_id, event=envelope)
        # DAPPS delivered it fine - the gap is an application-level concern, so the message is still acked
        _request_sync(origin, last_applied + 1, seq - 1)
        return

    outcome = _apply_one(conn, cur, origin, seq, envelope)
    _record("in", "data", op, outcome, peer=peer, origin=origin, seq=seq, detail=batch_note, dapps_id=dapps_id, event=envelope)
    _drain_pending(conn, cur, origin)
    _queue_app_ack(origin, seq)


# --- Application-level acks: retire outbox rows once every peer has applied them ----------

_pending_acks = {}  # origin -> [highest applied seq not yet acked, time.monotonic() the first was held]
_acked_up_to = {}   # origin -> highest seq an ack has been submitted for, since this process started
_pending_acks_lock = threading.Lock()


def _queue_app_ack(origin, seq):
    '''
    Holds an ack for origin/seq rather than sending it at once. _flush_due_acks sends a single
    ack for the highest held seq once the first has waited ACK_DELAY_SECONDS. Nothing waits on
    acks except outbox retirement, so the delay costs only a slightly later cleanup.
    '''
    with _pending_acks_lock:
        held = _pending_acks.get(origin)
        if held:
            held[0] = max(held[0], seq)
        else:
            _pending_acks[origin] = [seq, time.monotonic()]


def _flush_due_acks():
    now = time.monotonic()
    with _pending_acks_lock:
        due = {origin: held[0] for origin, held in _pending_acks.items() if now - held[1] >= ACK_DELAY_SECONDS}
        for origin in due:
            del _pending_acks[origin]
    for origin, seq in due.items():
        try:
            _submit_control(_dapps_for_origin(origin), {"op": "ack", "origin": origin, "seq": seq, "by": DAPPS_CALLSIGN})
            _acked_up_to[origin] = max(_acked_up_to.get(origin, 0), seq)
        except Exception as e:
            wps_logger("REPLICATION INBOX", ORIGIN, f"Failed to send app-level ack for {origin}/{seq}, will retry: {e}", "ERROR")
            _queue_app_ack(origin, seq)


def _take_held_acks(peer):
    '''
    Removes and returns the held acks addressed to peer ({origin: [seq, held_since]}), for the
    outbox pump to carry on a data event it is about to submit there. _restore_held_acks puts
    them back if that submit fails.
    '''
    with _pending_acks_lock:
        taken = {origin: held for origin, held in _pending_acks.items() if _dapps_for_origin(origin).upper() == peer.upper()}
        for origin in taken:
            del _pending_acks[origin]
    return taken


def _restore_held_acks(taken):
    with _pending_acks_lock:
        for origin, (seq, held_since) in taken.items():
            held = _pending_acks.get(origin)
            if held:
                held[0] = max(held[0], seq)
                held[1] = min(held[1], held_since)
            else:
                _pending_acks[origin] = [seq, held_since]


_PEER_SPELLING = {p.upper(): p for p in PEERS}  # replication_peer_ack is keyed by the configured spelling


def _handle_carried_acks(envelope, peer, dapps_id):
    '''
    Handles the `acks` a peer attached to a data event ({origin: seq}), exactly as if each had
    arrived as its own ack. `by` is the peer DAPPS delivered it from, already checked in
    _handle_inbound, so nothing in the envelope has to be trusted for it.
    '''
    acks = envelope.get("acks")
    if not isinstance(acks, dict):
        return
    by = _PEER_SPELLING.get(peer.upper(), peer)
    for origin, seq in acks.items():
        if not isinstance(seq, int):
            continue
        ack = {"op": "ack", "origin": origin, "seq": seq, "by": by}
        _record("in", "sync", "ack", "received", peer=peer, origin=origin, seq=seq,
                detail=f"{_describe_control(ack)} - carried on {_carrier_label(envelope)}",
                dapps_id=dapps_id, event=ack)
        _handle_app_ack(ack)


def _carrier_label(envelope):
    if envelope.get("op") == "batch":
        seqs = [e.get("seq") for e in envelope.get("events") or [] if isinstance(e, dict) and isinstance(e.get("seq"), int)]
        if seqs:
            return _events_label(envelope.get("origin"), min(seqs), max(seqs))
    return f"{envelope.get('origin')}/{envelope.get('seq')}"


def _handle_app_ack(envelope):
    if envelope.get("origin") != ORIGIN:
        return  # an ack for someone else's stream, not ours to record
    peer = envelope["by"]
    seq = envelope["seq"]
    conn = db.get_db_connection()
    cur = conn.cursor()
    # An ack also implies the peer already holds everything up to seq (e.g. via a
    # sync.request resend), so advance submitted_seq too rather than re-submitting it.
    cur.execute(
        "INSERT INTO replication_peer_ack (peer, peer_acked_seq, submitted_seq) VALUES (?, ?, ?) "
        "ON CONFLICT(peer) DO UPDATE SET "
        "peer_acked_seq = MAX(peer_acked_seq, excluded.peer_acked_seq), "
        "submitted_seq = MAX(submitted_seq, excluded.submitted_seq)",
        (peer, seq, seq)
    )
    conn.commit()
    _retire_acked_outbox_rows(cur, conn)


# --- Reconciliation: periodic digest + on-demand sync.request/response --------------------

def _reconcile_loop():
    while True:
        try:
            _retry_bootstrap_pending()
            _send_digest()
            _log_backlog()
            _prune_activity()
        except Exception as e:
            wps_logger("REPLICATION RECONCILE", ORIGIN, f"Tick error: {e}", "ERROR")
        time.sleep(RECONCILE_INTERVAL_SECONDS)


def _send_digest():
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT next_seq - 1 FROM replication_self WHERE id = 1")
    my_latest = cur.fetchone()[0]

    for peer in PEERS:
        cur.execute("SELECT peer_acked_seq FROM replication_peer_ack WHERE peer = ?", (peer,))
        row = cur.fetchone()
        if _digest_redundant(peer, my_latest, row[0] if row else 0):
            wps_logger("REPLICATION RECONCILE", ORIGIN, f"Skipping digest to {peer} - recent traffic already shows where we are")
            continue
        try:
            # Short TTL: only the newest digest matters, so one that can't be delivered within a
            # couple of intervals should expire in DAPPS rather than queue behind a down peer.
            _submit_control(peer, {"op": "digest", "origin": ORIGIN, "latest_seq": my_latest}, ttl=RECONCILE_INTERVAL_SECONDS * 2)
        except Exception as e:
            wps_logger("REPLICATION RECONCILE", ORIGIN, f"Failed to send digest to {peer}: {e}", "ERROR")


_last_heard_from = {}    # peer DAPPS callsign (upper) -> time.monotonic() anything was last received from it
_last_data_sent_to = {}  # peer DAPPS callsign (upper) -> time.monotonic() a data event was last submitted to it


def _digest_redundant(peer, my_latest, peer_acked_seq):
    '''
    A digest exists so a peer can spot events it missed. It adds nothing while traffic is
    flowing: either the peer has acked everything we have, or events are in flight to it now
    and will show any gap themselves. Requiring that we heard from the peer this interval keeps
    a digest going out whenever the link is quiet or the peer may be down, so the dashboard's
    "silent" check and TTL-expiry recovery are unaffected. A skipped tick delays gap detection
    by at most one interval.
    '''
    now = time.monotonic()
    heard = _last_heard_from.get(peer.upper())
    if heard is None or now - heard > RECONCILE_INTERVAL_SECONDS:
        return False
    if peer_acked_seq >= my_latest:
        return True
    sent = _last_data_sent_to.get(peer.upper())
    return sent is not None and now - sent <= RECONCILE_INTERVAL_SECONDS


def _handle_digest(envelope):
    origin = envelope["origin"]
    latest_seq = envelope["latest_seq"]
    if origin == ORIGIN:
        return

    conn = db.get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM replication_bootstrap_pending WHERE origin = ?", (origin,))
    if cur.fetchone() is not None:
        # Cursor for this origin isn't seeded yet - a digest compared against the default of 0
        # would ask for the whole history. _retry_bootstrap_pending is already chasing the
        # seq_at.response; nothing to do here but wait for it.
        return

    cur.execute("SELECT last_applied_seq FROM replication_origin_cursor WHERE origin = ?", (origin,))
    row = cur.fetchone()
    last_applied = row[0] if row else 0

    if latest_seq > last_applied:
        wps_logger("REPLICATION RECONCILE", ORIGIN, f"Digest shows {origin} is at {latest_seq}, we're at {last_applied} - requesting sync")
        _request_sync(origin, last_applied + 1, latest_seq)
    elif latest_seq == last_applied and last_applied > 0 and _acked_up_to.get(origin, 0) < last_applied:
        # Level, but no ack for the latest has gone out from this process - one held when WPS
        # last stopped would be lost with it, leaving the origin's outbox row un-retired.
        _queue_app_ack(origin, last_applied)
    elif latest_seq < last_applied:
        wps_logger("REPLICATION RECONCILE", ORIGIN, f"Digest shows {origin} at seq {latest_seq} but we have already applied up to {last_applied} - "
                   f"{origin} looks restored or rebuilt; its new events will be dropped as duplicates until this is resolved (see docs/replication/REPLICATION.md)", "ERROR")


# origin -> (from_seq, to_seq, monotonic time) of the last sync.request sent. Live events keep
# arriving while a gap is being filled, and each one would otherwise fire its own overlapping
# request (2-43, 2-44, 2-45, ...), making the origin re-send the same range over and over.
_last_sync_request = {}
_last_sync_request_lock = threading.Lock()


def _request_sync(origin, from_seq, to_seq):
    now = time.monotonic()
    with _last_sync_request_lock:
        prev = _last_sync_request.get(origin)
        if prev and prev[0] <= from_seq and now - prev[2] < RECONCILE_INTERVAL_SECONDS:
            # Within the window of an earlier request that starts at or before this one: only
            # ask for whatever extends beyond what was already requested.
            from_seq = max(from_seq, prev[1] + 1)
            if from_seq > to_seq:
                return
            _last_sync_request[origin] = (prev[0], to_seq, prev[2])
        else:
            _last_sync_request[origin] = (from_seq, to_seq, now)
    try:
        # Short TTL: if origin is unreachable, the next reconcile tick will send an updated
        # sync.request anyway, so a stale one shouldn't linger in the DAPPS queue.
        _submit_control(_dapps_for_origin(origin), {"op": "sync.request", "origin": origin, "from_seq": from_seq, "to_seq": to_seq, "requested_by": DAPPS_CALLSIGN}, ttl=RECONCILE_INTERVAL_SECONDS * 2)
    except Exception as e:
        wps_logger("REPLICATION RECONCILE", ORIGIN, f"Failed to request sync from {origin} for {from_seq}-{to_seq}: {e}", "ERROR")


def _handle_sync_request(envelope):
    origin = envelope["origin"]
    if origin != ORIGIN:
        # A sync.request only ever makes sense addressed to the actual origin of the log
        # range being asked for - in a full mesh that's always us when we're the recipient.
        return

    from_seq = envelope["from_seq"]
    to_seq = envelope["to_seq"]
    requester = envelope["requested_by"]

    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT seq, event FROM replication_log WHERE origin = ? AND seq BETWEEN ? AND ? ORDER BY seq ASC",
        (ORIGIN, from_seq, to_seq)
    )
    rows = cur.fetchall()
    wps_logger("REPLICATION RECONCILE", ORIGIN, f"Re-sending {len(rows)} event(s) {from_seq}-{to_seq} to {requester}")

    # The requester will keep asking until the range arrives, so make it visible when part of
    # it isn't in our log - it can never be served and needs an operator.
    missing = sorted(set(range(from_seq, to_seq + 1)) - {seq for seq, _ in rows})
    if missing:
        shown = ", ".join(str(s) for s in missing[:20]) + (f" ... ({len(missing)} total)" if len(missing) > 20 else "")
        wps_logger("REPLICATION RECONCILE", ORIGIN, f"sync.request {from_seq}-{to_seq} from {requester}: not in replication_log as {ORIGIN}: {shown}", "ERROR")
        _record("out", "sync", "sync.request", "failed", peer=requester, origin=ORIGIN,
                detail=f"Cannot serve {len(missing)} of {to_seq - from_seq + 1} requested seq(s), not in replication_log as {ORIGIN}: {shown}")

    for chunk in _batches([(seq, json.loads(event_json)) for seq, event_json in rows]):
        envelopes = [envelope for _, envelope in chunk]
        note = _batch_note(chunk)
        try:
            dapps_id = _dapps_submit(
                requester, _events_payload(envelopes),
                stream_id=_stream_id_for(ORIGIN, envelopes[0]["epoch"]),
                gap_timeout_seconds=0, ttl=STREAM_TTL_SECONDS
            )
        except Exception as e:
            label = _events_label(ORIGIN, chunk[0][0], chunk[-1][0])
            wps_logger("REPLICATION RECONCILE", ORIGIN, f"Failed to re-send {label} to {requester}: {e}", "ERROR")
            for seq, envelope in chunk:
                _record("out", "data", envelope.get("op"), "failed", peer=requester, origin=ORIGIN, seq=seq,
                        detail=f"Re-send for sync.request {from_seq}-{to_seq} failed: {e}")
            continue
        for seq, envelope in chunk:
            detail = f"Re-sent for sync.request {from_seq}-{to_seq}" + (f" - {note}" if note else "")
            _record("out", "data", envelope.get("op"), "resent", peer=requester, origin=ORIGIN, seq=seq,
                    detail=detail, dapps_id=dapps_id)


def _fill_gap_to_pending(origin):
    '''
    Called right after a bootstrap resolves and _drain_pending has consumed whatever was
    immediately contiguous. If a peer's data events started arriving (and got buffered) while
    the seq_at.response was still in flight, there is usually still a gap between the newly
    seeded cursor and the lowest buffered seq - request exactly that range, the same way an
    ordinary out-of-order arrival does in _handle_inbound.
    '''
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT last_applied_seq FROM replication_origin_cursor WHERE origin = ?", (origin,))
    row = cur.fetchone()
    last_applied = row[0] if row else 0
    cur.execute("SELECT MIN(seq) FROM replication_pending WHERE origin = ? AND seq > ?", (origin, last_applied))
    row = cur.fetchone()
    lowest_pending = row[0] if row else None
    if lowest_pending is not None and lowest_pending > last_applied + 1:
        wps_logger("REPLICATION BOOTSTRAP", ORIGIN, f"Requesting {origin}/{last_applied + 1}-{lowest_pending - 1} to bridge to buffered events received during bootstrap")
        _request_sync(origin, last_applied + 1, lowest_pending - 1)


# --- Timestamp bootstrap: seq_at.request/response, for a fresh instance joining mid-history ---

def _handle_seq_at_request(envelope):
    '''
    A peer (usually a brand-new instance configured with replication.bootstrapFromTs) is
    asking: "if I want your stream starting from timestamp ts, what last_applied_seq should I
    seed for you?" Answered from our own replication_log, which holds only our own origin's
    events - exactly what's needed to answer for ourselves.
    '''
    origin = envelope["origin"]
    if origin != ORIGIN:
        return  # only the actual owner of the requested log can answer for it

    requester = envelope["requested_by"]
    target_ts = envelope["ts"]

    conn = db.get_db_connection()
    cur = conn.cursor()
    # replication_log.ts is stored in the native precision of the thing that changed - seconds
    # for msg.* rows (lts/edts/ets), milliseconds for everything else (dts/edts/ets, or the
    # capture-time default for user.update). bootstrapFromTs is documented as epoch-ms, so
    # msg.* rows need normalising up to milliseconds before comparing.
    cur.execute(
        "SELECT MIN(seq) FROM replication_log WHERE origin = ? AND "
        "(CASE WHEN op LIKE 'msg.%' THEN ts * 1000 ELSE ts END) >= ?",
        (ORIGIN, target_ts)
    )
    row = cur.fetchone()
    first_seq_at_or_after = row[0] if row and row[0] is not None else None

    if first_seq_at_or_after is None:
        # Nothing in our log is that new yet - the requester is fully caught up as of now.
        cur.execute("SELECT next_seq - 1 FROM replication_self WHERE id = 1")
        seed_seq = cur.fetchone()[0]
    else:
        seed_seq = first_seq_at_or_after - 1

    wps_logger("REPLICATION BOOTSTRAP", ORIGIN, f"seq_at.request from {requester} for ts={target_ts}: answering seq={seed_seq}")
    try:
        _submit_control(requester, {"op": "seq_at.response", "origin": ORIGIN, "seq": seed_seq, "requested_by": requester})
    except Exception as e:
        wps_logger("REPLICATION BOOTSTRAP", ORIGIN, f"Failed to send seq_at.response to {requester}: {e}", "ERROR")


def _handle_seq_at_response(envelope):
    origin = envelope["origin"]
    if envelope.get("requested_by") != DAPPS_CALLSIGN:
        return  # a response to someone else's request

    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM replication_bootstrap_pending WHERE origin = ?", (origin,))
    if cur.fetchone() is None:
        return  # already resolved (e.g. a duplicate response) or not something we asked for

    seed_seq = envelope["seq"]
    cur.execute(
        "INSERT INTO replication_origin_cursor (origin, last_applied_seq) VALUES (?, ?) "
        "ON CONFLICT(origin) DO UPDATE SET last_applied_seq = excluded.last_applied_seq",
        (origin, seed_seq)
    )
    cur.execute("DELETE FROM replication_bootstrap_pending WHERE origin = ?", (origin,))
    conn.commit()
    wps_logger("REPLICATION BOOTSTRAP", ORIGIN, f"Seeded cursor for {origin} at seq={seed_seq}")

    _drain_pending(conn, cur, origin)
    _fill_gap_to_pending(origin)


def _start_bootstrap_if_configured(cur, conn):
    '''
    Called once from start(). If replication.bootstrapFromTs is set and this instance has never
    resolved a starting cursor for any peer, mark every peer pending and let
    _retry_bootstrap_pending (called immediately below, then every reconcile tick) send the
    seq_at.request. Safe to call on every restart: once replication_bootstrap_pending is empty
    and replication_origin_cursor has rows, bootstrapFromTs is inert and this is a no-op, so
    it's fine to leave the setting in env.json indefinitely.
    '''
    if not BOOTSTRAP_FROM_TS:
        return

    cur.execute("SELECT 1 FROM replication_bootstrap_pending LIMIT 1")
    restart_mid_bootstrap = cur.fetchone() is not None
    if restart_mid_bootstrap:
        return  # rows already there from a prior start; _retry_bootstrap_pending will chase them

    cur.execute("SELECT 1 FROM replication_origin_cursor LIMIT 1")
    if cur.fetchone() is not None:
        return  # already bootstrapped (or caught up organically) in an earlier run

    for peer_origin, _ in _PEER_PAIRS:
        cur.execute("INSERT OR IGNORE INTO replication_bootstrap_pending (origin, requested_at) VALUES (?, ?)", (peer_origin, _now_ms()))
    conn.commit()
    wps_logger("REPLICATION BOOTSTRAP", ORIGIN, f"Bootstrapping from ts={BOOTSTRAP_FROM_TS}: requesting seq_at from {PEERS}")


def _retry_bootstrap_pending():
    '''
    Runs every reconcile tick (and once right after _start_bootstrap_if_configured). Re-sends
    seq_at.request for any peer still in replication_bootstrap_pending - covers the request
    being sent before local DAPPS was reachable, or the response being lost. Once
    _handle_seq_at_response resolves a peer it deletes the row, so this naturally stops
    retrying that peer.
    '''
    if not BOOTSTRAP_FROM_TS:
        return
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT origin FROM replication_bootstrap_pending")
    pending = [row[0] for row in cur.fetchall()]
    for peer in pending:
        try:
            _submit_control(_dapps_for_origin(peer), {"op": "seq_at.request", "origin": peer, "requested_by": DAPPS_CALLSIGN, "ts": BOOTSTRAP_FROM_TS}, ttl=RECONCILE_INTERVAL_SECONDS * 2)
        except Exception as e:
            wps_logger("REPLICATION BOOTSTRAP", ORIGIN, f"seq_at.request to {peer} failed, will retry: {e}", "ERROR")


def _log_backlog():
    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM replication_outbox")
    outbox_backlog = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM replication_pending")
    pending_gaps = cur.fetchone()[0]
    if outbox_backlog or pending_gaps:
        wps_logger("REPLICATION RECONCILE", ORIGIN, f"Backlog: {outbox_backlog} outbox row(s) awaiting peer ack, {pending_gaps} gap(s) buffered pending resend")


# --- Startup -------------------------------------------------------------------------------

def _merge_origin_aliases(cur, conn):
    '''
    Called once from start(). The envelope `origin` used to be the DAPPS callsign, and is now
    originCallsign. Rows written under the old name are otherwise stranded: our own log rows
    under DAPPS_CALLSIGN can't be found by _handle_sync_request, and a peer's cursor/buffer
    under its DAPPS callsign doesn't count towards its origin, so each side keeps asking for a
    range the other can never serve. seq comes from a single per-node counter, so rows under
    the two names never collide and can simply be folded together. Idempotent - a no-op once
    nothing is left under an old name.
    '''
    # Our own log: the source for re-sends.
    if DAPPS_CALLSIGN and DAPPS_CALLSIGN != ORIGIN:
        cur.execute(
            "UPDATE OR IGNORE replication_log SET origin = ?, event = json_set(event, '$.origin', ?) WHERE origin = ?",
            (ORIGIN, ORIGIN, DAPPS_CALLSIGN)
        )
        if cur.rowcount:
            wps_logger("REPLICATION", ORIGIN, f"Moved {cur.rowcount} own log row(s) from old origin {DAPPS_CALLSIGN} to {ORIGIN}")

    # Each peer's cursor and out-of-order buffer.
    merged = []
    for peer_origin, peer_dapps in _PEER_PAIRS:
        if peer_origin == peer_dapps:
            continue
        cur.execute("SELECT last_applied_seq FROM replication_origin_cursor WHERE origin = ?", (peer_dapps,))
        old = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM replication_pending WHERE origin = ?", (peer_dapps,))
        old_pending = cur.fetchone()[0]
        if old is None and not old_pending:
            continue
        if old is not None:
            cur.execute(
                "INSERT INTO replication_origin_cursor (origin, last_applied_seq) VALUES (?, ?) "
                "ON CONFLICT(origin) DO UPDATE SET last_applied_seq = MAX(last_applied_seq, excluded.last_applied_seq)",
                (peer_origin, old[0])
            )
            cur.execute("DELETE FROM replication_origin_cursor WHERE origin = ?", (peer_dapps,))
        cur.execute(
            "INSERT OR IGNORE INTO replication_pending (origin, seq, event) "
            "SELECT ?, seq, json_set(event, '$.origin', ?) FROM replication_pending WHERE origin = ?",
            (peer_origin, peer_origin, peer_dapps)
        )
        cur.execute("DELETE FROM replication_pending WHERE origin = ?", (peer_dapps,))
        # Anything buffered at or below the merged cursor is already applied.
        cur.execute(
            "DELETE FROM replication_pending WHERE origin = ? AND seq <= "
            "(SELECT last_applied_seq FROM replication_origin_cursor WHERE origin = ?)",
            (peer_origin, peer_origin)
        )
        cur.execute("DELETE FROM replication_bootstrap_pending WHERE origin = ?", (peer_dapps,))
        merged.append(peer_origin)
        wps_logger("REPLICATION", ORIGIN, f"Merged cursor/buffer for old origin {peer_dapps} into {peer_origin} "
                   f"(old cursor {old[0] if old else None}, {old_pending} buffered)")
    conn.commit()

    # The merged cursor may now reach the buffer; apply what's contiguous and ask for the rest.
    for peer_origin in merged:
        try:
            _drain_pending(conn, cur, peer_origin)
            _fill_gap_to_pending(peer_origin)
        except Exception as e:
            wps_logger("REPLICATION", ORIGIN, f"Draining merged buffer for {peer_origin} failed, the inbox pump will retry via digest: {e}", "ERROR")


def start():
    '''
    Called once from wps.py's startup_and_listen(), after db.dbInit() has created the
    replication tables. Starts the three background pumps as daemon threads. No-ops (loudly)
    if replication isn't configured, so it's always safe to call.
    '''
    if not ENABLED:
        print(f"{timestamp()} Replication disabled (set replication.enabled=true in env.json to turn on)")
        return

    if not DAPPS_CALLSIGN or not PEERS:
        print(f"{timestamp()} Replication enabled but replication.dappsCallsign/peers are not configured in env.json - not starting")
        return

    conn = db.get_db_connection()
    cur = conn.cursor()
    for peer in PEERS:
        cur.execute("INSERT OR IGNORE INTO replication_peer_ack (peer, peer_acked_seq) VALUES (?, 0)", (peer,))
    conn.commit()

    _merge_origin_aliases(cur, conn)
    _start_bootstrap_if_configured(cur, conn)
    _retry_bootstrap_pending()

    threading.Thread(target=_outbox_pump_loop, daemon=True, name="replication_outbox_pump").start()
    threading.Thread(target=_inbox_pump_loop, daemon=True, name="replication_inbox_pump").start()
    threading.Thread(target=_reconcile_loop, daemon=True, name="replication_reconcile_pump").start()

    print(f"{timestamp()} Replication started: origin={ORIGIN} app={APP_SLUG} peers={PEERS} dapps={DAPPS_REST_URL}")
