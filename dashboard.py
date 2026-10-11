import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config_editor
import dashboard_auth
import env as env_module
import replication
import replication_dashboard
import wps_status
from env import env, STARTUP_ENV
from state import console_log

# dashboard.py is WPS's web dashboard. It runs as its own process - the wps-dashboard systemd
# service that install_service.sh sets up, or `python3 dashboard.py` - next to WPS rather than
# inside it, so it stays up to start WPS again after stopping it. One HTTP server, on
# dashboard.host:dashboard.port from env.json, serves three pages behind one login, with a
# switcher in the header:
#
# - Config (/): the settings in env.json, editable. A save writes env.json and then warm-reloads
#   WPS - SIGHUP, the same as `systemctl reload wps` - so every setting that can apply live does
#   so at once. The page marks the ones that need a restart, and lists any saved but not yet in
#   effect.
# - Replication (/replication): replication_dashboard.py's read-only view. Shown only when
#   replication is enabled.
# - Service (/service): the WPS service's state, start/stop/restart/reload, and its logs.
#
# What the dashboard knows about the running WPS comes from the status file WPS writes
# (wps_status.py) and from systemd. Start, stop and restart go through
# `sudo -n systemctl --no-block <action> <service>`, which the sudoers rule install_service.sh
# writes allows for exactly those commands. Reload needs no sudo: the dashboard runs as the same
# user as WPS and signals it directly.
#
# When WPS restarts (a new started_at in its status file), the dashboard restarts itself too, so
# the Replication pages - which read replication.py's settings as of this process's start -
# always match the running WPS. It also applies the dashboard's own settings, if they changed.
#
# Login: there is no default password. Until one is set, every page shows a form to set it, and
# the first person to submit it sets it - so open the dashboard as soon as it first starts. It is
# stored hashed in dashboard.passwordHash (see dashboard_auth.py); a session is a cookie signed
# with that hash. To reset a forgotten password, set dashboard.passwordHash to "" in env.json;
# the next visit asks for a new one.
#
# Plain HTTP, no TLS: it's meant for a trusted network. Write requests must be same-origin JSON,
# which together with the SameSite=Strict session cookie stops another site's page from using a
# logged-in browser.

CONFIG = STARTUP_ENV.get('dashboard', {})
MAX_POST_BYTES = 1024 * 1024
STARTED_AT = wps_status.now_ms()
SERVICE = "wps"           # the WPS systemd service; main() takes --service
SHOW_REPLICATION = False  # set by main()
RELOAD_WAIT_SECONDS = 15
LOG_MAX_LINES = 5000

# Failed logins per client address: after LOGIN_MAX_FAILURES within LOGIN_WINDOW_SECONDS,
# further attempts are refused until the oldest failure ages out.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300
_login_failures = {}
_login_lock = threading.Lock()


class ActionError(Exception):
    pass


_hash_cache = {"mtime": None, "hash": ""}


def _password_hash():
    '''
    dashboard.passwordHash, re-read from env.json whenever the file changes - so blanking it by
    hand to reset a forgotten password applies at once, without restarting the dashboard.
    '''
    try:
        mtime = os.stat(config_editor.ENV_PATH).st_mtime_ns
        if mtime != _hash_cache["mtime"]:
            with open(config_editor.ENV_PATH) as f:
                _hash_cache["hash"] = json.load(f).get("dashboard", {}).get("passwordHash", "")
            _hash_cache["mtime"] = mtime
    except (OSError, ValueError, AttributeError):
        pass  # unreadable mid-write or by hand: keep the last good value
    return _hash_cache["hash"] or ""


def _cookie_name():
    # Cookies ignore the port, so two WPS dashboards on one host would otherwise log each other out.
    return f"wps_session_{CONFIG.get('port', 8095)}"


