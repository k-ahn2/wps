import csv
import datetime
import io
import json
import os
import sqlite3
import time

import replication

# replication_dashboard.py is the Replication half of the web dashboard: a read-only view of the
# replication tables - per-peer status, the replication_activity log (data received, sync traffic
# in both directions), and every individual data item sent (replication_log) or received
# (replication_activity). It only ever reads wps.db, over a read-only connection, so it can
# never interfere with replication itself.
#
# This module is the JSON API (ROUTES, export_activity) and the page (PAGE). dashboard.py serves
# them - behind its login, at /replication - when replication is enabled. Run
# `python3 replication_dashboard.py` to serve the dashboard on its own (e.g. while WPS is
# stopped), Replication pages included even if replication is disabled.

DB_PATH = os.path.abspath(replication.env['dbFilename'])
MAX_PAGE = 500
ISSUE_STATUSES = ("failed", "error", "rejected")


def _now_ms():
    return round(time.time() * 1000)


def _connect():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(cur, name):
    cur.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
    return cur.fetchone() is not None


def _parse_event(event_json):
    if not event_json:
        return None
    try:
        return json.loads(event_json)
    except ValueError:
        return None


def _clip(text, length=140):
    text = " ".join(str(text).split())
    return text if len(text) <= length else text[:length - 1] + "…"


def _summarise(envelope):
    '''One-line human description of a data event's content, for list views.'''
    if not isinstance(envelope, dict):
        return ""
    op = envelope.get("op") or ""
    key = envelope.get("key") or {}
    data = envelope.get("data") or {}
    if not isinstance(data, dict):
        return ""
    if op == "post.insert":
        return _clip(f"{data.get('fc', '?')} in channel {data.get('cid', '?')}: {data.get('p', '')}")
    if op == "post.edit":
        return _clip(f"edit post {key.get('cid')}/{key.get('ts')}: {data.get('p', '')}")
    if op == "msg.insert":
        return _clip(f"{data.get('fc', '?')} → {data.get('tc', '?')}: {data.get('m', '')}")
    if op == "msg.edit":
        return _clip(f"edit message {key.get('_id')}: {data.get('m', '')}")
    if op in ("post.emoji", "msg.emoji"):
        target = key.get('_id') or f"{key.get('cid')}/{key.get('ts')}"
        return _clip(f"reactions on {target}: {json.dumps(data.get('e'), ensure_ascii=False)}")
    if op == "user.update":
        return _clip(f"{key.get('callsign', data.get('callsign', '?'))} name → {data.get('name', '')}")
    if op == "user.online":
        return _clip(f"{data.get('callsign', '?')} ({data.get('name', '')}) {'online' if data.get('is_online') else 'offline'}")
    return ""


def _peer_pairs():
    return [{"origin": origin, "dapps": dapps} for origin, dapps in replication._PEER_PAIRS]


# --- API -----------------------------------------------------------------------------------

def api_status(cur, _query):
    now = _now_ms()
    activity_ok = _has_table(cur, "replication_activity")

    cur.execute("SELECT next_seq - 1 AS latest, epoch FROM replication_self WHERE id = 1")
    row = cur.fetchone()
    my_latest, epoch = (row["latest"], row["epoch"]) if row else (0, None)

    cur.execute("SELECT COUNT(*), MIN(seq) FROM replication_outbox")
    outbox_count, outbox_oldest = cur.fetchone()

    cur.execute("SELECT peer, peer_acked_seq, submitted_seq FROM replication_peer_ack")
    peer_ack = {r["peer"].upper(): dict(r) for r in cur.fetchall()}
    cur.execute("SELECT origin, last_applied_seq FROM replication_origin_cursor")
    cursors = {r["origin"].upper(): r["last_applied_seq"] for r in cur.fetchall()}
    cur.execute("SELECT origin, COUNT(*) AS n, MIN(seq) AS lo, MAX(seq) AS hi FROM replication_pending GROUP BY origin")
    pending = {r["origin"].upper(): dict(r) for r in cur.fetchall()}
    cur.execute("SELECT origin, requested_at FROM replication_bootstrap_pending")
    bootstrap = {r["origin"].upper(): r["requested_at"] for r in cur.fetchall()}

    def activity_one(sql, params):
        if not activity_ok:
            return None
        cur.execute(sql, params)
        r = cur.fetchone()
        return dict(r) if r else None

    peers = []
    configured_origins = set()
    for pair in _peer_pairs():
        origin_u, dapps_u = pair["origin"].upper(), pair["dapps"].upper()
        configured_origins.add(origin_u)
        ack = peer_ack.get(dapps_u, {})
        applied = cursors.get(origin_u)
        last_digest = activity_one(
            "SELECT at, event FROM replication_activity WHERE direction = 'in' AND op = 'digest' AND UPPER(origin) = ? ORDER BY id DESC LIMIT 1",
            (origin_u,))
        their_latest = None
        if last_digest:
            their_latest = (_parse_event(last_digest["event"]) or {}).get("latest_seq")
        pend = pending.get(origin_u)
        # The digest is only periodic; events applied or buffered since then prove the peer is at least that far along.
        seen = max(applied or 0, pend["hi"] if pend else 0)
        if their_latest is not None or seen:
            their_latest = max(their_latest or 0, seen)
        last_in = activity_one("SELECT MAX(at) AS at FROM replication_activity WHERE direction = 'in' AND UPPER(peer) = ?", (dapps_u,))
        last_out = activity_one("SELECT MAX(at) AS at FROM replication_activity WHERE direction = 'out' AND status IN ('sent', 'resent') AND UPPER(peer) = ?", (dapps_u,))
        issues = activity_one(
            f"SELECT COUNT(*) AS n FROM replication_activity WHERE at >= ? AND UPPER(peer) = ? AND status IN ({','.join('?' * len(ISSUE_STATUSES))})",
            (now - 86400000, dapps_u, *ISSUE_STATUSES))
        acked = ack.get("peer_acked_seq", 0)
        submitted = ack.get("submitted_seq", 0)
        peers.append({
            **pair,
            "submitted_seq": submitted,
            "acked_seq": acked,
            "unsubmitted": max(my_latest - submitted, 0),
            "unacked": max(my_latest - acked, 0),
            "applied_seq": applied,
            "their_latest": their_latest,
            "last_digest_at": last_digest["at"] if last_digest else None,
            "behind": (their_latest - (applied or 0)) if their_latest is not None else None,
            "pending": pend,
            "bootstrap_requested_at": bootstrap.get(origin_u),
            "last_heard_at": last_in["at"] if last_in else None,
            "last_sent_at": last_out["at"] if last_out else None,
            "issues_24h": issues["n"] if issues else 0,
        })

    # Origins we hold a cursor or buffered events for but that aren't (or are no longer) configured
    # - with relay on, mostly the origins a neighbour relays to us.
    other_origins = sorted((set(cursors) | set(pending)) - configured_origins)

    counts = []
    recent_issues = []
    dapps_poll = None
    if activity_ok:
        cur.execute(
            "SELECT direction, category, status, COUNT(*) AS n FROM replication_activity WHERE at >= ? "
            "GROUP BY direction, category, status", (now - 86400000,))
        counts = [dict(r) for r in cur.fetchall()]
        cur.execute(
            f"SELECT id, at, direction, category, op, peer, origin, seq, status, detail FROM replication_activity "
            f"WHERE status IN ({','.join('?' * len(ISSUE_STATUSES))}) ORDER BY id DESC LIMIT 10", ISSUE_STATUSES)
        recent_issues = [dict(r) for r in cur.fetchall()]
        dapps_poll = activity_one("SELECT at, status, detail FROM replication_activity WHERE op = 'dapps.poll' ORDER BY id DESC LIMIT 1", ())

    return {
        "now": now,
        "activity_table": activity_ok,
        "config": {
            "enabled": replication.ENABLED,
            "origin": replication.ORIGIN,
            "dapps_callsign": replication.DAPPS_CALLSIGN,
            "app_slug": replication.APP_SLUG,
            "dapps_rest_url": replication.DAPPS_REST_URL,
            "outbox_poll_seconds": replication.OUTBOX_POLL_SECONDS,
            "inbox_poll_seconds": replication.INBOX_POLL_SECONDS,
            "inbox_fast_poll_seconds": replication.INBOX_FAST_POLL_SECONDS,
            "inbox_fast_poll_window_seconds": replication.INBOX_FAST_POLL_WINDOW_SECONDS,
            "ack_delay_seconds": replication.ACK_DELAY_SECONDS,
            "batch_size": replication.BATCH_SIZE,
            "reconcile_interval_seconds": replication.RECONCILE_INTERVAL_SECONDS,
            "activity_retention_days": replication.ACTIVITY_RETENTION_DAYS,
            "bootstrap_from_ts": replication.BOOTSTRAP_FROM_TS,
            "relay": replication.RELAY,
        },
        "self": {"latest_seq": my_latest, "epoch": epoch, "outbox_count": outbox_count, "outbox_oldest_seq": outbox_oldest},
        "peers": peers,
        "other_origins": [{"origin": o, "applied_seq": cursors.get(o), "pending": pending.get(o),
                           "via": replication._routes.get(o) if replication.RELAY else None} for o in other_origins],
        "counts_24h": counts,
        "recent_issues": recent_issues,
        "dapps_poll": dapps_poll,
    }


