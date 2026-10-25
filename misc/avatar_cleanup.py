'''
One-off cleanup of avatar timestamps left by the nightly user sync before it kept the origin's
avatar_last_updated. It used to stamp an avatar it brought in with local time, keeping the
origin's time in avatar_ts, which nothing uses now.

    python3 misc/avatar_cleanup.py list  [--db PATH]
    python3 misc/avatar_cleanup.py clean [--db PATH] [--apply]
    python3 misc/avatar_cleanup.py reset [--db PATH] [--apply] [--resync]

list   every avatar: callsign, avatar_ts, avatar_last_updated, hash. Run it on both nodes and
       compare before a reset, so no avatar held only by the node being reset is lost.
clean  removes avatar_ts, keeping avatar and avatar_last_updated as they are. For MB7NPW. The
       dry run flags any user whose two values differ - an avatar the sync brought in, whose
       avatar_last_updated is the time it arrived.
reset  removes every avatar (avatar, avatar_ts, avatar_last_updated), so the next user sync
       brings them all back from the peer with their original times. For GB7NBH.
       --resync also clears replication_user_sync, so the sync starts at the first reconcile
       tick after a restart rather than waiting for tomorrow's userSyncTime.

Without --apply nothing is written. With it, the database is first copied to
<db>.bak-<YYYYmmdd-HHMMSS>. Stop WPS first, and run the updated code on both nodes before the
next sync. Nothing here is replicated.
'''
import argparse, hashlib, json, os, sqlite3, sys, time

WPS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_db():
    with open(os.path.join(WPS_DIR, 'env.json')) as f:
        return os.path.join(WPS_DIR, json.load(f).get('dbFilename', 'wps.db'))


def avatar_rows(cur):
    cur.execute("""
    SELECT json_extract(user, '$.callsign'), json_extract(user, '$.avatar_ts'),
           json_extract(user, '$.avatar_last_updated'), json_extract(user, '$.avatar')
    FROM users
    WHERE json_extract(user, '$.avatar') IS NOT NULL
       OR json_extract(user, '$.avatar_ts') IS NOT NULL
       OR json_extract(user, '$.avatar_last_updated') IS NOT NULL
    ORDER BY json_extract(user, '$.callsign')
    """)
    return cur.fetchall()


def show(rows, mark=None):
    print(f"{'callsign':<12} {'avatar_ts':<20} {'avatar_last_updated':<20} hash")
    for callsign, ts, last_updated, avatar in rows:
        flag = mark(ts, last_updated, avatar) if mark else ""
        print(f"{callsign or '?':<12} {fmt(ts):<20} {fmt(last_updated):<20} "
              f"{hashlib.sha1(avatar.encode()).hexdigest()[:8] if avatar else '-'}{f'  <- {flag}' if flag else ''}")
    print(f"{len(rows)} user(s)")


def fmt(ts):
    if ts is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def backup(conn, db_path):
    path = f"{db_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    dest = sqlite3.connect(path)
    conn.backup(dest)
    dest.close()
    print(f"Backed up to {path}")


def main():
    parser = argparse.ArgumentParser(description="Clean up avatar timestamps from the nightly user sync")
    parser.add_argument("mode", choices=["list", "clean", "reset"])
    parser.add_argument("--db", help="database file (default: dbFilename from env.json)")
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    parser.add_argument("--resync", action="store_true", help="reset only: run the user sync at the next reconcile tick")
    args = parser.parse_args()

    db_path = args.db or default_db()
    if not os.path.exists(db_path):
        sys.exit(f"No database at {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = avatar_rows(cur)

    if args.mode == "list":
        show(rows)
        return

    if args.mode == "clean":
        def change(ts, last_updated, avatar):
            if ts is None:
                return ""
            return "avatar_ts removed" + (", values differ" if ts != last_updated else "")
        show(rows, change)
        count = sum(1 for _, ts, _, _ in rows if ts is not None)
        differ = sum(1 for _, ts, last_updated, _ in rows if ts is not None and ts != last_updated)
        print(f"{count} avatar_ts value(s) to remove, {differ} of them different from avatar_last_updated")
        if not args.apply or not count:
            print("Dry run - nothing written" if not args.apply else "Nothing to do")
            return
        backup(conn, db_path)
        cur.execute("""
        UPDATE users
        SET user = json_remove(user, '$.avatar_ts')
        WHERE json_extract(user, '$.avatar_ts') IS NOT NULL
        """)
        print(f"Updated {cur.rowcount} user(s)")

    else:  # reset
        show(rows, lambda *_: "removed")
        print(f"{len(rows)} avatar(s) to remove" + ("; replication_user_sync to clear" if args.resync else ""))
        if not args.apply:
            print("Dry run - nothing written")
            return
        backup(conn, db_path)
        cur.execute("""
        UPDATE users
        SET user = json_remove(user, '$.avatar', '$.avatar_ts', '$.avatar_last_updated')
        WHERE json_extract(user, '$.avatar') IS NOT NULL
           OR json_extract(user, '$.avatar_ts') IS NOT NULL
           OR json_extract(user, '$.avatar_last_updated') IS NOT NULL
        """)
        print(f"Removed avatars from {cur.rowcount} user(s)")
        if args.resync:
            cur.execute("DELETE FROM replication_user_sync")
            print(f"Cleared {cur.rowcount} replication_user_sync row(s)")

    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


if __name__ == "__main__":
    main()
