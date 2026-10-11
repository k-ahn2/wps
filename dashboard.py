import json
import os
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config_editor
import dashboard_auth
import replication
import replication_dashboard
from env import env, STARTUP_ENV
from state import console_log, connections_snapshot

# dashboard.py is WPS's web dashboard: one HTTP server, on dashboard.host:dashboard.port from
# env.json, serving two dashboards behind one login, with a switcher in the header:
#
# - Admin (/): the settings in env.json, editable. A save writes env.json and then warm-reloads
#   WPS (wps.py's reload_code, passed to start()), so every setting that can apply live does so
#   at once; the page marks the ones that need a restart, and lists any changed but not yet
#   applied.
# - Replication (/replication): replication_dashboard.py's read-only view. Shown only when
#   replication is enabled.
#
# Started from wps.py unless dashboard.enabled is false. Its own address is fixed at startup.
#
# Login: there is no default password. Until one is set, every page shows a form to set it, and
# the first person to submit it sets it - so open the dashboard as soon as WPS starts. It is
# stored hashed in dashboard.passwordHash (see dashboard_auth.py); a session is a cookie signed
# with that hash. To reset a forgotten password, set dashboard.passwordHash to "" in env.json
# and warm-reload (`systemctl reload wps`) or restart WPS.
#
# Plain HTTP, no TLS: it's meant for a trusted network. Write requests must be same-origin JSON,
# which together with the SameSite=Strict session cookie stops another site's page from using a
# logged-in browser.

CONFIG = STARTUP_ENV.get('dashboard', {})
MAX_POST_BYTES = 1024 * 1024
STARTED_AT = round(time.time() * 1000)

# Set by start().
RELOAD = None              # wps.py's reload_code, or None when running standalone
SHOW_REPLICATION = False

# Failed logins per client address: after LOGIN_MAX_FAILURES within LOGIN_WINDOW_SECONDS,
# further attempts are refused until the oldest failure ages out.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300
_login_failures = {}
_login_lock = threading.Lock()


def _password_hash():
    return env.get('dashboard', {}).get('passwordHash', '')


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


def _runtime():
    return {
        "embedded": RELOAD is not None,
        "started_at": STARTED_AT,
        "connections": len(connections_snapshot()) if RELOAD is not None else None,
        "replication": SHOW_REPLICATION,
    }


class _Handler(BaseHTTPRequestHandler):
    server_version = "WPSDashboard/1"

    def log_message(self, *_args):
        pass  # keep the WPS console quiet

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

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html", "/replication"):
            if not _password_hash():
                self._send(200, _page(AUTH_PAGE).replace("__MODE__", "setup"), "text/html; charset=utf-8")
            elif not self._logged_in():
                self._send(200, _page(AUTH_PAGE).replace("__MODE__", "login"), "text/html; charset=utf-8")
            elif url.path == "/replication":
                if SHOW_REPLICATION:
                    self._send(200, _page(replication_dashboard.PAGE, "replication"), "text/html; charset=utf-8")
                else:
                    self._redirect("/")
            else:
                self._send(200, _page(ADMIN_PAGE, "admin"), "text/html; charset=utf-8")
            return

        if not url.path.startswith("/api/"):
            self._send_json(404, {"error": "not found"})
            return
        if not self._logged_in():
            self._send_json(401, {"error": "Not logged in"})
            return
        try:
            if url.path == "/api/admin/config":
                self._send_json(200, {**config_editor.load(), "runtime": _runtime()})
            elif not SHOW_REPLICATION:
                self._send_json(404, {"error": "not found"})
            elif url.path == "/api/activity/export":
                conn = replication_dashboard._connect()
                try:
                    body, content_type, filename = replication_dashboard.export_activity(conn.cursor(), parse_qs(url.query))
                finally:
                    conn.close()
                self._send(200, body, content_type, filename)
            elif url.path in replication_dashboard.ROUTES:
                conn = replication_dashboard._connect()
                try:
                    result = replication_dashboard.ROUTES[url.path](conn.cursor(), parse_qs(url.query))
                finally:
                    conn.close()
                self._send_json(200, result)
            else:
                self._send_json(404, {"error": "not found"})
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
            "/api/admin/config": self._post_config,
        }.get(path)
        if handler is None:
            self._send_json(404, {"error": "not found"})
            return
        if path not in ("/api/auth/setup", "/api/auth/login") and not self._logged_in():
            self._send_json(401, {"error": "Not logged in"})
            return
        try:
            handler(body)
        except config_editor.ConfigError as e:
            self._send_json(e.status, {"error": str(e)})
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
        if result["changed"] and RELOAD is not None:
            result["reload"] = {"errors": RELOAD()}
        result["restart_pending"] = config_editor.restart_pending() if RELOAD is not None else []
        result["runtime"] = _runtime()
        self._send_json(200, result)