def _throttled(address):
    now = time.time()
    with _login_lock:
        recent = [t for t in _login_failures.get(address, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[address] = recent
        return len(recent) >= LOGIN_MAX_FAILURES


def _record_failure(address):
    with _login_lock:
        _login_failures.setdefault(address, []).append(time.time())


# --- WPS service -------------------------------------------------------------------------

def _run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _systemctl():
    return shutil.which("systemctl")


def _unit_properties(unit):
    '''systemctl show's properties for unit, or None without systemd.'''
    systemctl = _systemctl()
    if not systemctl:
        return None
    try:
        out = _run([systemctl, "show", unit, "--property=LoadState,ActiveState,SubState,MainPID,ActiveEnterTimestamp,MemoryCurrent,NRestarts"]).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _connected_users():
    try:
        conn = sqlite3.connect(f"file:{replication_dashboard.DB_PATH}?mode=ro", uri=True, timeout=5)
        try:
            return conn.execute("SELECT COUNT(*) FROM users WHERE json_extract(user, '$.is_online') = 1").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def service_status():
    props = _unit_properties(SERVICE)
    systemd = bool(props) and props.get("LoadState") == "loaded"
    status = wps_status.running()
    if systemd:
        state, sub_state = props.get("ActiveState", "unknown"), props.get("SubState", "")
    else:
        state, sub_state = ("active", "running") if status else ("inactive", "dead")
    memory = props.get("MemoryCurrent", "") if systemd else ""
    return {
        "wps": {
            "service": SERVICE,
            "systemd": systemd,
            "state": state,
            "sub_state": sub_state,
            "pid": (status or {}).get("pid"),
            "started_at": (status or {}).get("started_at"),
            "since": props.get("ActiveEnterTimestamp") if systemd else None,
            "memory": int(memory) if memory.isdigit() else None,
            "restarts": props.get("NRestarts") if systemd else None,
            "connections": _connected_users() if status else None,
            "last_reload": (status or {}).get("last_reload"),
        },
        "dashboard": {"pid": os.getpid(), "started_at": STARTED_AT, "service": f"{SERVICE}-dashboard"},
        "replication": SHOW_REPLICATION,
    }


def _wps_startup_env():
    status = wps_status.running()
    return status.get("startup_env") if status else None


def reload_wps():
    '''
    Warm-reloads the running WPS (SIGHUP, as `systemctl reload wps` does) and waits for it to
    record the result in its status file. Returns {"errors": [...]}, or {"skipped": reason}, or
    {"timeout": True} if it didn't report back in time.
    '''
    status = wps_status.running()
    if not status:
        return {"skipped": "WPS isn't running - it will use the saved settings when it starts"}
    sent = wps_status.now_ms()
    try:
        os.kill(status["pid"], signal.SIGHUP)
    except PermissionError:
        # WPS runs as another user than the dashboard - fall back on systemd, if sudo allows it
        if not _systemctl() or _run(["sudo", "-n", _systemctl(), "--no-block", "reload", SERVICE]).returncode:
            return {"skipped": "The dashboard isn't allowed to signal WPS. Run it as the same user as WPS, or reload with: sudo systemctl reload " + SERVICE}
    except ProcessLookupError:
        return {"skipped": "WPS isn't running - it will use the saved settings when it starts"}
    deadline = time.monotonic() + RELOAD_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(0.25)
        last = (wps_status.read() or {}).get("last_reload")
        if last and last.get("at", 0) >= sent:
            return {"errors": last.get("errors", [])}
    return {"timeout": True}


def service_action(action):
    if action == "reload":
        return {"reload": reload_wps()}
    if action == "restart-dashboard":
        restart_self(delay=0.5)
        return {"ok": True}
    if action not in ("start", "stop", "restart"):
        raise ActionError(f"Unknown action {action}")
    props = _unit_properties(SERVICE)
    if not props or props.get("LoadState") != "loaded":
        raise ActionError(f"WPS isn't installed as the systemd service '{SERVICE}' here, so the dashboard can't {action} it. "
                          f"Install it with: sudo ./install_service.sh")
    result = _run(["sudo", "-n", _systemctl(), "--no-block", action, SERVICE])
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if "password" in detail or "not allowed" in detail or "sudoers" in detail:
            raise ActionError(f"Not allowed to {action} {SERVICE}: the sudo rule for the dashboard is missing. "
                              f"Re-run sudo ./install_service.sh to add it. ({detail})")
        raise ActionError(f"systemctl {action} {SERVICE} failed: {detail}")
    console_log(f"Dashboard: systemctl {action} {SERVICE}")
    return {"ok": True}


def restart_self(delay=0.0):
    '''Restarts this dashboard process in place (same pid, so systemd doesn't notice).'''
    def go():
        time.sleep(delay)
        console_log("Dashboard restarting")
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=go, daemon=True, name="dashboard_restart").start()


def _follow_wps_restarts():
    '''Restarts the dashboard whenever WPS restarts - see the module comment.'''
    seen = (wps_status.read() or {}).get("started_at")
    while True:
        time.sleep(5)
        status = wps_status.running()
        if status and status.get("started_at") != seen:
            console_log("WPS has restarted - restarting the dashboard to match its settings")
            restart_self()
            return


# --- Logs --------------------------------------------------------------------------------

def _tail(path, lines):
    '''The last `lines` lines of a text file, reading backwards so a large log isn't loaded whole.'''
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = pos = f.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= lines:
            step = min(65536, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    return b"\n".join(data.splitlines()[-lines:]).decode("utf-8", errors="replace"), end


def read_logs(source, lines, query):
    lines = max(10, min(lines or 200, LOG_MAX_LINES))
    note = None
    if source in ("journal", "dashboard-journal"):
        unit = SERVICE if source == "journal" else f"{SERVICE}-dashboard"
        journalctl = shutil.which("journalctl")
        if not journalctl:
            return {"text": "", "note": "journalctl isn't available on this system."}
        try:
            result = _run([journalctl, "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"], timeout=20)
        except subprocess.SubprocessError as e:
            return {"text": "", "note": f"journalctl failed: {e}"}
        text = result.stdout
        if "insufficient permissions" in (result.stderr + text) or (result.returncode and not text.strip()):
            note = ("The dashboard can't read the system journal. install_service.sh gives its service the "
                    "systemd-journal group; if you run it some other way, add its user to that group. " + result.stderr.strip())
    elif source in ("wps.log", "db.log"):
        try:
            text, size = _tail(source, lines)
        except FileNotFoundError:
            return {"text": "", "note": f"{source} doesn't exist yet (or file logging is off in env.json)."}
    else:
        raise ActionError(f"Unknown log source {source}")
    if query:
        q = query.lower()
        text = "\n".join(line for line in text.splitlines() if q in line.lower())
    return {"text": text, "note": note, "lines": lines}


# --- HTTP --------------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "WPSDashboard/1"

    def log_message(self, *_args):
        pass  # keep the console quiet

    # --- responses ---

    def _send(self, status, body, content_type, filename=None, headers=()):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status, result, headers=()):
        self._send(status, json.dumps(result, default=str), "application/json", headers=headers)

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _session_cookie(self, stored):
        return ("Set-Cookie", f"{_cookie_name()}={dashboard_auth.make_session(stored)}; Path=/; HttpOnly; "
                              f"SameSite=Strict; Max-Age={dashboard_auth.SESSION_SECONDS}")

    # --- auth ---

    def _logged_in(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(_cookie_name())
        return dashboard_auth.check_session(_password_hash(), morsel.value if morsel else None)

    def _same_origin_json(self):
        # A cross-site form can't send application/json without a CORS preflight, which this
        # server never grants; a fetch from another site carries its own Origin.
        origin = self.headers.get("Origin")
        return (self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"
                and (not origin or urlparse(origin).netloc == self.headers.get("Host")))

    # --- GET ---

    PAGES = {"/": "config", "/index.html": "config", "/replication": "replication", "/service": "service"}

    def do_GET(self):
        url = urlparse(self.path)
        page = self.PAGES.get(url.path)
        if page:
            if not _password_hash():
                self._send(200, _page(AUTH_PAGE).replace("__MODE__", "setup"), "text/html; charset=utf-8")
            elif not self._logged_in():
                self._send(200, _page(AUTH_PAGE).replace("__MODE__", "login"), "text/html; charset=utf-8")
            elif page == "replication" and not SHOW_REPLICATION:
                self._redirect("/")
            else:
                template = {"config": CONFIG_PAGE, "replication": replication_dashboard.PAGE, "service": SERVICE_PAGE}[page]
                self._send(200, _page(template, page), "text/html; charset=utf-8")
            return

        if not url.path.startswith("/api/"):
            self._send_json(404, {"error": "not found"})
            return
        if not self._logged_in():
            self._send_json(401, {"error": "Not logged in"})
            return
        query = parse_qs(url.query)
        try:
            if url.path == "/api/config":
                self._send_json(200, {**config_editor.load(_wps_startup_env()), "service": service_status()})
            elif url.path == "/api/service/status":
                self._send_json(200, service_status())
            elif url.path == "/api/service/logs":
                lines = replication_dashboard._int_arg(query, "lines", 200)
                self._send_json(200, read_logs(query.get("source", ["journal"])[0], lines, replication_dashboard._str_arg(query, "q")))
            elif not SHOW_REPLICATION:
                self._send_json(404, {"error": "not found"})
            elif url.path == "/api/activity/export":
                conn = replication_dashboard._connect()
                try:
                    body, content_type, filename = replication_dashboard.export_activity(conn.cursor(), query)
                finally:
                    conn.close()
                self._send(200, body, content_type, filename)
            elif url.path in replication_dashboard.ROUTES:
                conn = replication_dashboard._connect()
                try:
                    result = replication_dashboard.ROUTES[url.path](conn.cursor(), query)
                finally:
                    conn.close()
                self._send_json(200, result)
            else:
                self._send_json(404, {"error": "not found"})
        except ActionError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # --- POST ---

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._same_origin_json():
            self._send_json(403, {"error": "Cross-origin request refused"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_POST_BYTES:
            self._send_json(413, {"error": "Request too large"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"error": "Expected a JSON object"})
            return
        handler = {
            "/api/auth/setup": self._post_setup,
            "/api/auth/login": self._post_login,
            "/api/auth/logout": self._post_logout,
            "/api/auth/password": self._post_password,
            "/api/config": self._post_config,
            "/api/service/action": self._post_action,
        }.get(path)
        if handler is None:
            self._send_json(404, {"error": "not found"})
            return
        if path not in ("/api/auth/setup", "/api/auth/login") and not self._logged_in():
            self._send_json(401, {"error": "Not logged in"})
            return
        try:
            handler(body)
        except (config_editor.ConfigError, ActionError) as e:
            self._send_json(getattr(e, "status", 400), {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    _setup_lock = threading.Lock()

    def _post_setup(self, body):
        with self._setup_lock:  # first one wins
            if _password_hash():
                self._send_json(409, {"error": "A password has already been set. Reload the page to log in."})
                return
            stored = config_editor.set_password(body.get("password"))
        console_log(f"Dashboard password set from {self.client_address[0]}")
        self._send_json(200, {"ok": True}, headers=[self._session_cookie(stored)])

    def _post_login(self, body):
        address = self.client_address[0]
        if _throttled(address):
            self._send_json(429, {"error": "Too many failed attempts. Wait a few minutes and try again."})
            return
        stored = _password_hash()
        if not stored or not dashboard_auth.verify_password(str(body.get("password", "")), stored):
            _record_failure(address)
            self._send_json(401, {"error": "Wrong password"})
            return
        self._send_json(200, {"ok": True}, headers=[self._session_cookie(stored)])

    def _post_logout(self, _body):
        self._send_json(200, {"ok": True}, headers=[("Set-Cookie", f"{_cookie_name()}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")])

    def _post_password(self, body):
        address = self.client_address[0]
        if _throttled(address):
            self._send_json(429, {"error": "Too many failed attempts. Wait a few minutes and try again."})
            return
        if not dashboard_auth.verify_password(str(body.get("current", "")), _password_hash()):
            _record_failure(address)
            self._send_json(400, {"error": "The current password is wrong"})
            return
        stored = config_editor.set_password(body.get("new"))
        # The new hash keys sessions, so every other browser is now logged out; keep this one in.
        self._send_json(200, {"ok": True}, headers=[self._session_cookie(stored)])

    def _post_config(self, body):
        result = config_editor.save(body)
        result["reload"] = None
        if result["changed"]:
            try:
                env_module.reload()  # this process's copy, e.g. for a reset password
            except Exception as e:
                console_log(f"ERROR: dashboard failed to re-read env.json: {e}", "ERROR")
            result["reload"] = reload_wps()
        result["restart_pending"] = config_editor.restart_pending(_wps_startup_env())
        result["service"] = service_status()
        self._send_json(200, result)

    def _post_action(self, body):
        self._send_json(200, service_action(str(body.get("action", ""))))


def _page(template, current=None):
    '''Fills the shared stylesheet, header switcher and account buttons into a page template.'''
    links = [("config", "/", "Config")] + ([("replication", "/replication", "Replication")] if SHOW_REPLICATION else []) + [("service", "/service", "Service")]
    app_nav = '<h1>WPS</h1>' + ('<nav class="apps">' + "".join(
        f'<a href="{href}"{" class=active" if key == current else ""}>{label}</a>' for key, href, label in links) + '</nav>' if current else "")
    account = ('<span class="account">' + ('<button class="btn" id="password-open">Password</button>' if current == "config" else "") +
               '<button class="btn" onclick="wpsLogout()">Log out</button></span>' + LOGOUT_SCRIPT) if current else ""
    return (template.replace("__BASE_CSS__", BASE_CSS).replace("__APP_NAV__", app_nav)
            .replace("__ACCOUNT__", account).replace("__FEATURES__", json.dumps({"replication": SHOW_REPLICATION})))


def main(show_replication=None):
    '''
    Runs the dashboard until stopped. Exits straight away if dashboard.enabled is false.
    Options: --service NAME (the WPS systemd service, default wps). show_replication defaults to
    replication.enabled; `python3 replication_dashboard.py` passes True.
    '''
    global SERVICE, SHOW_REPLICATION
    if "--service" in sys.argv:
        SERVICE = sys.argv[sys.argv.index("--service") + 1]
    if not CONFIG.get("enabled", True):
        console_log("Dashboard disabled (dashboard.enabled is false in env.json) - exiting")
        return
    SHOW_REPLICATION = replication.ENABLED if show_replication is None else show_replication
    host = CONFIG.get("host", "0.0.0.0")
    port = CONFIG.get("port", 8095)
    try:
        server = ThreadingHTTPServer((host, port), _Handler)
    except OSError as e:
        console_log(f"Dashboard failed to start on {host}:{port}: {e}", "ERROR")
        sys.exit(1)
    server.daemon_threads = True
    threading.Thread(target=_follow_wps_restarts, daemon=True, name="dashboard_follow_wps").start()
    console_log(f"Dashboard on http://{host}:{port}/ (WPS service: {SERVICE})")
    if not _password_hash():
        console_log(f"Dashboard has no password yet - open http://{host}:{port}/ now to set one", "WARN")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console_log("Dashboard stopped")


BASE_CSS = r""":root {
  --bg: #f6f7f9; --panel: #ffffff; --ink: #1b1f24; --muted: #5f6b7a; --line: #e2e6eb; --hover: #f1f4f8;
  --accent: #2563eb; --ok: #15803d; --ok-bg: #e7f6ec; --warn: #a16207; --warn-bg: #fdf5e0;
  --bad: #b91c1c; --bad-bg: #fdecec; --info: #1d4ed8; --info-bg: #e8effd; --neutral-bg: #eef0f3;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216; --panel: #171b21; --ink: #e5e9ef; --muted: #8f9aa8; --line: #2a3039; --hover: #1e242c;
    --accent: #6ea0ff; --ok: #4ade80; --ok-bg: #12301f; --warn: #facc15; --warn-bg: #332a0c;
    --bad: #f87171; --bad-bg: #3a1616; --info: #93b4ff; --info-bg: #17254a; --neutral-bg: #232932;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px; padding: 12px 20px; background: var(--panel); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 5; }
header h1 { font-size: 16px; margin: 0; font-weight: 600; }
header .ident { color: var(--muted); font-family: var(--mono); font-size: 12px; }
nav { display: flex; gap: 4px; flex-wrap: wrap; }
nav button { border: 0; background: none; color: var(--muted); padding: 6px 12px; border-radius: 6px; cursor: pointer; font: inherit; }
nav button.active { background: var(--neutral-bg); color: var(--ink); font-weight: 600; }
nav button:hover { color: var(--ink); }
.spacer { flex: 1; }
.refresh { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; }
main { padding: 20px; max-width: 1500px; margin: 0 auto; }
section.view { display: none; } section.view.active { display: block; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 20px; overflow: hidden; }
.card h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0; padding: 12px 16px; border-bottom: 1px solid var(--line); font-weight: 600; }
.card .body { padding: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 20px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.tile .label { color: var(--muted); font-size: 12px; }
.tile .value { font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; margin-top: 2px; }
.tile .sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); vertical-align: top; white-space: nowrap; }
th { font-size: 12px; color: var(--muted); font-weight: 600; background: var(--panel); position: sticky; top: 0; }
td.wrap { white-space: normal; min-width: 240px; }
tbody tr.clickable { cursor: pointer; } tbody tr.clickable:hover { background: var(--hover); }
tbody tr:last-child td { border-bottom: 0; }
.mono { font-family: var(--mono); font-size: 12.5px; }
.muted { color: var(--muted); }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 500; background: var(--neutral-bg); color: var(--muted); }
.pill.ok { background: var(--ok-bg); color: var(--ok); }
.pill.warn { background: var(--warn-bg); color: var(--warn); }
.pill.bad { background: var(--bad-bg); color: var(--bad); }
.pill.info { background: var(--info-bg); color: var(--info); }
.dir { font-family: var(--mono); font-size: 12px; font-weight: 600; }
.dir.in { color: var(--ok); } .dir.out { color: var(--info); }
.filters { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--line); align-items: center; }
select, input[type=text], input[type=search] { font: inherit; padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--ink); }
button.btn { font: inherit; padding: 5px 12px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--ink); cursor: pointer; }
button.btn:hover { background: var(--hover); }
.more { padding: 12px 16px; text-align: center; }
.empty { padding: 24px 16px; color: var(--muted); text-align: center; }
.notice { padding: 12px 16px; background: var(--warn-bg); color: var(--warn); border-radius: 8px; margin-bottom: 20px; }
.notice.bad { background: var(--bad-bg); color: var(--bad); }
.peerstates { display: flex; gap: 4px; flex-wrap: wrap; }
dl.kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0; }
dl.kv dt { color: var(--muted); } dl.kv dd { margin: 0; font-family: var(--mono); font-size: 12.5px; word-break: break-all; }
#drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(720px, 100vw); background: var(--panel); border-left: 1px solid var(--line); box-shadow: -8px 0 24px rgba(0,0,0,.12); transform: translateX(100%); transition: transform .18s ease; z-index: 20; display: flex; flex-direction: column; }
#drawer.open { transform: none; }
#drawer .head { display: flex; align-items: center; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--line); }
#drawer .head h3 { margin: 0; font-size: 15px; flex: 1; }
#drawer .content { padding: 18px; overflow-y: auto; flex: 1; }
#drawer h4 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 22px 0 8px; }
#drawer h4:first-child { margin-top: 0; }
pre.json { background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 12px; overflow: auto; font: 12.5px/1.5 var(--mono); margin: 0; white-space: pre-wrap; word-break: break-word; }
.timeline td { font-size: 13px; }
a.link { color: var(--accent); cursor: pointer; text-decoration: none; } a.link:hover { text-decoration: underline; }
dialog#dl-dialog { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); color: var(--ink); padding: 0; width: min(420px, calc(100vw - 32px)); box-shadow: 0 12px 32px rgba(0,0,0,.2); }
dialog#dl-dialog::backdrop { background: rgba(0,0,0,.35); }
dialog#dl-dialog h3 { margin: 0; padding: 14px 18px; font-size: 15px; border-bottom: 1px solid var(--line); }
dialog#dl-dialog .content { padding: 16px 18px; display: grid; gap: 12px; }
dialog#dl-dialog .opt { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
dialog#dl-dialog input[type=number], dialog#dl-dialog input[type=datetime-local] { font: inherit; padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--ink); }
dialog#dl-dialog input[type=number] { width: 7em; }
dialog#dl-dialog .actions { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 18px; border-top: 1px solid var(--line); }
button.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.btn.primary:hover { filter: brightness(1.08); background: var(--accent); }
@media (max-width: 700px) { main { padding: 12px; } header { padding: 10px 12px; } }
nav.apps { gap: 0; padding: 2px; background: var(--neutral-bg); border-radius: 8px; }
nav.apps a { color: var(--muted); text-decoration: none; padding: 5px 14px; border-radius: 6px; font-weight: 500; }
nav.apps a:hover { color: var(--ink); }
nav.apps a.active { background: var(--panel); color: var(--ink); font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,.08); }
.account { display: flex; gap: 8px; }
header .subbar { flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 20px; margin: 0 -8px -4px; }
[hidden] { display: none !important; }
.notice.ok { background: var(--ok-bg); color: var(--ok); }
.notice.info { background: var(--info-bg); color: var(--info); }
.notice ul { margin: 6px 0 0; padding-left: 20px; }
.status-line { color: var(--muted); font-size: 13px; margin: 0 0 16px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; color: var(--muted); font-size: 12px; margin: 0 0 16px; }
.cfg-grid { display: grid; grid-template-columns: minmax(220px, 2fr) 3fr; }
.cfg-row { display: contents; }
.cfg-row > div { min-width: 0; padding: 10px 16px; border-bottom: 1px solid var(--line); }
.cfg-row:last-child > div { border-bottom: 0; }
.cfg-row.dirty > div:first-child { box-shadow: inset 3px 0 0 var(--accent); }
.cfg-key { font-family: var(--mono); font-size: 12.5px; font-weight: 600; word-break: break-all; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.cfg-help { color: var(--muted); font-size: 12px; margin-top: 2px; }
.cfg-field input[type=text], .cfg-field input[type=password], .cfg-field input[type=number], .cfg-field select, .cfg-field textarea {
  font: inherit; padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--ink); width: 100%; max-width: 520px; }
.cfg-field textarea { font: 12.5px/1.5 var(--mono); min-height: 64px; resize: vertical; }
.cfg-field textarea.json { min-height: 140px; }
.cfg-field .err { color: var(--bad); font-size: 12px; margin-top: 4px; }
.cfg-field .invalid { border-color: var(--bad); }
.cfg-field .sub { color: var(--muted); font-size: 12px; margin-top: 4px; display: flex; gap: 12px; flex-wrap: wrap; }
.cfg-bar { position: sticky; bottom: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 12px 16px; margin-top: -8px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 -4px 16px rgba(0,0,0,.06); }
.cfg-bar .summary { flex: 1; min-width: 200px; color: var(--muted); font-size: 13px; }
dialog.modal { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); color: var(--ink); padding: 0; width: min(420px, calc(100vw - 32px)); box-shadow: 0 12px 32px rgba(0,0,0,.2); }
dialog.modal::backdrop { background: rgba(0,0,0,.35); }
dialog.modal h3 { margin: 0; padding: 14px 18px; font-size: 15px; border-bottom: 1px solid var(--line); }
dialog.modal .content, .auth .content { padding: 16px 18px; display: grid; gap: 12px; }
dialog.modal .actions, .auth .actions { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 18px; border-top: 1px solid var(--line); }
.field { display: grid; gap: 4px; font-size: 13px; }
.field input { font: inherit; padding: 7px 9px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--ink); }
.form-error { color: var(--bad); font-size: 13px; min-height: 1em; }
.auth { max-width: 400px; margin: 12vh auto 0; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
.auth h1 { margin: 0; padding: 16px 18px; font-size: 17px; border-bottom: 1px solid var(--line); }
.auth p { margin: 0; color: var(--muted); font-size: 13px; }
.list-editor, .obj-list { display: grid; gap: 6px; max-width: 520px; margin-bottom: 6px; }
.list-row { display: flex; gap: 6px; align-items: center; }
.list-row input { flex: 1; min-width: 0; }
.obj-item { display: flex; gap: 8px; align-items: flex-start; padding: 8px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); }
.obj-fields { flex: 1; min-width: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 6px 8px; }
.obj-field { display: grid; gap: 2px; font-size: 12px; color: var(--muted); font-family: var(--mono); }
.cfg-field .obj-field input { max-width: none; background: var(--panel); }
button.btn.small { padding: 3px 10px; font-size: 13px; }
button.btn.icon { padding: 2px 9px; font-size: 16px; line-height: 1.2; }
button.btn:disabled { opacity: .5; cursor: default; }
.svc-state { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
.svc-state .pill { font-size: 13px; padding: 2px 10px; }
.svc-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.svc-help { font-size: 12px; margin: 12px 0 0; }
pre.log { margin: 0; padding: 12px 16px; max-height: 65vh; overflow: auto; font: 12px/1.5 var(--mono); white-space: pre; background: var(--bg); }
.log-note { margin: 12px 16px; }
@media (max-width: 700px) {
  .cfg-grid { grid-template-columns: minmax(0, 1fr); }
  .cfg-row > div:first-child { border-bottom: 0; padding-bottom: 2px; }
  .auth { margin: 24px 16px 0; }
}
"""

LOGOUT_SCRIPT = r"""<script>
function wpsLogout() {
  fetch("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(() => location.href = "/");
}
</script>"""

AUTH_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WPS Dashboard</title>
<style>
__BASE_CSS__</style>
</head>
<body>
<form class="auth" id="form">
  <h1 id="heading"></h1>
  <div class="content">
    <p id="intro"></p>
    <label class="field">Password <input type="password" id="password" autocomplete="current-password" required autofocus></label>
    <label class="field" id="confirm-row">Confirm password <input type="password" id="confirm" autocomplete="new-password"></label>
    <div class="form-error" id="error"></div>
  </div>
  <div class="actions"><button class="btn primary" id="submit"></button></div>
</form>
<script>
const MODE = "__MODE__";
const $ = (id) => document.getElementById(id);
const setup = MODE === "setup";
$("heading").textContent = setup ? "Set a dashboard password" : "WPS Dashboard";
$("intro").textContent = setup
  ? "This dashboard has no password yet. Choose one now (at least 8 characters). Anyone who can open this page before you can set it, so don't leave it unset."
  : "Enter the dashboard password.";
$("submit").textContent = setup ? "Set password" : "Log in";
$("confirm-row").hidden = !setup;
if (setup) $("password").autocomplete = "new-password";
$("form").onsubmit = async (e) => {
  e.preventDefault();
  $("error").textContent = "";
  const password = $("password").value;
  if (setup && password !== $("confirm").value) { $("error").textContent = "The passwords don't match"; return; }
  $("submit").disabled = true;
  try {
    const res = await fetch(setup ? "/api/auth/setup" : "/api/auth/login",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.statusText);
    location.reload();  // same URL, now with a session - e.g. back to /replication#activity
  } catch (err) {
    $("error").textContent = err.message;
    $("submit").disabled = false;
  }
};
</script>
</body>
</html>
"""

CONFIG_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WPS Config</title>
<style>
__BASE_CSS__</style>
</head>
<body>
<header>
  __APP_NAV__
  <span class="spacer"></span>
  __ACCOUNT__
</header>

<main>
  <p class="status-line" id="status-line"></p>
  <div id="cfg-notices"></div>
  <div class="legend">
    When a change applies:
    <span><span class="pill ok">live</span> when you save</span>
    <span><span class="pill warn">restart</span> after WPS restarts</span>
    <span><span class="pill info">dashboard</span> after the dashboard restarts</span>
    <span><span class="pill">script</span> read by a separate script</span>
  </div>
  <div id="cfg-sections"></div>
  <div class="cfg-bar" id="cfg-bar" hidden>
    <span class="summary" id="cfg-summary">No changes</span>
    <button class="btn" id="cfg-discard" disabled>Discard</button>
    <button class="btn primary" id="cfg-save" disabled>Save and apply</button>
  </div>
</main>

<dialog class="modal" id="password-dialog">
  <form id="password-form">
    <h3>Change dashboard password</h3>
    <div class="content">
      <label class="field">Current password <input type="password" id="pw-current" autocomplete="current-password" required></label>
      <label class="field">New password <input type="password" id="pw-new" autocomplete="new-password" required minlength="8"></label>
      <label class="field">Confirm new password <input type="password" id="pw-confirm" autocomplete="new-password" required></label>
      <div class="form-error" id="pw-error"></div>
    </div>
    <div class="actions">
      <button class="btn" type="button" id="pw-cancel">Cancel</button>
      <button class="btn primary" id="pw-save">Change password</button>
    </div>
  </form>
</dialog>

<script>
const FEATURES = __FEATURES__;
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const codeList = (paths) => paths.map((p) => `<span class="mono">${esc(p)}</span>`).join(", ");

// Old bookmarks: the Replication dashboard used to be served at / with its tab in the hash.
if (FEATURES.replication && /^#(overview|activity|received|sent|pending|item\/)/.test(location.hash)) location.replace("/replication" + location.hash);

async function api(path, options) {
  const res = await fetch(path, options);
  if (res.status === 401) { location.reload(); throw new Error("Logged out"); }
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}
const postJson = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---------- Config ----------
// Edits env.json through /api/config. Fields are generated from the file itself, so a key added
// to env.py's template shows up here without changing this page. Secrets arrive blank: typing a
// value replaces the stored one, "clear" empties it, leaving it blank keeps it.
let cfg = null;          // last /api/config response
let cfgNotice = "";      // survives re-render, e.g. the result of a save

const pathOf = (prefix, key) => prefix ? `${prefix}.${key}` : key;
function setPath(obj, path, value) { const ks = path.split("."), leaf = ks.pop(); ks.reduce((o, k) => o[k], obj)[leaf] = value; }
const appliesOf = (path) => cfg.meta.applies[path] || "restart";
const isNumberList = (path) => cfg.meta.number_lists.includes(path);

function appliesPill(path) {
  const a = appliesOf(path);
  const pending = cfg.restart_pending.includes(path) ? ` <span class="pill bad" title="Saved, but not in effect until the restart">restart pending</span>` : "";
  if (a === "live") return `<span class="pill ok" title="Applied as soon as you save">live</span>`;
  if (a === "script") return `<span class="pill" title="${esc(cfg.meta.script[path])}">script</span>`;
  if (a === "dashboard") return `<span class="pill info" title="Takes effect when the dashboard restarts (Service page)">dashboard</span>` + pending;
  return `<span class="pill warn" title="Takes effect after WPS restarts">restart</span>` + pending;
}

function fieldKind(path, value) {
  const m = cfg.meta;
  if (path in cfg.secrets) return "secret";
  if (m.choices[path]) return "choice";
  if (path in m.item_templates)
    return Array.isArray(value) && value.every((v) => v && typeof v === "object" && !Array.isArray(v) && Object.values(v).every((x) => typeof x === "string")) ? "objects" : "json";
  if (path in m.nullable) return m.nullable[path] === "number" ? "number" : "text";
  if (typeof value === "boolean") return "bool";
  if (typeof value === "number") return "number";
  if (typeof value === "string") return "text";
  if (Array.isArray(value)) return value.every((v) => v === null || typeof v !== "object") ? "list" : "json";
  return "json";
}

// ----- list editors: one input per item, with add and remove -----
const removeButton = `<button type="button" class="btn icon" data-remove title="Remove" aria-label="Remove">&times;</button>`;
function listRow(path, v) {
  return `<div class="list-row"><input type="${isNumberList(path) ? "number" : "text"}" step="any" value="${esc(v ?? "")}" spellcheck="false">${removeButton}</div>`;
}
function objectItem(path, item, isNew) {
  // An existing item keeps its own key order (so an untouched item compares equal); fields only
  // the template has are added after, and only saved if filled in.
  const own = isNew ? [] : Object.keys(item);
  const keys = [...own, ...Object.keys(cfg.meta.item_templates[path]).filter((k) => !own.includes(k))];
  const required = cfg.meta.item_required[path] || [];
  return `<div class="obj-item"><div class="obj-fields">` + keys.map((k) =>
    `<label class="obj-field"><span>${esc(k)}${required.includes(k) ? " *" : ""}</span><input type="text" data-key="${esc(k)}"${own.includes(k) || isNew ? " data-keep" : ""} value="${esc(item[k] ?? "")}" spellcheck="false"></label>`
  ).join("") + `</div><button type="button" class="btn" data-remove>Remove</button></div>`;
}
const itemNoun = (path) => ({ "apps": "app", "replication.peers": "peer" }[path] || "item");

function fieldHtml(path, value, kind) {
  const id = esc(`cfg:${path}`);
  if (kind === "bool") return `<input type="checkbox" id="${id}"${value ? " checked" : ""}>`;
  if (kind === "choice") return `<select id="${id}">${[...new Set([value, ...cfg.meta.choices[path]])].map((c) => `<option${c === value ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  if (kind === "number") return `<input type="number" step="any" id="${id}" value="${esc(value ?? "")}"${path in cfg.meta.nullable ? ` placeholder="empty = null"` : ""}>`;
  if (kind === "text") return `<input type="text" id="${id}" value="${esc(value)}" spellcheck="false">`;
  if (kind === "list") return `<div class="list-editor" id="${id}">${value.map((v) => listRow(path, v)).join("")}</div>` +
    `<button type="button" class="btn small" data-add="${esc(path)}">+ Add</button>`;
  if (kind === "objects") return `<div class="obj-list" id="${id}">${value.map((item) => objectItem(path, item, false)).join("")}</div>` +
    `<button type="button" class="btn small" data-add="${esc(path)}">+ Add ${itemNoun(path)}</button>`;
  if (kind === "json") return `<textarea class="json" id="${id}" spellcheck="false">${esc(JSON.stringify(value, null, 2))}</textarea>`;
  // secret
  const isSet = cfg.secrets[path];
  return `<input type="password" id="${id}" autocomplete="new-password" placeholder="${isSet ? "set - leave blank to keep" : "not set"}">` +
    (isSet ? `<div class="sub"><label><input type="checkbox" id="${id}:clear"> Clear the stored value</label></div>` : "");
}

// Returns {value} or {error}. For secrets, value is undefined when unchanged.
function readField(path, kind, original) {
  const el = document.getElementById(`cfg:${path}`);
  if (kind === "bool") return { value: el.checked };
  if (kind === "choice") return { value: el.value };
  if (kind === "text") return { value: el.value };
  if (kind === "number") {
    if (el.value.trim() === "") return path in cfg.meta.nullable ? { value: null } : { error: "A number is required" };
    const n = Number(el.value);
    if (!Number.isFinite(n)) return { error: "Not a number" };
    if (cfg.meta.ports.includes(path) && !(Number.isInteger(n) && n >= 1 && n <= 65535)) return { error: "Port must be 1-65535" };
    return { value: n };
  }
  if (kind === "list") {
    // Blank rows are ignored, so "+ Add" and leaving it empty changes nothing.
    const items = [...el.querySelectorAll(".list-row input")].map((i) => i.value.trim()).filter(Boolean);
    if (!isNumberList(path)) return { value: items };
    const value = items.map(Number);
    if (value.some((n) => !Number.isFinite(n))) return { error: "Every item must be a number" };
    return { value };
  }
  if (kind === "objects") {
    const required = cfg.meta.item_required[path] || [];
    const value = [];
    for (const item of el.querySelectorAll(".obj-item")) {
      const obj = {};
      item.querySelectorAll("input[data-key]").forEach((i) => { const v = i.value.trim(); if (v || i.hasAttribute("data-keep")) obj[i.dataset.key] = v; });
      if (Object.values(obj).every((v) => !v)) continue;  // an empty new item
      const missing = required.filter((k) => !obj[k]);
      if (missing.length) return { error: `${itemNoun(path)} ${value.length + 1} needs ${missing.join(", ")}` };
      value.push(obj);
    }
    return { value };
  }
  if (kind === "json") {
    try {
      const value = JSON.parse(el.value);
      if (Array.isArray(original) && !Array.isArray(value)) return { error: "Must be a JSON array [...]" };
      return { value };
    } catch (e) { return { error: `Invalid JSON: ${e.message}` }; }
  }
  const clear = document.getElementById(`cfg:${path}:clear`);
  if (clear?.checked) return { value: "" };
  return { value: el.value === "" ? undefined : el.value };
}

function cfgSections() {
  // [{prefix, fields: [{path, key, value, kind}]}]: top-level scalars first, then each object.
  const sections = [];
  (function walk(node, prefix) {
    const fields = [], children = [];
    Object.entries(node).forEach(([k, v]) => {
      const path = pathOf(prefix, k);
      if (v && typeof v === "object" && !Array.isArray(v)) children.push([v, path]);
      else fields.push({ path, key: k, value: v, kind: fieldKind(path, v) });
    });
    if (fields.length) sections.push({ prefix, fields });
    children.forEach(([v, p]) => walk(v, p));
  })(cfg.config, "");
  return sections;
}
const cfgFields = () => cfgSections().flatMap((s) => s.fields);

async function loadConfig() {
  cfg = await api("/api/config");
  renderConfig();
}

function renderConfig() {
  $("cfg-sections").innerHTML = cfgSections().map(({ prefix, fields }) => {
    const title = cfg.meta.sections[prefix] || prefix;
    return `<div class="card"><h2>${esc(title)}${prefix ? ` <span class="mono" style="text-transform:none;letter-spacing:0">${esc(prefix)}</span>` : ""}</h2><div class="cfg-grid">` +
      fields.map((f) => `<div class="cfg-row" data-path="${esc(f.path)}"><div><div class="cfg-key">${esc(f.key)} ${appliesPill(f.path)}</div>` +
        `<div class="cfg-help">${esc(cfg.meta.help[f.path] || "")}${appliesOf(f.path) === "script" ? ` <i>Used by ${esc(cfg.meta.script[f.path])}.</i>` : ""}</div></div>` +
        `<div class="cfg-field">${fieldHtml(f.path, f.value, f.kind)}<div class="err"></div></div></div>`).join("") +
      `</div></div>`;
  }).join("");
  $("cfg-bar").hidden = false;
  renderStatus();
  updateCfgBar();
}

// Rows come and go, so input and the add/remove buttons are handled once, on the container.
$("cfg-sections").addEventListener("input", updateCfgBar);
$("cfg-sections").addEventListener("change", updateCfgBar);
$("cfg-sections").addEventListener("click", (e) => {
  const add = e.target.closest("[data-add]"), remove = e.target.closest("[data-remove]");
  if (add) {
    const path = add.dataset.add, list = document.getElementById(`cfg:${path}`);
    list.insertAdjacentHTML("beforeend", path in cfg.meta.item_templates ? objectItem(path, cfg.meta.item_templates[path], true) : listRow(path, ""));
    list.lastElementChild.querySelector("input").focus();
  } else if (remove) {
    remove.closest(".list-row, .obj-item").remove();
  } else return;
  updateCfgBar();
});

function wpsSummary(w) {
  if (w.state === "active" && w.started_at) return `WPS running since ${esc(new Date(w.started_at).toLocaleString())}` +
    (w.connections != null ? ` · ${w.connections} user${w.connections === 1 ? "" : "s"} connected` : "");
  if (w.state === "active") return "WPS running";
  return `WPS is <b>${w.state === "failed" ? "failed" : "stopped"}</b> - saved settings apply when it starts (<a class="link" href="/service">Service</a>)`;
}

function renderStatus() {
  const w = cfg.service.wps;
  $("status-line").innerHTML = `${wpsSummary(w)} · editing <span class="mono" style="word-break:break-all">${esc(cfg.path)}</span>`;
  const notes = [];
  if (cfgNotice) notes.push(cfgNotice);
  const restart = cfg.restart_pending.filter((p) => appliesOf(p) === "restart"), dash = cfg.restart_pending.filter((p) => appliesOf(p) === "dashboard");
  if (restart.length) notes.push(`<div class="notice">Saved, but not in effect until WPS restarts (<a class="link" href="/service">Service</a>): ${codeList(restart)}.</div>`);
  if (dash.length) notes.push(`<div class="notice">Saved, but not in effect until the dashboard restarts (<a class="link" href="/service">Service</a>): ${codeList(dash)}.</div>`);
  $("cfg-notices").innerHTML = notes.join("");
}

// Paths whose field differs from what was loaded, plus any field errors.
function cfgDirty() {
  if (!cfg || !$("cfg-sections").firstChild) return [];
  const dirty = [];
  cfgFields().forEach((f) => {
    const r = readField(f.path, f.kind, f.value);
    const row = document.querySelector(`.cfg-row[data-path="${CSS.escape(f.path)}"]`);
    row.querySelector(".err").textContent = r.error || "";
    document.getElementById(`cfg:${f.path}`).classList.toggle("invalid", !!r.error);
    const changed = !!r.error || (f.kind === "secret" ? r.value !== undefined : JSON.stringify(r.value) !== JSON.stringify(f.value));
    row.classList.toggle("dirty", changed);
    if (changed) dirty.push({ path: f.path, error: r.error });
  });
  return dirty;
}

function updateCfgBar() {
  const dirty = cfgDirty(), errors = dirty.filter((d) => d.error);
  const restart = dirty.filter((d) => ["restart", "dashboard"].includes(appliesOf(d.path)));
  $("cfg-summary").textContent = errors.length ? `${errors.length} field${errors.length > 1 ? "s" : ""} to fix: ${errors.map((d) => d.path).join(", ")}`
    : dirty.length ? `${dirty.length} unsaved change${dirty.length > 1 ? "s" : ""}: ${dirty.map((d) => d.path).join(", ")}` +
        (restart.length ? ` (${restart.length} need${restart.length === 1 ? "s" : ""} a restart)` : "")
    : "No changes";
  $("cfg-save").disabled = !dirty.length || errors.length > 0;
  $("cfg-discard").disabled = !dirty.length;
}

function describeSave(res) {
  if (!res.changed.length) return `<div class="notice">Nothing to save - env.json already has these values.</div>`;
  const items = [], r = res.reload || {}, errors = r.errors || [];
  if (res.live.length) items.push(
    r.errors && !errors.length ? `<li>Applied now: ${codeList(res.live)}</li>`
    : r.errors ? `<li>Saved, but the reload reported problems, so these may not have applied: ${codeList(res.live)}</li>`
    : r.timeout ? `<li>Saved; WPS was sent a reload but didn't confirm it in time - check its log (<a class="link" href="/service">Service</a>): ${codeList(res.live)}</li>`
    : `<li>Saved: ${codeList(res.live)}. ${esc(r.skipped || "")}</li>`);
  if (res.restart.length) items.push(`<li><b>Restart WPS</b> to apply: ${codeList(res.restart)}</li>`);
  if (res.dashboard.length) items.push(`<li><b>Restart the dashboard</b> to apply: ${codeList(res.dashboard)}</li>`);
  if (res.script.length) items.push(`<li>Read by a separate script (see each setting): ${codeList(res.script)}</li>`);
  if (errors.length) items.push(`<li>Reload errors: ${errors.map(esc).join("; ")}</li>`);
  return `<div class="notice ${errors.length ? "bad" : "ok"}">Saved ${res.changed.length} change${res.changed.length > 1 ? "s" : ""} to env.json (previous version kept as env.json.bak).<ul>${items.join("")}</ul></div>`;
}

async function saveConfig() {
  const config = structuredClone(cfg.config), secrets = {};
  for (const f of cfgFields()) {
    const r = readField(f.path, f.kind, f.value);
    if (r.error) return updateCfgBar();
    if (f.kind === "secret") { if (r.value !== undefined) secrets[f.path] = r.value; }
    else setPath(config, f.path, r.value);
  }
  $("cfg-save").disabled = true;
  $("cfg-summary").textContent = "Saving and applying…";
  try {
    const res = await postJson("/api/config", { config, secrets, mtime: cfg.mtime });
    cfgNotice = describeSave(res);
  } catch (e) {
    cfgNotice = `<div class="notice bad">Not saved: ${esc(e.message)}</div>`;
    renderStatus(); updateCfgBar();
    return;
  }
  await loadConfig();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$("cfg-save").onclick = saveConfig;
$("cfg-discard").onclick = () => { cfgNotice = ""; renderConfig(); };
window.addEventListener("beforeunload", (e) => { if (cfgDirty().length) e.preventDefault(); });

// ---------- Password ----------
$("password-open").onclick = () => { $("password-form").reset(); $("pw-error").textContent = ""; $("password-dialog").showModal(); };
$("pw-cancel").onclick = () => $("password-dialog").close();
$("password-form").onsubmit = async (e) => {
  e.preventDefault();
  if ($("pw-new").value !== $("pw-confirm").value) { $("pw-error").textContent = "The new passwords don't match"; return; }
  try {
    await postJson("/api/auth/password", { current: $("pw-current").value, new: $("pw-new").value });
    $("password-dialog").close();
    cfgNotice = `<div class="notice ok">Password changed. Other browsers have been logged out.</div>`;
    renderStatus();
  } catch (err) { $("pw-error").textContent = err.message; }
};

loadConfig().catch((e) => { $("cfg-notices").innerHTML = `<div class="notice bad">Couldn't load env.json: ${esc(e.message)}</div>`; });
</script>
</body>
</html>
"""

SERVICE_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WPS Service</title>
<style>
__BASE_CSS__</style>
</head>
<body>
<header>
  __APP_NAV__
  <span class="spacer"></span>
  __ACCOUNT__
</header>

<main>
  <div id="svc-notices"></div>

  <div class="card">
    <h2>WPS</h2>
    <div class="body">
      <div class="svc-state"><span id="wps-state"></span><span class="muted" id="wps-detail"></span></div>
      <dl class="kv" id="wps-kv"></dl>
      <div class="svc-actions">
        <button class="btn" data-action="start">Start</button>
        <button class="btn" data-action="stop">Stop</button>
        <button class="btn" data-action="restart">Restart</button>
        <button class="btn" data-action="reload">Reload</button>
      </div>
      <p class="muted svc-help">
        <b>Reload</b> re-reads env.json and warm-reloads WPS's code without disconnecting anyone (the same as <span class="mono">systemctl reload</span>).
        <b>Restart</b> and <b>Stop</b> disconnect every connected user.
      </p>
    </div>
  </div>

  <div class="card">
    <h2>Dashboard</h2>
    <div class="body">
      <dl class="kv" id="dash-kv"></dl>
      <div class="svc-actions"><button class="btn" data-action="restart-dashboard">Restart dashboard</button></div>
      <p class="muted svc-help">Applies the dashboard's own settings (its address and on/off switch). It also restarts by itself whenever WPS does.</p>
    </div>
  </div>

  <div class="card">
    <h2>Logs</h2>
    <div class="filters">
      <select id="log-source">
        <option value="journal">journalctl: WPS service</option>
        <option value="wps.log">wps.log</option>
        <option value="db.log">db.log</option>
        <option value="dashboard-journal">journalctl: dashboard service</option>
      </select>
      <select id="log-lines"><option>100</option><option selected>200</option><option>500</option><option>1000</option><option>5000</option></select>
      <input type="search" id="log-q" placeholder="Filter the lines shown">
      <label class="muted"><input type="checkbox" id="log-follow"> Follow (5s)</label>
      <span class="spacer"></span>
      <button class="btn" id="log-refresh">Refresh</button>
    </div>
    <div class="notice log-note" id="log-note" hidden></div>
    <pre class="log" id="log"></pre>
  </div>
</main>

<dialog class="modal" id="confirm-dialog">
  <form method="dialog">
    <h3 id="confirm-title"></h3>
    <div class="content"><p id="confirm-text" style="margin:0"></p></div>
    <div class="actions">
      <button class="btn" value="cancel" formnovalidate>Cancel</button>
      <button class="btn primary" id="confirm-go" value="ok"></button>
    </div>
  </form>
</dialog>

<script>
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options) {
  const res = await fetch(path, options);
  if (res.status === 401) { location.reload(); throw new Error("Logged out"); }
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}
const postJson = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const STATES = { active: ["running", "ok"], reloading: ["reloading", "info"], activating: ["starting", "info"], deactivating: ["stopping", "info"],
  inactive: ["stopped", ""], failed: ["failed", "bad"] };
const mb = (bytes) => bytes == null ? "—" : `${(bytes / 1048576).toFixed(1)} MB`;
const fmt = (ms) => ms ? new Date(ms).toLocaleString() : "—";
let svc = null, notice = "";

function showNotice() { $("svc-notices").innerHTML = notice; }

async function loadStatus() {
  svc = await api("/api/service/status");
  const w = svc.wps, [label, cls] = STATES[w.state] || [w.state, ""];
  $("wps-state").innerHTML = `<span class="pill ${cls}">${esc(label)}</span>`;
  $("wps-detail").textContent = w.systemd ? `systemd service ${w.service}` : `not running as a systemd service here - only Reload works`;
  const lr = w.last_reload;
  $("wps-kv").innerHTML = [
    ["Running since", w.state === "active" ? fmt(w.started_at) : "—"],
    ["Process id", w.pid ?? "—"],
    ["Connected users", w.connections ?? "—"],
    ...(w.systemd ? [["Memory", mb(w.memory)], ["Automatic restarts", w.restarts ?? "—"]] : []),
    ["Last reload", lr ? `${fmt(lr.at)} - ${lr.errors.length ? `errors: ${lr.errors.join("; ")}` : "OK"}` : "none since it started"],
  ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  const running = ["active", "reloading"].includes(w.state);
  document.querySelector('[data-action="start"]').disabled = !w.systemd || running || w.state === "activating";
  document.querySelector('[data-action="stop"]').disabled = !w.systemd || !running;
  document.querySelector('[data-action="restart"]').disabled = !w.systemd || !running;
  document.querySelector('[data-action="reload"]').disabled = !w.pid;
  const d = svc.dashboard;
  $("dash-kv").innerHTML = [["Running since", fmt(d.started_at)], ["Process id", d.pid], ["Service", d.service]]
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
}

async function loadLogs() {
  const params = new URLSearchParams({ source: $("log-source").value, lines: $("log-lines").value, q: $("log-q").value });
  const log = $("log"), atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  try {
    const res = await api("/api/service/logs?" + params);
    $("log-note").hidden = !res.note;
    $("log-note").textContent = res.note || "";
    log.textContent = res.text || "(no lines)";
  } catch (e) {
    $("log-note").hidden = false;
    $("log-note").textContent = e.message;
  }
  if (atBottom) log.scrollTop = log.scrollHeight;
}

const CONFIRM = {
  stop: ["Stop WPS?", "Every connected user is disconnected, and nobody can connect until you start it again.", "Stop WPS"],
  restart: ["Restart WPS?", "Every connected user is disconnected while WPS restarts. Clients reconnect afterwards.", "Restart WPS"],
  "restart-dashboard": ["Restart the dashboard?", "This page reloads when it's back, in a few seconds.", "Restart dashboard"],
};

function confirmAction(action) {
  if (!CONFIRM[action]) return Promise.resolve(true);
  const [title, text, go] = CONFIRM[action];
  $("confirm-title").textContent = title; $("confirm-text").textContent = text; $("confirm-go").textContent = go;
  return new Promise((resolve) => {
    $("confirm-dialog").onclose = () => resolve($("confirm-dialog").returnValue === "ok");
    $("confirm-dialog").returnValue = "";
    $("confirm-dialog").showModal();
  });
}

async function waitForDashboard() {
  await new Promise((r) => setTimeout(r, 1500));
  for (let i = 0; i < 40; i++) {
    try { await api("/api/service/status"); location.reload(); return; } catch (e) { await new Promise((r) => setTimeout(r, 1000)); }
  }
  notice = `<div class="notice bad">The dashboard hasn't come back. Check it with: sudo systemctl status wps-dashboard</div>`; showNotice();
}

async function runAction(action) {
  if (!(await confirmAction(action))) return;
  document.querySelectorAll("[data-action]").forEach((b) => b.disabled = true);
  notice = `<div class="notice info">${esc(action === "restart-dashboard" ? "Restarting the dashboard…" : `Sending ${action}…`)}</div>`; showNotice();
  try {
    const res = await postJson("/api/service/action", { action });
    if (action === "restart-dashboard") return waitForDashboard();
    if (action === "reload") {
      const r = res.reload;
      notice = r.errors ? (r.errors.length ? `<div class="notice bad">Reloaded with errors: ${esc(r.errors.join("; "))}</div>` : `<div class="notice ok">Reloaded env.json and code.</div>`)
        : r.timeout ? `<div class="notice bad">WPS was sent a reload but didn't confirm it in time. Check its log below.</div>`
        : `<div class="notice">${esc(r.skipped)}</div>`;
    } else {
      notice = `<div class="notice ok">${esc({ start: "Starting", stop: "Stopping", restart: "Restarting" }[action])} WPS. The status below updates as it happens.</div>`;
    }
  } catch (e) {
    notice = `<div class="notice bad">${esc(e.message)}</div>`;
  }
  showNotice();
  await refresh();
}

async function refresh() {
  try { await loadStatus(); } catch (e) { notice = `<div class="notice bad">${esc(e.message)}</div>`; showNotice(); }
}

document.querySelectorAll("[data-action]").forEach((b) => b.onclick = () => runAction(b.dataset.action));
["log-source", "log-lines"].forEach((id) => $(id).onchange = loadLogs);
let debounce;
$("log-q").oninput = () => { clearTimeout(debounce); debounce = setTimeout(loadLogs, 300); };
$("log-refresh").onclick = loadLogs;
try { $("log-source").value = localStorage.getItem("wps-log-source") || "journal"; } catch (e) {}
$("log-source").addEventListener("change", () => { try { localStorage.setItem("wps-log-source", $("log-source").value); } catch (e) {} });

setInterval(() => { if (!document.hidden) refresh(); }, 3000);
setInterval(() => { if (!document.hidden && $("log-follow").checked) loadLogs(); }, 5000);
refresh().then(loadLogs);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