def _int_arg(query, name, default=None):
    try:
        return int(query[name][0])
    except (KeyError, ValueError, IndexError):
        return default


def _str_arg(query, name):
    value = query.get(name, [""])[0].strip()
    return value or None


def _activity_filters(query):
    '''WHERE clauses for replication_activity (aliased a, joined to replication_log as l) from
    the query string - shared by the activity view and its export, so a download always matches
    what the filters show on screen.'''
    where, params = [], []
    for column in ("direction", "category", "status", "op"):
        value = _str_arg(query, column)
        if value:
            where.append(f"a.{column} = ?")
            params.append(value)
    for column in ("peer", "origin"):
        value = _str_arg(query, column)
        if value:
            where.append(f"UPPER(a.{column}) = ?")
            params.append(value.upper())
    if _str_arg(query, "issues"):
        where.append(f"a.status IN ({','.join('?' * len(ISSUE_STATUSES))})")
        params.extend(ISSUE_STATUSES)
    seq = _int_arg(query, "seq")
    if seq is not None:
        where.append("a.seq = ?")
        params.append(seq)
    since_hours = _int_arg(query, "since_hours")
    if since_hours:
        where.append("a.at >= ?")
        params.append(_now_ms() - since_hours * 3600000)
    text = _str_arg(query, "q")
    if text:
        where.append("(COALESCE(a.event, l.event, '') LIKE ? OR COALESCE(a.detail, '') LIKE ?)")
        params.extend([f"%{text}%", f"%{text}%"])
    return where, params


# Outbound data rows don't store the envelope - it's already in replication_log.
_ACTIVITY_SELECT = (
    "SELECT a.id, a.at, a.direction, a.category, a.op, a.peer, a.origin, a.seq, a.status, a.detail, a.dapps_id, "
    "COALESCE(a.event, l.event) AS event FROM replication_activity a "
    "LEFT JOIN replication_log l ON a.direction = 'out' AND a.category = 'data' AND l.origin = a.origin AND l.seq = a.seq "
)


def api_activity(cur, query):
    if not _has_table(cur, "replication_activity"):
        return {"rows": [], "activity_table": False}

    where, params = _activity_filters(query)
    before = _int_arg(query, "before")
    if before is not None:
        where.append("a.id < ?")
        params.append(before)
    limit = min(_int_arg(query, "limit", 100), MAX_PAGE)

    cur.execute(
        _ACTIVITY_SELECT + (f"WHERE {' AND '.join(where)} " if where else "") +
        "ORDER BY a.id DESC LIMIT ?", (*params, limit))
    rows = []
    for r in cur.fetchall():
        row = dict(r)
        envelope = _parse_event(row.pop("event"))
        row["summary"] = _summarise(envelope) if row["category"] == "data" else ""
        rows.append(row)
    return {"rows": rows, "activity_table": True, "has_more": len(rows) == limit}