def _page(template, current=None):
    '''Fills the shared stylesheet, header switcher and account buttons into a page template.'''
    links = [("admin", "/", "Admin")] + ([("replication", "/replication", "Replication")] if SHOW_REPLICATION else [])
    app_nav = '<h1>WPS</h1>' + (
        '<nav class="apps">' + "".join(f'<a href="{href}"{" class=active" if key == current else ""}>{label}</a>' for key, href, label in links) + '</nav>'
        if current and len(links) > 1 else "")
    account = ('<span class="account">' + ('<button class="btn" id="password-open">Password</button>' if current == "admin" else "") +
               '<button class="btn" onclick="wpsLogout()">Log out</button></span>' + LOGOUT_SCRIPT) if current else ""
    return (template.replace("__BASE_CSS__", BASE_CSS).replace("__APP_NAV__", app_nav)
            .replace("__ACCOUNT__", account).replace("__FEATURES__", json.dumps({"replication": SHOW_REPLICATION})))


def start(reload=None, show_replication=None):
    '''
    Called once from wps.py at startup with reload=reload_code. No-ops if dashboard.enabled is
    false. Serves on a daemon thread, returns the server or None. show_replication defaults to
    replication.enabled.
    '''
    global RELOAD, SHOW_REPLICATION
    if not CONFIG.get("enabled", True):
        console_log("Dashboard disabled (dashboard.enabled is false in env.json)")
        return None
    RELOAD = reload
    SHOW_REPLICATION = replication.ENABLED if show_replication is None else show_replication
    host = CONFIG.get("host", "0.0.0.0")
    port = CONFIG.get("port", 8095)
    try:
        server = ThreadingHTTPServer((host, port), _Handler)
    except OSError as e:
        console_log(f"Dashboard failed to start on {host}:{port}: {e}", "ERROR")
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    console_log(f"Dashboard on http://{host}:{port}/")
    if not _password_hash():
        console_log(f"Dashboard has no password yet - open http://{host}:{port}/ now to set one", "WARN")
    return server


def main(show_replication=None):
    # Standalone: serve the dashboard without running WPS. Saves still write env.json, but there
    # is no WPS in this process to reload - the page says so.
    server = start(show_replication=show_replication)
    if server:
        try:
            threading.Event().wait()
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

ADMIN_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WPS Admin</title>
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
    <span><span class="pill warn">restart</span> after a WPS restart</span>
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

// ---------- Configuration ----------
// Edits env.json through /api/admin/config. Fields are generated from the file itself, so a key
// added to env.py's template shows up here without changing this page. Secrets arrive blank:
// typing a value replaces the stored one, "clear" empties it, leaving it blank keeps it.
let cfg = null;          // last /api/admin/config response
let cfgNotice = "";      // survives re-render, e.g. the result of a save

const pathOf = (prefix, key) => prefix ? `${prefix}.${key}` : key;
const getPath = (obj, path) => path.split(".").reduce((o, k) => o[k], obj);
function setPath(obj, path, value) { const ks = path.split("."), leaf = ks.pop(); ks.reduce((o, k) => o[k], obj)[leaf] = value; }
const appliesOf = (path) => cfg.meta.live.includes(path) ? "live" : path in cfg.meta.script ? "script" : "restart";

function appliesPill(path) {
  const a = appliesOf(path);
  if (a === "live") return `<span class="pill ok" title="Applied as soon as you save">live</span>`;
  if (a === "script") return `<span class="pill" title="${esc(cfg.meta.script[path])}">script</span>`;
  return `<span class="pill warn" title="Takes effect after WPS restarts">restart</span>` +
    (cfg.restart_pending.includes(path) ? ` <span class="pill bad" title="Changed since WPS started">restart pending</span>` : "");
}

function fieldKind(path, value) {
  const m = cfg.meta;
  if (path in cfg.secrets) return "secret";
  if (m.choices[path]) return "choice";
  if (m.structured.includes(path)) return "json";
  if (path in m.nullable) return m.nullable[path] === "number" ? "number" : "text";
  if (typeof value === "boolean") return "bool";
  if (typeof value === "number") return "number";
  if (typeof value === "string") return "text";
  if (Array.isArray(value)) return value.every((v) => v === null || typeof v !== "object") ? "lines" : "json";
  return "json";
}

