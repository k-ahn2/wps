#!/usr/bin/env python3
'''
Read-only CLI for inspecting WPS's replication queues around DAPPS (see replication.py /
docs/replication/DESIGN.md). Shows three things, none of which it ever mutates:

  - outbound: rows in replication_outbox (events captured locally, not yet acked by every
    peer at the application level), with per-peer submitted/acked status.
  - inbound gap buffer: rows in replication_pending (events received out of order, held
    locally while a resend of the missing seq(s) is requested).
  - live DAPPS inbound: whatever DAPPS is currently holding for us via GET
    /AppApi/inbound/{appSlug} - a plain GET, so polling it here never acks anything and
    never interferes with the real inbox pump.

Run from the repo root (reads env.json and wps.db there), e.g.:
  python3 dapps_queue.py
  python3 dapps_queue.py --direction out --limit 20
  python3 dapps_queue.py --json
'''
import argparse
import base64
import json
import sqlite3
import time

import requests


def load_env():
    with open("env.json") as f:
        return json.load(f)


def connect_db(db_filename):
    # mode=ro: this tool only ever reads, and must never take a write lock or interfere
    # with the live server's WAL checkpointing.
    return sqlite3.connect(f"file:{db_filename}?mode=ro", uri=True)


def decode_envelope(raw, is_b64=False):
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw)) if is_b64 else json.loads(raw)
    except Exception:
        return None


def format_age(ts_ms):
    if not ts_ms:
        return "-"
    seconds = max(0, round(time.time() - ts_ms / 1000))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def fetch_outbound(conn, origin):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT o.seq, l.op, l.ts, l.event, o.dapps_ids, o.submitted_at "
            "FROM replication_outbox o JOIN replication_log l ON l.origin = ? AND l.seq = o.seq "
            "ORDER BY o.seq ASC",
            (origin,)
        )
        rows = cur.fetchall()
        cur.execute("SELECT peer, peer_acked_seq, submitted_seq FROM replication_peer_ack")
        peer_status = {peer: (acked_seq, submitted_seq) for peer, acked_seq, submitted_seq in cur.fetchall()}
    except sqlite3.OperationalError:
        # Replication tables don't exist yet - dbInit hasn't run against this db file.
        return None, {}
    return rows, peer_status