def _iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Shipped inside every JSON export so the file explains itself to whoever (or whatever) reads it.
EXPORT_FIELD_NOTES = {
    "about": "Replication activity from one WPS instance. Instances replicate posts, messages, edits, emoji "
             "reactions and user names to each other over DAPPS. Each instance numbers its own events with a "
             "per-origin sequence (seq); peers apply each origin's events strictly in seq order.",
    "at / at_iso": "When this instance recorded the row (epoch ms / UTC ISO-8601).",
    "direction": "in = received from a peer via DAPPS; out = submitted to local DAPPS for a peer.",
    "category": "data = a replicated change (post.insert, post.edit, post.emoji, msg.insert, msg.edit, msg.emoji, "
                "user.update, and user.online from versions that still sent presence in the stream); "
                "sync = control message (ack, digest, sync.request, seq_at.request, seq_at.response, online.request, online.response, presence); "
                "system = local DAPPS polling failed/recovered.",
    "peer": "The other instance's DAPPS callsign (sender for in, destination for out).",
    "origin / seq": "The instance that originated the data event and its sequence number - together they identify "
                    "one replicated change. For ack rows they identify the event being acknowledged.",
    "status": "in/data: applied, buffered (arrived ahead of a gap, or while bootstrap pending), duplicate (already "
              "applied), stale (older than what is held), ignored, rejected (unconfigured sender), error (apply "
              "failed, will be redelivered). out: sent, resent (answering a sync.request), failed. in/sync: received. "
              "system: failed, recovered.",
    "detail": "Human-readable explanation recorded with the row.",
    "summary": "One-line description of a data event's content.",
    "event": "The full replication envelope / control message. Envelope fields: v, origin, seq, epoch, ts (seconds "
             "for msg.* ops, milliseconds otherwise), op, key, data.",
    "healthy pattern": "Each data event: out sent -> peer in applied -> peer out ack -> origin in ack. With batch_size > 1 a backlog goes "
                       "as one DAPPS message: its events share a dapps_id and say 'In batch of N'. Acks are held "
                       "ack_delay_seconds and combined, so one ack can cover several seqs. Digests every reconcile "
                       "interval in both directions, skipped while recent traffic shows the peer is level. Gaps show as buffered + sync.request, then resent and "
                       "applied.",
}


def export_activity(cur, query):
    '''Returns (body, content_type, filename): activity rows matching the filters, oldest first. Optional
    scope: last=N keeps only the most recent N matching rows; since_ms=<epoch ms> keeps rows recorded at or after it.'''
    fmt = "csv" if _str_arg(query, "format") == "csv" else "json"
    now = _now_ms()
    rows = []
    if _has_table(cur, "replication_activity"):
        where, params = _activity_filters(query)
        since_ms = _int_arg(query, "since_ms")
        if since_ms is not None:
            where.append("a.at >= ?")
            params.append(since_ms)
        sql = _ACTIVITY_SELECT + (f"WHERE {' AND '.join(where)} " if where else "")
        last = _int_arg(query, "last")
        if last and last > 0:
            # Most recent N, still written oldest first.
            cur.execute(sql + "ORDER BY a.id DESC LIMIT ?", (*params, last))
            fetched = cur.fetchall()[::-1]
        else:
            cur.execute(sql + "ORDER BY a.id ASC", params)
            fetched = cur.fetchall()
        for r in fetched:
            row = dict(r)
            envelope = _parse_event(row.pop("event"))
            rows.append({
                "id": row["id"], "at": row["at"], "at_iso": _iso(row["at"]),
                **{k: row[k] for k in ("direction", "category", "op", "peer", "origin", "seq", "status", "detail", "dapps_id")},
                "summary": _summarise(envelope) if row["category"] == "data" else "",
                "event": envelope,
            })

    stamp = datetime.datetime.fromtimestamp(now / 1000).strftime("%Y%m%d-%H%M%S")
    filename = f"wps-replication-activity-{replication.ORIGIN or 'node'}-{stamp}.{fmt}"
    filters = {k: v[0] for k, v in query.items() if k != "format" and v and v[0]}

    if fmt == "csv":
        out = io.StringIO()
        columns = ["id", "at_iso", "at", "direction", "category", "op", "peer", "origin", "seq", "status", "detail", "dapps_id", "summary", "event"]
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "event": json.dumps(row["event"], ensure_ascii=False, separators=(',', ':')) if row["event"] is not None else ""})
        return out.getvalue(), "text/csv; charset=utf-8", filename

    body = {
        "export": {
            "generated_at": _iso(now),
            "node_origin": replication.ORIGIN,
            "node_dapps_callsign": replication.DAPPS_CALLSIGN,
            "filters": filters,
            "row_count": len(rows),
            "first_at": rows[0]["at_iso"] if rows else None,
            "last_at": rows[-1]["at_iso"] if rows else None,
            "retention_days": replication.ACTIVITY_RETENTION_DAYS,
            "field_notes": EXPORT_FIELD_NOTES,
        },
        "status_at_export": api_status(cur, {}),
        "rows": rows,
    }
    return json.dumps(body, ensure_ascii=False, indent=1, default=str), "application/json; charset=utf-8", filename


def _sent_peer_states(seq, peer_ack, dapps_ids):
    states = []
    for pair in _peer_pairs():
        ack = peer_ack.get(pair["dapps"].upper(), {})
        if ack.get("peer_acked_seq", 0) >= seq:
            state = "acked"
        elif ack.get("submitted_seq", 0) >= seq:
            state = "submitted"
        else:
            state = "queued"
        states.append({**pair, "state": state, "dapps_id": dapps_ids.get(pair["dapps"])})
    return states


def api_sent(cur, query):
    cur.execute("SELECT peer, peer_acked_seq, submitted_seq FROM replication_peer_ack")
    peer_ack = {r["peer"].upper(): dict(r) for r in cur.fetchall()}

    where, params = ["l.origin = ?"], [replication.ORIGIN]
    op = _str_arg(query, "op")
    if op:
        where.append("l.op = ?")
        params.append(op)
    text = _str_arg(query, "q")
    if text:
        where.append("l.event LIKE ?")
        params.append(f"%{text}%")
    before = _int_arg(query, "before")
    if before is not None:
        where.append("l.seq < ?")
        params.append(before)
    limit = min(_int_arg(query, "limit", 100), MAX_PAGE)

    cur.execute(
        "SELECT l.seq, l.ts, l.op, l.event, o.dapps_ids FROM replication_log l "
        "LEFT JOIN replication_outbox o ON o.seq = l.seq "
        f"WHERE {' AND '.join(where)} ORDER BY l.seq DESC LIMIT ?", (*params, limit))
    rows = []
    for r in cur.fetchall():
        envelope = _parse_event(r["event"])
        dapps_ids = json.loads(r["dapps_ids"]) if r["dapps_ids"] else {}
        rows.append({
            "seq": r["seq"], "ts": r["ts"], "op": r["op"], "origin": replication.ORIGIN,
            "summary": _summarise(envelope),
            "peers": _sent_peer_states(r["seq"], peer_ack, dapps_ids),
        })
    return {"rows": rows, "has_more": len(rows) == limit}