function fieldHtml(path, value, kind) {
  const id = esc(`cfg:${path}`);
  if (kind === "bool") return `<input type="checkbox" id="${id}"${value ? " checked" : ""}>`;
  if (kind === "choice") return `<select id="${id}">${[...new Set([value, ...cfg.meta.choices[path]])].map((c) => `<option${c === value ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  if (kind === "number") return `<input type="number" step="any" id="${id}" value="${esc(value ?? "")}"${path in cfg.meta.nullable ? ` placeholder="empty = null"` : ""}>`;
  if (kind === "text") return `<input type="text" id="${id}" value="${esc(value)}" spellcheck="false">`;
  if (kind === "lines") return `<textarea id="${id}" spellcheck="false" placeholder="one per line">${esc(value.join("\n"))}</textarea>`;
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
  if (kind === "lines") {
    const numeric = original.length ? original.every((v) => typeof v === "number") : null;
    const items = el.value.split("\n").map((l) => l.trim()).filter(Boolean);
    // Existing items decide the type; an empty list takes numbers for numeric lines (channel ids).
    const value = items.map((l) => numeric !== false && /^-?\d+(\.\d+)?$/.test(l) ? Number(l) : l);
    if (numeric && value.some((v) => typeof v !== "number")) return { error: "Every line must be a number" };
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
  cfg = await api("/api/admin/config");
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
  $("cfg-sections").querySelectorAll("input, select, textarea").forEach((el) => { el.oninput = el.onchange = updateCfgBar; });
  $("cfg-bar").hidden = false;
  renderStatus();
  updateCfgBar();
}

function renderStatus() {
  const r = cfg.runtime;
  $("status-line").innerHTML = r.embedded
    ? `WPS running since ${esc(new Date(r.started_at).toLocaleString())} · ${r.connections} user${r.connections === 1 ? "" : "s"} connected · editing <span class="mono" style="word-break:break-all">${esc(cfg.path)}</span>`
    : `Editing <span class="mono" style="word-break:break-all">${esc(cfg.path)}</span>`;
  const notes = [];
  if (!r.embedded) notes.push(`<div class="notice">This dashboard is running on its own, not inside WPS, so saving only writes env.json. If WPS is running, apply the live settings with <span class="mono">systemctl reload wps</span> (or press r in its terminal); the rest apply when it next starts.</div>`);
  if (cfgNotice) notes.push(cfgNotice);
  if (r.embedded && cfg.restart_pending.length) notes.push(`<div class="notice">Restart WPS to apply ${cfg.restart_pending.length === 1 ? "this change" : "these changes"}, saved but not yet in effect: ${codeList(cfg.restart_pending)}.</div>`);
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
  const restart = dirty.filter((d) => appliesOf(d.path) === "restart");
  $("cfg-summary").textContent = errors.length ? `${errors.length} field${errors.length > 1 ? "s" : ""} to fix: ${errors.map((d) => d.path).join(", ")}`
    : dirty.length ? `${dirty.length} unsaved change${dirty.length > 1 ? "s" : ""}: ${dirty.map((d) => d.path).join(", ")}` +
        (restart.length ? ` (${restart.length} need${restart.length === 1 ? "s" : ""} a restart)` : "")
    : "No changes";
  $("cfg-save").disabled = !dirty.length || errors.length > 0;
  $("cfg-discard").disabled = !dirty.length;
}

function describeSave(res) {
  if (!res.changed.length) return `<div class="notice">Nothing to save - env.json already has these values.</div>`;
  const items = [];
  const errors = res.reload?.errors || [];
  if (res.live.length) items.push(res.reload && !errors.length ? `<li>Applied now: ${codeList(res.live)}</li>`
    : res.reload ? `<li>Saved, but the reload reported problems, so these may not have applied: ${codeList(res.live)}</li>`
    : `<li>Saved, and apply when WPS reloads: ${codeList(res.live)}</li>`);
  if (res.restart.length) items.push(`<li><b>Restart WPS</b> to apply: ${codeList(res.restart)}</li>`);
  if (res.script.length) items.push(`<li>Read by a separate script (see each setting): ${codeList(res.script)}</li>`);
  if (errors.length) items.push(`<li>Reload errors: ${errors.map(esc).join("; ")} - see the WPS log.</li>`);
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
    const res = await postJson("/api/admin/config", { config, secrets, mtime: cfg.mtime });
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


if __name__ == "__main__":
    main()