def fetch_gap_buffer(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT origin, seq, event FROM replication_pending ORDER BY origin ASC, seq ASC")
        return cur.fetchall()
    except sqlite3.OperationalError:
        return None


def fetch_live_inbound(dapps_url, app_slug):
    resp = requests.get(f"{dapps_url}/AppApi/inbound/{app_slug}", timeout=5)
    resp.raise_for_status()
    return resp.json()


def peer_status_for_seq(seq, peer, dapps_ids, peer_status):
    acked_seq, _submitted_seq = peer_status.get(peer, (0, 0))
    if acked_seq >= seq:
        return "acked"
    if peer in dapps_ids:
        return "submitted"
    return "pending"


def print_outbound(rows, peer_status, peers, limit):
    if rows is None:
        print("=== Outbound queue ===\n  replication_outbox table not found (dbInit hasn't run against this db)\n")
        return
    print(f"=== Outbound queue: {len(rows)} event(s) awaiting peer ack ===")
    if not rows:
        print("  (empty)")
    for seq, op, ts, event_json, dapps_ids_json, _submitted_at in rows[:limit]:
        envelope = decode_envelope(event_json)
        key = envelope.get("key") if envelope else None
        key_str = json.dumps(key, separators=(",", ":")) if key else "-"
        dapps_ids = json.loads(dapps_ids_json) if dapps_ids_json else {}
        statuses = " ".join(f"{peer}={peer_status_for_seq(seq, peer, dapps_ids, peer_status)}" for peer in peers)
        print(f"  seq={seq:<6} op={op:<12} key={key_str:<28} age={format_age(ts):<4} {statuses}")
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more (raise --limit to see them)")
    print()


def print_gap_buffer(rows, limit):
    if rows is None:
        print("=== Inbound gap buffer ===\n  replication_pending table not found (dbInit hasn't run against this db)\n")
        return
    print(f"=== Inbound gap buffer: {len(rows)} event(s) buffered pending resend ===")
    if not rows:
        print("  (empty)")
    for origin, seq, event_json in rows[:limit]:
        envelope = decode_envelope(event_json)
        op = envelope.get("op") if envelope else "?"
        ts = envelope.get("ts") if envelope else None
        print(f"  origin={origin:<10} seq={seq:<6} op={op:<12} age={format_age(ts)}")
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more (raise --limit to see them)")
    print()


def print_live_inbound(messages, limit):
    print(f"=== Live DAPPS inbound queue: {len(messages)} message(s) not yet ack'd ===")
    if not messages:
        print("  (empty)")
    for msg in messages[:limit]:
        envelope = decode_envelope(msg.get("payload"), is_b64=True)
        op = envelope.get("op", "?") if envelope else "?"
        origin = envelope.get("origin", "?") if envelope else "?"
        seq = envelope.get("seq", "?") if envelope else "?"
        source = msg.get("sourceCallsign", "?")
        print(f"  id={msg.get('id')} source={source:<10} op={op:<12} origin={origin} seq={seq}")
    if len(messages) > limit:
        print(f"  ... {len(messages) - limit} more (raise --limit to see them)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Inspect WPS's DAPPS replication queues (read-only).")
    parser.add_argument("--direction", choices=["in", "out", "all"], default="all",
                         help="Which queue(s) to show (default: all)")
    parser.add_argument("--limit", type=int, default=50, help="Max rows per section (default: 50)")
    parser.add_argument("--no-live", action="store_true",
                         help="Skip polling DAPPS's own live inbound queue over REST")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of tables")
    args = parser.parse_args()

    env = load_env()
    replication_config = env.get("replication", {})
    origin = replication_config.get("originCallsign")
    peers = replication_config.get("peers", [])
    app_slug = replication_config.get("appSlug", "wps-repl")
    dapps_url = replication_config.get("dappsRestUrl", "http://127.0.0.1:5000").rstrip("/")

    conn = connect_db(env["dbFilename"])
    result = {}

    if args.direction in ("out", "all"):
        rows, peer_status = fetch_outbound(conn, origin)
        if args.json:
            result["outbound"] = None if rows is None else [
                {
                    "seq": seq, "op": op, "ts": ts,
                    "event": decode_envelope(event_json),
                    "dapps_ids": json.loads(dapps_ids_json) if dapps_ids_json else {},
                    "submitted_at": submitted_at,
                    "peer_status": {
                        peer: peer_status_for_seq(seq, peer, json.loads(dapps_ids_json) if dapps_ids_json else {}, peer_status)
                        for peer in peers
                    },
                }
                for seq, op, ts, event_json, dapps_ids_json, submitted_at in rows
            ]
        else:
            print_outbound(rows, peer_status, peers, args.limit)

    if args.direction in ("in", "all"):
        gap_rows = fetch_gap_buffer(conn)
        if args.json:
            result["inbound_gap_buffer"] = None if gap_rows is None else [
                {"origin": o, "seq": s, "event": decode_envelope(e)} for o, s, e in gap_rows
            ]
        else:
            print_gap_buffer(gap_rows, args.limit)

        if not args.no_live:
            try:
                live_messages = fetch_live_inbound(dapps_url, app_slug)
            except Exception as e:
                live_messages = None
                if args.json:
                    result["live_dapps_inbound_error"] = str(e)
                else:
                    print(f"=== Live DAPPS inbound queue ===\n  Could not reach DAPPS at {dapps_url}: {e}\n")
            if live_messages is not None:
                if args.json:
                    result["live_dapps_inbound"] = live_messages
                else:
                    print_live_inbound(live_messages, args.limit)

    if args.json:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