def api_item(cur, query):
    '''Everything known about one data event, identified by (origin, seq).'''
    origin = _str_arg(query, "origin")
    seq = _int_arg(query, "seq")
    if not origin or seq is None:
        return {"error": "origin and seq are required"}
    activity_ok = _has_table(cur, "replication_activity")
    result = {"origin": origin, "seq": seq, "event": None, "source": None, "peers": None, "applied": None, "timeline": []}

    is_self = replication.ORIGIN and origin.upper() == replication.ORIGIN.upper()
    if is_self:
        cur.execute("SELECT event FROM replication_log WHERE origin = ? AND seq = ?", (replication.ORIGIN, seq))
        row = cur.fetchone()
        if row:
            result["event"], result["source"] = _parse_event(row["event"]), "replication_log"
        cur.execute("SELECT peer, peer_acked_seq, submitted_seq FROM replication_peer_ack")
        peer_ack = {r["peer"].upper(): dict(r) for r in cur.fetchall()}
        cur.execute("SELECT dapps_ids FROM replication_outbox WHERE seq = ?", (seq,))
        row = cur.fetchone()
        dapps_ids = json.loads(row["dapps_ids"]) if row and row["dapps_ids"] else {}
        result["peers"] = _sent_peer_states(seq, peer_ack, dapps_ids)
        result["in_outbox"] = row is not None
    else:
        cur.execute("SELECT last_applied_seq FROM replication_origin_cursor WHERE UPPER(origin) = ?", (origin.upper(),))
        row = cur.fetchone()
        result["applied"] = row is not None and row["last_applied_seq"] >= seq
        cur.execute("SELECT event FROM replication_pending WHERE UPPER(origin) = ? AND seq = ?", (origin.upper(), seq))
        row = cur.fetchone()
        if row:
            result["event"], result["source"] = _parse_event(row["event"]), "replication_pending"
            result["pending"] = True

    if activity_ok:
        cur.execute(
            "SELECT id, at, direction, category, op, peer, status, detail, dapps_id, event FROM replication_activity "
            "WHERE UPPER(origin) = ? AND seq = ? ORDER BY id ASC", (origin.upper(), seq))
        for r in cur.fetchall():
            row = dict(r)
            envelope = _parse_event(row.pop("event"))
            if result["event"] is None and row["category"] == "data" and envelope:
                result["event"], result["source"] = envelope, "replication_activity"
            result["timeline"].append(row)
    return result


def api_activity_row(cur, query):
    row_id = _int_arg(query, "id")
    if row_id is None or not _has_table(cur, "replication_activity"):
        return {"error": "not found"}
    cur.execute("SELECT * FROM replication_activity WHERE id = ?", (row_id,))
    r = cur.fetchone()
    if not r:
        return {"error": "not found"}
    row = dict(r)
    row["event"] = _parse_event(row["event"])
    return row


def api_pending(cur, _query):
    cur.execute("SELECT origin, seq, event FROM replication_pending ORDER BY origin, seq")
    rows = []
    for r in cur.fetchall():
        envelope = _parse_event(r["event"])
        rows.append({"origin": r["origin"], "seq": r["seq"], "op": (envelope or {}).get("op"), "summary": _summarise(envelope)})
    return {"rows": rows}


ROUTES = {
    "/api/status": api_status,
    "/api/activity": api_activity,
    "/api/activity/row": api_activity_row,
    "/api/sent": api_sent,
    "/api/item": api_item,
    "/api/pending": api_pending,
}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WPS Replication</title>
<style>
__BASE_CSS__</style>
</head>
<body>
<header>
  __APP_NAV__
  <span class="ident" id="ident"></span>
  <span class="spacer"></span>
  __ACCOUNT__
  <div class="subbar">
    <nav id="nav">
      <button data-view="overview" class="active">Overview</button>
      <button data-view="activity">Activity log</button>
      <button data-view="received">Received items</button>
      <button data-view="sent">Sent items</button>
      <button data-view="pending">Buffered</button>
    </nav>
    <span class="spacer"></span>
    <label class="refresh"><input type="checkbox" id="auto" checked> Auto-refresh 5s <span id="updated"></span></label>
  </div>
</header>

<main>
  <section class="view active" id="view-overview">
    <div id="notices"></div>
    <div class="tiles" id="tiles"></div>
    <div class="card"><h2>Peers</h2><div class="table-wrap"><table id="peers"></table></div></div>
    <div class="card"><h2>Last 24 hours</h2><div class="table-wrap"><table id="counts"></table></div></div>
    <div class="card"><h2>Recent problems</h2><div class="table-wrap"><table id="issues"></table></div></div>
    <div class="card"><h2>Configuration</h2><div class="body"><dl class="kv" id="config"></dl></div></div>
  </section>

  <section class="view" id="view-activity">
    <div class="card">
      <div class="filters">
        <select id="f-category"><option value="">All categories</option><option value="data">Data</option><option value="sync">Sync</option><option value="system">System</option></select>
        <select id="f-direction"><option value="">Both directions</option><option value="in">Received</option><option value="out">Sent</option></select>
        <select id="f-status"><option value="">Any status</option></select>
        <select id="f-peer"><option value="">All peers</option></select>
        <label class="muted"><input type="checkbox" id="f-issues"> Problems only</label>
        <select id="f-since"><option value="">All retained</option><option value="1">Last hour</option><option value="6">Last 6 hours</option><option value="24">Last 24 hours</option><option value="168">Last 7 days</option></select>
        <input type="search" id="f-q" placeholder="Search content / detail">
        <span class="spacer"></span>
        <button class="btn" id="dl-json" title="Rows matching these filters, with full message bodies and a snapshot of peer status - suited to analysis">Download JSON</button>
        <button class="btn" id="dl-csv" title="Rows matching these filters, one per line">Download CSV</button>
      </div>
      <div class="table-wrap"><table id="activity"></table></div>
      <div class="more" id="activity-more"></div>
    </div>
  </section>

  <section class="view" id="view-received">
    <div class="card">
      <div class="filters">
        <select id="r-origin"><option value="">All origins</option></select>
        <select id="r-status"><option value="">Any outcome</option><option>applied</option><option>buffered</option><option>duplicate</option><option>stale</option><option>ignored</option><option>rejected</option><option>error</option></select>
        <select id="r-op" class="op-select"></select>
        <input type="search" id="r-q" placeholder="Search content">
      </div>
      <div class="table-wrap"><table id="received"></table></div>
      <div class="more" id="received-more"></div>
    </div>
  </section>

  <section class="view" id="view-sent">
    <div class="card">
      <div class="filters">
        <select id="s-op" class="op-select"></select>
        <input type="search" id="s-q" placeholder="Search content">
        <span class="muted">Per-peer state: <span class="pill">queued</span> not yet handed to DAPPS · <span class="pill info">submitted</span> in DAPPS · <span class="pill ok">acked</span> applied by peer</span>
      </div>
      <div class="table-wrap"><table id="sent"></table></div>
      <div class="more" id="sent-more"></div>
    </div>
  </section>

  <section class="view" id="view-pending">
    <div class="card"><h2>Events buffered ahead of a gap</h2><div class="table-wrap"><table id="pending"></table></div></div>
  </section>
