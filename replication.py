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
RECONCILE_INTERVAL_SECONDS = REPLICATION_CONFIG.get('reconcileIntervalSeconds', 300)
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


# --- DAPPS REST client --------------------------------------------------------------------

def _dapps_submit(dest_callsign, payload_dict, stream_id=None, gap_timeout_seconds=None, ttl=None):
    body = {
        "app": APP_SLUG,
        "destCallsign": dest_callsign,
        "payload": base64.b64encode(json.dumps(payload_dict, separators=(',', ':')).encode()).decode(),
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


# --- Outbox pump: replication_log/outbox (captured by db.py) -> DAPPS ---------------------

def _outbox_pump_loop():
    while True:
        try:
            _outbox_pump_tick()
        except Exception as e:
            wps_logger("REPLICATION OUTBOX", ORIGIN, f"Tick error: {e}", "ERROR")
        time.sleep(OUTBOX_POLL_SECONDS)


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
            "WHERE o.seq > ? ORDER BY o.seq ASC LIMIT 50",
            (ORIGIN, submitted_seq)
        )
        for seq, event_json, dapps_ids_json in cur.fetchall():
            envelope = json.loads(event_json)
            try:
                dapps_id = _dapps_submit(peer, envelope, stream_id=_stream_id_for(ORIGIN, envelope["epoch"]), gap_timeout_seconds=0, ttl=STREAM_TTL_SECONDS)
            except Exception as e:
                wps_logger("REPLICATION OUTBOX", ORIGIN, f"Submit seq={seq} to {peer} failed, will retry: {e}", "ERROR")
                if _last_outbox_failure.get(peer) != seq:
                    _last_outbox_failure[peer] = seq
                    _record("out", "data", envelope.get("op"), "failed", peer=peer, origin=ORIGIN, seq=seq, detail=f"Submit to DAPPS failed, retrying every tick: {e}")
                break

            dapps_ids = json.loads(dapps_ids_json) if dapps_ids_json else {}
            dapps_ids[peer] = dapps_id
            cur.execute("UPDATE replication_peer_ack SET submitted_seq = ? WHERE peer = ?", (seq, peer))
            cur.execute(
                "UPDATE replication_outbox SET dapps_ids = ?, submitted_at = ? WHERE seq = ?",
                (json.dumps(dapps_ids), _now_ms() if all(p in dapps_ids for p in PEERS) else None, seq)
            )
            conn.commit()
            _last_outbox_failure.pop(peer, None)
            _record("out", "data", envelope.get("op"), "sent", peer=peer, origin=ORIGIN, seq=seq, dapps_id=dapps_id)


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
    key = envelope["key"]
    data = envelope["data"]

    if op == "post.insert":
        post = dict(data)  # copy: the envelope itself is recorded as received, without the local `o`
        post["o"] = envelope["origin"]
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
        _send_app_ack(origin, seq)


# --- Inbox pump: DAPPS -> apply / control messages -----------------------------------------

def _inbox_pump_loop():
    while True:
        try:
            _inbox_pump_tick()
        except Exception as e:
            wps_logger("REPLICATION INBOX", ORIGIN, f"Tick error: {e}", "ERROR")
        time.sleep(INBOX_POLL_SECONDS)


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
        envelope = json.loads(base64.b64decode(msg["payload"]))
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
    envelope = json.loads(base64.b64decode(msg["payload"]))
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

    # A normal replicated data event (post.insert, post.edit, msg.insert, ...)
    origin = envelope["origin"]
    seq = envelope["seq"]

    if origin == ORIGIN:
        # Our own event came back somehow (e.g. a peer relayed it) - nothing to apply.
        _record("in", "data", op, "ignored", peer=peer, origin=origin, seq=seq, detail="Our own event echoed back",
                dapps_id=dapps_id, event=envelope)
        _dapps_ack(dapps_id)
        return

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
        _dapps_ack(dapps_id)
        return

    cur.execute("SELECT last_applied_seq FROM replication_origin_cursor WHERE origin = ?", (origin,))
    row = cur.fetchone()
    last_applied = row[0] if row else 0

    if seq <= last_applied:
        wps_logger("REPLICATION INBOX", ORIGIN, f"Duplicate delivery of {origin}/{seq}, already applied - ack and drop")
        _record("in", "data", op, "duplicate", peer=peer, origin=origin, seq=seq,
                detail=f"Already applied up to {last_applied} - dropped", dapps_id=dapps_id, event=envelope)
        _dapps_ack(dapps_id)
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
        _dapps_ack(dapps_id)  # DAPPS delivered it fine - the gap is an application-level concern
        _request_sync(origin, last_applied + 1, seq - 1)
        return

    outcome = _apply_one(conn, cur, origin, seq, envelope)
    _record("in", "data", op, outcome, peer=peer, origin=origin, seq=seq, dapps_id=dapps_id, event=envelope)
    _drain_pending(conn, cur, origin)
    _dapps_ack(dapps_id)
    _send_app_ack(origin, seq)


# --- Application-level acks: retire outbox rows once every peer has applied them ----------

def _send_app_ack(origin, seq):
    try:
        _submit_control(_dapps_for_origin(origin), {"op": "ack", "origin": origin, "seq": seq, "by": DAPPS_CALLSIGN})
    except Exception as e:
        wps_logger("REPLICATION INBOX", ORIGIN, f"Failed to send app-level ack for {origin}/{seq}: {e}", "ERROR")


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
        try:
            # Short TTL: only the newest digest matters, so one that can't be delivered within a
            # couple of intervals should expire in DAPPS rather than queue behind a down peer.
            _submit_control(peer, {"op": "digest", "origin": ORIGIN, "latest_seq": my_latest}, ttl=RECONCILE_INTERVAL_SECONDS * 2)
        except Exception as e:
            wps_logger("REPLICATION RECONCILE", ORIGIN, f"Failed to send digest to {peer}: {e}", "ERROR")


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

    for seq, event_json in rows:
        envelope_to_resend = json.loads(event_json)
        try:
            dapps_id = _dapps_submit(
                requester, envelope_to_resend,
                stream_id=_stream_id_for(ORIGIN, envelope_to_resend["epoch"]),
                gap_timeout_seconds=0, ttl=STREAM_TTL_SECONDS
            )
            _record("out", "data", envelope_to_resend.get("op"), "resent", peer=requester, origin=ORIGIN, seq=seq,
                    detail=f"Re-sent for sync.request {from_seq}-{to_seq}", dapps_id=dapps_id)
        except Exception as e:
            wps_logger("REPLICATION RECONCILE", ORIGIN, f"Failed to re-send seq={seq} to {requester}: {e}", "ERROR")
            _record("out", "data", envelope_to_resend.get("op"), "failed", peer=requester, origin=ORIGIN, seq=seq,
                    detail=f"Re-send for sync.request {from_seq}-{to_seq} failed: {e}")


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

    _start_bootstrap_if_configured(cur, conn)
    _retry_bootstrap_pending()

    threading.Thread(target=_outbox_pump_loop, daemon=True, name="replication_outbox_pump").start()
    threading.Thread(target=_inbox_pump_loop, daemon=True, name="replication_inbox_pump").start()
    threading.Thread(target=_reconcile_loop, daemon=True, name="replication_reconcile_pump").start()

    print(f"{timestamp()} Replication started: origin={ORIGIN} app={APP_SLUG} peers={PEERS} dapps={DAPPS_REST_URL}")