</main>

<dialog id="dl-dialog">
  <form method="dialog">
    <h3 id="dl-title">Download</h3>
    <div class="content">
      <label class="opt"><input type="radio" name="dl-scope" value="all" checked> Everything</label>
      <label class="opt"><input type="radio" name="dl-scope" value="last"> Last <input type="number" id="dl-last" min="1" step="1" value="1000"> rows</label>
      <label class="opt"><input type="radio" name="dl-scope" value="since"> Everything since <input type="datetime-local" id="dl-since" step="1"></label>
      <div class="muted" style="font-size:12px">The activity filters currently set on screen still apply.</div>
    </div>
    <div class="actions">
      <button class="btn" value="cancel" formnovalidate>Cancel</button>
      <button class="btn primary" id="dl-go" value="ok">Download</button>
    </div>
  </form>
</dialog>

<aside id="drawer" aria-hidden="true">
  <div class="head"><h3 id="drawer-title"></h3><button class="btn" id="drawer-close">Close</button></div>
  <div class="content" id="drawer-content"></div>
</aside>

<script>
const OPS = ["post.insert", "post.edit", "post.emoji", "msg.insert", "msg.edit", "msg.emoji", "user.update", "user.online"];
const STATUSES = ["applied", "sent", "resent", "received", "buffered", "duplicate", "stale", "ignored", "failed", "error", "rejected", "recovered"];
const STATUS_CLASS = { applied: "ok", sent: "info", resent: "info", received: "", recovered: "ok", acked: "ok", submitted: "info",
  buffered: "warn", duplicate: "", stale: "", ignored: "", queued: "", failed: "bad", error: "bad", rejected: "bad" };

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pill = (s) => `<span class="pill ${STATUS_CLASS[s] ?? ""}">${esc(s)}</span>`;
const dir = (d) => `<span class="dir ${d}">${d === "in" ? "← IN" : "OUT →"}</span>`;
const num = (n) => n == null ? "—" : Number(n).toLocaleString();

function fmtTime(ms) {
  if (!ms) return "—";
  const d = new Date(ms);
  const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString() : d.toLocaleString();
}
function ago(ms) {
  if (!ms) return "never";
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
// replication_log.ts is in seconds for msg.* ops and milliseconds for everything else.
const eventTs = (op, ts) => (op || "").startsWith("msg.") ? ts * 1000 : ts;

async function api(path, params = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null && v !== false)).toString();
  const res = await fetch(path + (qs ? "?" + qs : ""));
  if (res.status === 401) { location.href = "/"; throw new Error("Logged out"); }
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

// ---------- Overview ----------
let lastStatus = null;
async function loadOverview() {
  const s = await api("/api/status");
  lastStatus = s;
  const c = s.config;
  $("ident").textContent = `${c.origin || "(no origin)"} · DAPPS ${c.dapps_callsign || "—"} · ${c.app_slug}`;

  const notices = [];
  if (!c.enabled) notices.push(`<div class="notice">Replication is disabled in env.json (replication.enabled = false). Showing whatever is in the tables.</div>`);
  if (!s.activity_table) notices.push(`<div class="notice">The replication_activity table doesn't exist yet - restart WPS once so db.dbInit creates it. Status below comes from the core replication tables only.</div>`);
  if (s.dapps_poll && s.dapps_poll.status === "failed") notices.push(`<div class="notice bad">Local DAPPS unreachable since ${esc(fmtTime(s.dapps_poll.at))}: ${esc(s.dapps_poll.detail)}</div>`);
  $("notices").innerHTML = notices.join("");

  const tally = (pred) => s.counts_24h.filter(pred).reduce((a, r) => a + r.n, 0);
  const issues24 = tally((r) => ["failed", "error", "rejected"].includes(r.status));
  const pendingTotal = s.peers.reduce((a, p) => a + (p.pending?.n || 0), 0) + s.other_origins.reduce((a, o) => a + (o.pending?.n || 0), 0);
  $("tiles").innerHTML = [
    ["Our latest seq", num(s.self.latest_seq), `epoch ${esc(s.self.epoch ?? "—")}`],
    ["Outbox awaiting ack", num(s.self.outbox_count), s.self.outbox_oldest_seq ? `oldest seq ${s.self.outbox_oldest_seq}` : "all acknowledged"],
    ["Received data (24h)", num(tally((r) => r.direction === "in" && r.category === "data" && r.status === "applied")), `${num(tally((r) => r.direction === "in" && r.category === "data"))} deliveries incl. dupes`],
    ["Sent data (24h)", num(tally((r) => r.direction === "out" && r.category === "data" && ["sent", "resent"].includes(r.status))), "submissions to DAPPS, all peers"],
    ["Sync messages (24h)", num(tally((r) => r.category === "sync")), `${num(tally((r) => r.category === "sync" && r.direction === "in"))} in / ${num(tally((r) => r.category === "sync" && r.direction === "out"))} out`],
    ["Buffered (gaps)", num(pendingTotal), pendingTotal ? "waiting on earlier events" : "none"],
    ["Problems (24h)", num(issues24), issues24 ? "see below" : "none"],
  ].map(([l, v, sub]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join("");

  $("peers").innerHTML = `<thead><tr><th>Peer</th><th>Health</th><th>Our events → them</th><th>Their events → us</th><th>Buffered</th><th>Last heard</th><th>Last sent</th><th>Problems 24h</th></tr></thead><tbody>` +
    (s.peers.length ? s.peers.map((p) => {
      const health = peerHealth(p, s);
      const theirs = p.bootstrap_requested_at ? `<span class="pill warn">bootstrapping</span> since ${esc(ago(p.bootstrap_requested_at))}`
        : `applied ${num(p.applied_seq ?? 0)}${p.their_latest != null ? ` of ${num(p.their_latest)}` : ""}` +
          (p.behind > 0 ? ` <span class="pill warn">${num(p.behind)} behind</span>` : "") +
          `<div class="muted">last digest ${esc(ago(p.last_digest_at))}</div>`;
      return `<tr>
        <td><a class="link" data-peer="${esc(p.dapps)}"><b>${esc(p.origin)}</b></a><div class="muted mono">${esc(p.dapps)}</div></td>
        <td><span class="pill ${health[1]}">${esc(health[0])}</span><div class="muted">${esc(health[2])}</div></td>
        <td>acked ${num(p.acked_seq)} / ${num(s.self.latest_seq)}${p.unacked ? ` <span class="pill warn">${num(p.unacked)} unacked</span>` : ""}
            <div class="muted">submitted ${num(p.submitted_seq)}${p.unsubmitted ? ` · ${num(p.unsubmitted)} not yet submitted` : ""}</div></td>
        <td>${theirs}</td>
        <td>${p.pending ? `${num(p.pending.n)} <span class="muted">(${p.pending.lo}–${p.pending.hi})</span>` : "—"}</td>
        <td>${esc(ago(p.last_heard_at))}</td>
        <td>${esc(ago(p.last_sent_at))}</td>
        <td>${p.issues_24h ? `<span class="pill bad">${p.issues_24h}</span>` : "0"}</td></tr>`;
    }).join("") : `<tr><td colspan="8" class="empty">No peers configured</td></tr>`) +
    s.other_origins.map((o) => `<tr><td><b>${esc(o.origin)}</b><div class="muted">${o.via ? `relayed via ${esc(o.via)}` : "not in peers config"}</div></td><td>${pill(o.via ? "relayed" : "unconfigured")}</td><td>—</td><td>applied ${num(o.applied_seq)}</td><td>${o.pending ? num(o.pending.n) : "—"}</td><td colspan="3"></td></tr>`).join("") +
    `</tbody>`;
  $("peers").querySelectorAll("[data-peer]").forEach((a) => a.onclick = () => { $("f-peer").value = a.dataset.peer; showView("activity"); });

  const groups = {};
  s.counts_24h.forEach((r) => { const k = `${r.direction}|${r.category}`; (groups[k] ||= {})[r.status] = r.n; });
  const keys = Object.keys(groups).sort();
  $("counts").innerHTML = keys.length ? `<thead><tr><th>Direction</th><th>Category</th><th>By status</th></tr></thead><tbody>` +
    keys.map((k) => { const [d, cat] = k.split("|"); return `<tr><td>${dir(d)}</td><td>${esc(cat)}</td><td>${Object.entries(groups[k]).map(([st, n]) => `${pill(st)} ${num(n)}`).join("&nbsp;&nbsp; ")}</td></tr>`; }).join("") + `</tbody>`
    : `<tbody><tr><td class="empty">No activity recorded in the last 24 hours</td></tr></tbody>`;

  $("issues").innerHTML = s.recent_issues.length ? activityHead() + `<tbody>${s.recent_issues.map(activityRow).join("")}</tbody>` : `<tbody><tr><td class="empty">No failures, errors or rejections on record</td></tr></tbody>`;
  bindActivityRows($("issues"));

  $("config").innerHTML = Object.entries(c).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v ?? "—")}</dd>`).join("") +
    `<dt>peers</dt><dd>${s.peers.map((p) => `${esc(p.origin)} (${esc(p.dapps)})`).join(", ") || "—"}</dd>`;

  const peerSel = $("f-peer"), current = peerSel.value;
  peerSel.innerHTML = `<option value="">All peers</option>` + s.peers.map((p) => `<option value="${esc(p.dapps)}">${esc(p.origin)} (${esc(p.dapps)})</option>`).join("");
  peerSel.value = current;
  const originSel = $("r-origin"), curOrigin = originSel.value;
  originSel.innerHTML = `<option value="">All origins</option>` + [...s.peers.map((p) => p.origin), ...s.other_origins.map((o) => o.origin)].map((o) => `<option>${esc(o)}</option>`).join("");
  originSel.value = curOrigin;
}

function peerHealth(p, s) {
  // A peer skips its digest only if it heard from us that interval, so a few missed intervals means it's gone quiet.
  if (p.bootstrap_requested_at) return ["bootstrapping", "warn", "waiting for seq_at.response"];
  const staleAfter = s.config.reconcile_interval_seconds * 3 * 1000;
  if (!p.last_heard_at) return ["unknown", "", "nothing heard yet"];
  if (Date.now() - p.last_heard_at > staleAfter) return ["silent", "bad", `nothing for ${ago(p.last_heard_at).replace(" ago", "")}`];
  if (p.pending?.n || p.behind > 0) return ["catching up", "warn", "gap or backlog from them"];
  if (p.unsubmitted > 0) return ["backlogged", "warn", "our events not yet in DAPPS"];
  return ["in step", "ok", "level both ways"];
}

// ---------- Activity log ----------
function activityHead(showSummary = true) {
  return `<thead><tr><th>Time</th><th>Dir</th><th>Category</th><th>Op</th><th>Peer</th><th>Item</th><th>Status</th><th>${showSummary ? "Detail / content" : "Detail"}</th></tr></thead>`;
}
function activityRow(r) {
  const item = r.seq != null && r.origin ? `<span class="mono">${esc(r.origin)}/${r.seq}</span>` : `<span class="muted">${esc(r.origin || "")}</span>`;
  const text = [r.summary, r.detail].filter(Boolean).map(esc).join(`<div class="muted">`) + (r.summary && r.detail ? "</div>" : "");
  return `<tr class="clickable" data-id="${r.id}" data-origin="${esc(r.origin || "")}" data-seq="${r.seq ?? ""}" data-category="${esc(r.category)}" data-op="${esc(r.op || "")}">
    <td class="mono">${esc(fmtTime(r.at))}</td><td>${dir(r.direction)}</td><td>${esc(r.category)}</td><td class="mono">${esc(r.op || "")}</td>
    <td class="mono">${esc(r.peer || "")}</td><td>${item}</td><td>${pill(r.status)}</td><td class="wrap">${text}</td></tr>`;
}
function bindActivityRows(table) {
  table.querySelectorAll("tr[data-id]").forEach((tr) => tr.onclick = () => {
    const d = tr.dataset;
    const isItem = d.seq !== "" && d.origin && (d.category === "data" || d.op === "ack");
    isItem ? openItem(d.origin, d.seq) : openActivity(d.id);
  });
}

function makeList({ table, more, head, row, bind, fetchPage, cursor, empty }) {
  let rows = [], hasMore = false;
  async function load(append = false) {
    const params = append && rows.length ? { before: cursor(rows[rows.length - 1]) } : {};
    const res = await fetchPage(params);
    rows = append ? rows.concat(res.rows) : res.rows;
    hasMore = res.has_more;
    $(table).innerHTML = head() + `<tbody>${rows.length ? rows.map(row).join("") : `<tr><td colspan="9" class="empty">${res.activity_table === false ? "replication_activity table not created yet - restart WPS" : empty}</td></tr>`}</tbody>`;
    bind($(table));
    $(more).innerHTML = hasMore ? `<button class="btn">Load older</button>` : "";
    if (hasMore) $(more).firstChild.onclick = () => load(true);
  }
  // Auto-refresh only reloads the first page, so it never throws away rows the user paged into.
  return { load, refresh: () => rows.length <= 100 ? load(false) : Promise.resolve() };
}

const activityFilters = () => ({ category: $("f-category").value, direction: $("f-direction").value, status: $("f-status").value,
  peer: $("f-peer").value, issues: $("f-issues").checked ? 1 : "", since_hours: $("f-since").value, q: $("f-q").value });

function downloadActivity(format, scope = {}) {
  const params = Object.entries({ ...activityFilters(), ...scope, format }).filter(([, v]) => v !== "" && v != null);
  location.href = "/api/activity/export?" + new URLSearchParams(params).toString();
}

// datetime-local wants local time without a zone: YYYY-MM-DDTHH:MM:SS
const toLocalInput = (ms) => { const d = new Date(ms - new Date(ms).getTimezoneOffset() * 60000); return d.toISOString().slice(0, 19); };

let dlFormat = "json";
function openDownloadDialog(format) {
  dlFormat = format;
  $("dl-title").textContent = `Download ${format.toUpperCase()}`;
  if (!$("dl-since").value) $("dl-since").value = toLocalInput(Date.now() - 24 * 3600000);
  $("dl-dialog").showModal();
}

function dlScope() {
  const scope = document.querySelector('input[name="dl-scope"]:checked').value;
  if (scope === "last") {
    const n = parseInt($("dl-last").value, 10);
    return n > 0 ? { last: n } : null;
  }
  if (scope === "since") {
    const ms = new Date($("dl-since").value).getTime();
    return Number.isFinite(ms) ? { since_ms: ms } : null;
  }
  return {};
}

const activityList = makeList({
  table: "activity", more: "activity-more", head: activityHead, row: activityRow, bind: bindActivityRows, cursor: (r) => r.id,
  empty: "No activity matches these filters",
  fetchPage: (p) => api("/api/activity", { ...p, ...activityFilters() }),
});

const receivedList = makeList({
  table: "received", more: "received-more", cursor: (r) => r.id, empty: "Nothing received yet",
  head: () => `<thead><tr><th>Received</th><th>Item</th><th>Op</th><th>Via</th><th>Outcome</th><th>Content</th></tr></thead>`,
  row: (r) => `<tr class="clickable" data-id="${r.id}" data-origin="${esc(r.origin || "")}" data-seq="${r.seq ?? ""}" data-category="data">
    <td class="mono">${esc(fmtTime(r.at))}</td><td class="mono">${esc(r.origin || "?")}/${r.seq ?? "?"}</td><td class="mono">${esc(r.op || "")}</td>
    <td class="mono">${esc(r.peer || "")}</td><td>${pill(r.status)}${r.detail ? `<div class="muted">${esc(r.detail)}</div>` : ""}</td><td class="wrap">${esc(r.summary)}</td></tr>`,
  bind: bindActivityRows,
  fetchPage: (p) => api("/api/activity", { ...p, direction: "in", category: "data", origin: $("r-origin").value, status: $("r-status").value, op: $("r-op").value, q: $("r-q").value }),
});

const sentList = makeList({
  table: "sent", more: "sent-more", cursor: (r) => r.seq, empty: "No events originated here yet",
  head: () => `<thead><tr><th>Seq</th><th>Event time</th><th>Op</th><th>Peers</th><th>Content</th></tr></thead>`,
  row: (r) => `<tr class="clickable" data-origin="${esc(r.origin)}" data-seq="${r.seq}">
    <td class="mono">${r.seq}</td><td class="mono">${esc(fmtTime(eventTs(r.op, r.ts)))}</td><td class="mono">${esc(r.op)}</td>
    <td><div class="peerstates">${r.peers.map((p) => `<span class="pill ${STATUS_CLASS[p.state]}" title="${esc(p.dapps)}">${esc(p.origin)}: ${p.state}</span>`).join("")}</div></td>
    <td class="wrap">${esc(r.summary)}</td></tr>`,
  bind: (t) => t.querySelectorAll("tr[data-seq]").forEach((tr) => tr.onclick = () => openItem(tr.dataset.origin, tr.dataset.seq)),
  fetchPage: (p) => api("/api/sent", { ...p, op: $("s-op").value, q: $("s-q").value }),
});

async function loadPending() {
  const res = await api("/api/pending");
  $("pending").innerHTML = `<thead><tr><th>Item</th><th>Op</th><th>Content</th></tr></thead><tbody>` +
    (res.rows.length ? res.rows.map((r) => `<tr class="clickable" data-origin="${esc(r.origin)}" data-seq="${r.seq}"><td class="mono">${esc(r.origin)}/${r.seq}</td><td class="mono">${esc(r.op || "")}</td><td class="wrap">${esc(r.summary)}</td></tr>`).join("")
      : `<tr><td colspan="3" class="empty">Nothing buffered - every origin's stream is contiguous</td></tr>`) + `</tbody>`;
  $("pending").querySelectorAll("tr[data-seq]").forEach((tr) => tr.onclick = () => openItem(tr.dataset.origin, tr.dataset.seq));
}

// ---------- Detail drawer ----------
function openDrawer(title, html) {
  $("drawer-title").textContent = title;
  $("drawer-content").innerHTML = html;
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
}
function closeDrawer() {
  $("drawer").classList.remove("open"); $("drawer").setAttribute("aria-hidden", "true");
  if (location.hash.startsWith("#item/")) history.replaceState(null, "", `#${currentView}`);
}
const json = (v) => `<pre class="json">${esc(JSON.stringify(v, null, 2))}</pre>`;

async function openItem(origin, seq) {
  history.replaceState(null, "", `#item/${encodeURIComponent(origin)}/${seq}`);
  openDrawer(`${origin} / ${seq}`, `<p class="muted">Loading…</p>`);
  const it = await api("/api/item", { origin, seq });
  const ev = it.event;
  let html = `<h4>Item</h4><dl class="kv"><dt>origin / seq</dt><dd>${esc(origin)} / ${esc(seq)}</dd>`;
  if (ev) html += `<dt>op</dt><dd>${esc(ev.op)}</dd><dt>event time</dt><dd>${esc(fmtTime(eventTs(ev.op, ev.ts)))}</dd><dt>epoch</dt><dd>${esc(ev.epoch)}</dd>`;
  if (ev && ev.key) html += `<dt>key</dt><dd>${esc(JSON.stringify(ev.key))}</dd>`;
  if (it.applied != null) html += `<dt>applied here</dt><dd>${it.applied ? "yes" : "no"}${it.pending ? " (buffered, waiting for earlier events)" : ""}</dd>`;
  if (it.in_outbox != null) html += `<dt>outbox</dt><dd>${it.in_outbox ? "still awaiting acknowledgement from every peer" : "retired - acknowledged by all peers"}</dd>`;
  html += `</dl>`;
  if (it.peers) html += `<h4>Delivery to peers</h4><table><thead><tr><th>Peer</th><th>State</th><th>DAPPS id</th></tr></thead><tbody>${it.peers.map((p) => `<tr><td>${esc(p.origin)} <span class="muted mono">${esc(p.dapps)}</span></td><td>${pill(p.state)}</td><td class="mono">${esc(p.dapps_id || "—")}</td></tr>`).join("")}</tbody></table>`;
  html += `<h4>Timeline</h4>` + (it.timeline.length
    ? `<table class="timeline"><thead><tr><th>Time</th><th>Dir</th><th>Op</th><th>Peer</th><th>Status</th><th>Detail</th></tr></thead><tbody>${it.timeline.map((r) => `<tr><td class="mono">${esc(fmtTime(r.at))}</td><td>${dir(r.direction)}</td><td class="mono">${esc(r.op || "")}</td><td class="mono">${esc(r.peer || "")}</td><td>${pill(r.status)}</td><td class="wrap">${esc(r.detail || "")}${r.dapps_id ? `<div class="muted mono">DAPPS ${esc(r.dapps_id)}</div>` : ""}</td></tr>`).join("")}</tbody></table>`
    : `<p class="muted">No activity recorded for this item (it may predate the activity log, or have been pruned).</p>`);
  html += `<h4>Event${it.source ? ` <span class="muted">(from ${esc(it.source)})</span>` : ""}</h4>` + (ev ? json(ev) : `<p class="muted">Event body not available.</p>`);
  openDrawer(`${origin} / ${seq}`, html);
}

async function openActivity(id) {
  openDrawer(`Activity #${id}`, `<p class="muted">Loading…</p>`);
  const r = await api("/api/activity/row", { id });
  const { event, ...meta } = r;
  openDrawer(`${r.op || r.category} · ${r.status}`,
    `<h4>Record</h4><dl class="kv">${Object.entries(meta).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(k === "at" ? `${fmtTime(v)} (${v})` : v ?? "—")}</dd>`).join("")}</dl>` +
    `<h4>Message</h4>` + (event ? json(event) : `<p class="muted">No message body recorded.</p>`));
}

// ---------- Wiring ----------
const views = { overview: loadOverview, activity: () => activityList.load(), received: () => receivedList.load(), sent: () => sentList.load(), pending: loadPending };
const refreshers = { overview: loadOverview, activity: activityList.refresh, received: receivedList.refresh, sent: sentList.refresh, pending: loadPending };
let currentView = "overview";

function showView(name) {
  currentView = name;
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll("section.view").forEach((s) => s.classList.toggle("active", s.id === `view-${name}`));
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  run(views[name]);
}
async function run(fn) {
  try { await fn(); $("updated").textContent = `· updated ${new Date().toLocaleTimeString()}`; }
  catch (e) { $("updated").textContent = `· error: ${e.message}`; }
}

document.querySelectorAll("#nav button").forEach((b) => b.onclick = () => showView(b.dataset.view));
$("drawer-close").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
$("f-status").innerHTML += STATUSES.map((s) => `<option>${s}</option>`).join("");
document.querySelectorAll(".op-select").forEach((sel) => sel.innerHTML = `<option value="">All ops</option>` + OPS.map((o) => `<option>${o}</option>`).join(""));

let debounce;
const onFilter = (list) => () => { clearTimeout(debounce); debounce = setTimeout(() => run(list.load), 250); };
$("dl-json").onclick = () => openDownloadDialog("json");
$("dl-csv").onclick = () => openDownloadDialog("csv");
// Picking a value selects its option.
$("dl-last").addEventListener("focus", () => document.querySelector('input[name="dl-scope"][value="last"]').checked = true);
$("dl-since").addEventListener("focus", () => document.querySelector('input[name="dl-scope"][value="since"]').checked = true);
$("dl-go").onclick = (e) => {
  const scope = dlScope();
  if (!scope) { e.preventDefault(); return; }
  downloadActivity(dlFormat, scope);
};
["f-category", "f-direction", "f-status", "f-peer", "f-issues", "f-since"].forEach((id) => $(id).onchange = onFilter(activityList));
$("f-q").oninput = onFilter(activityList);
["r-origin", "r-status", "r-op"].forEach((id) => $(id).onchange = onFilter(receivedList));
$("r-q").oninput = onFilter(receivedList);
$("s-op").onchange = onFilter(sentList);
$("s-q").oninput = onFilter(sentList);

setInterval(() => {
  if ($("auto").checked && !document.hidden && !$("drawer").classList.contains("open")) run(refreshers[currentView]);
}, 5000);

// URL hash: #<view> selects a tab, #item/<origin>/<seq> opens that item - both bookmarkable.
run(loadOverview).then(() => {
  const [name, origin, seq] = location.hash.slice(1).split("/");
  if (name === "item" && origin && seq) run(() => openItem(decodeURIComponent(origin), seq));
  else if (views[name] && name !== "overview") showView(name);
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    # Standalone, e.g. while WPS is stopped: the whole dashboard, with the Replication pages
    # shown even if replication is disabled. See dashboard.py.
    import dashboard
    dashboard.main(show_replication=True)
