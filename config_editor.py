import copy
import json
import os
import threading

import dashboard_auth
from env import env, env_template, STARTUP_ENV
from state import console_log

# config_editor.py backs the admin dashboard's Configuration page (dashboard.py): it reads
# env.json for display and writes an edited copy back. dashboard.py then warm-reloads WPS
# (wps.py's reload_code), which applies every "live" setting below straight away.
#
# - Secrets (passwords, REST keys) never leave the server. They are sent to the browser blanked,
#   with a flag saying whether one is set; the browser sends a secret back only when the user
#   types a new one (or clears it), otherwise the stored value is kept. The dashboard's own
#   password hash isn't shown at all - it's changed through the dashboard's password dialog.
# - A save is checked against the file on disk: the browser sends back the mtime it loaded, and
#   if env.json has changed since (another editor, or env.py backfilling a new key) the save is
#   refused rather than overwriting it.
# - Every value must keep the JSON type it already has (or the template's, for a new key), so a
#   typo can't turn a port into a string. Nothing is added or removed except by env.py.
# - The file is written atomically with the previous version kept as env.json.bak.

ENV_PATH = os.path.abspath("env.json")
BACKUP_PATH = ENV_PATH + ".bak"
_WRITE_LOCK = threading.Lock()

# Leaf key names whose values are credentials.
SECRET_KEYS = {"telnetPassword", "dappsSysopPassword", "notificationsProdRestKey"}

# Never sent to the browser, and kept as they are on save.
HIDDEN = {"dashboard.passwordHash"}

# Keys whose value may be null as well as the type given.
NULLABLE = {"replication.bootstrapFromTs": "number"}

CHOICES = {
    "minWpsLogLevel": ["ERROR", "INFO"],
    "minDbLogLevel": ["ERROR", "INFO"],
}

PORT_KEYS = {"socketTcpPort", "serviceMonitoring.bpqPort", "dashboard.port"}

# Lists of objects, edited as JSON rather than one item per line.
STRUCTURED = {"apps", "replication.peers"}

# When a change takes effect. "live": applied by the warm reload that follows a save (see
# wps.py's reload_env and reload_code, and env.STARTUP_ENV for why the rest can't be). "script":
# read by a separate script, not by WPS. Anything not listed needs a WPS restart.
LIVE = {
    "environment", "apps", "minWpsLogLevel", "minDbLogLevel", "wpsLoggingEnabled", "dbLoggingEnabled",
    "daysToRetainLogFiles", "notificationsEnabled", "notificationsProdId", "notificationsProdRestKey",
    "wpsClients", "autoSubscribeToChannelIds", "maxNewPostsToReturnPerChannelOnConnect",
    "events.enableWpsEvents",
    "replication.dappsRestUrl", "replication.streamTtlSeconds", "replication.outboxPollSeconds",
    "replication.inboxPollSeconds", "replication.inboxFastPollSeconds", "replication.inboxFastPollWindowSeconds",
    "replication.reconcileIntervalSeconds", "replication.ackDelaySeconds", "replication.batchSize",
    "replication.activityRetentionDays",
}
SCRIPT = {
    "events.enableBpqEvents": "bpq_queue_monitor.py - restart it to apply",
    "events.bpqApplName": "bpq_queue_monitor.py - restart it to apply",
    "events.bpqQueueApiUrl": "bpq_queue_monitor.py - restart it to apply",
    "serviceMonitoring.enableServiceMonitoring": "service_monitor.py - applies on its next run",
    "serviceMonitoring.bpqEndpoint": "service_monitor.py - applies on its next run",
    "serviceMonitoring.bpqPort": "service_monitor.py - applies on its next run",
    "serviceMonitoring.telnetUsername": "service_monitor.py - applies on its next run",
    "serviceMonitoring.telnetPassword": "service_monitor.py - applies on its next run",
    "serviceMonitoring.enabledCallsignsToReceiveServiceNotifications": "service_monitor.py - applies on its next run",
    "replication.dappsSysopPassword": "dapps-tools/dapps_queue.py - applies on its next run",
}


def applies(path):
    if path in LIVE:
        return "live"
    if path in SCRIPT:
        return "script"
    return "restart"


SECTION_TITLES = {
    "": "General",
    "events": "Events",
    "serviceMonitoring": "Service monitoring",
    "replication": "Replication",
    "dashboard": "Dashboard",
}

HELP = {
    "environment": "Historically used to suppress certain functions outside Production; currently unused.",
    "apps": "One object per supported client app: appCode (3 characters, matched against the connect object's 'a' key), appName, recommendedClientVersion and minClientVersion (major.minor.patch).",
    "socketTcpPort": "TCP port WPS listens on. Must match the APPLICATION port set up in BPQ or Xrouter.",
    "dbFilename": "SQLite database filename.",
    "minWpsLogLevel": "wps.log level: ERROR for errors only, INFO for everything (large).",
    "minDbLogLevel": "db.log level: ERROR for errors only, INFO logs every query (large).",
    "notificationsEnabled": "Send OneSignal push notifications. Needs notificationsProdId and notificationsProdRestKey.",
    "notificationsProdId": "OneSignal app id.",
    "notificationsProdRestKey": "OneSignal REST API key.",
    "botsEnabled": "Load and run the channel bots listed in bots/bots.json.",
    "wpsClients": "Client apps listed to users who connect manually, one per line.",
    "autoSubscribeToChannelIds": "Channel ids every user is subscribed to. Applying adds them to every existing user.",
    "maxNewPostsToReturnPerChannelOnConnect": "Above this many new posts in a channel, the client gets a paused channel header (pch) instead.",
    "wpsLoggingEnabled": "Write wps.log.",
    "dbLoggingEnabled": "Write db.log.",
    "daysToRetainLogFiles": "Number of daily log files kept.",
    "events.enableWpsEvents": "Record select activity (connect, disconnect, bytes sent) to the events database.",
    "events.enableBpqEvents": "Let the BPQ queue monitor query BPQ for queue statistics.",
    "events.eventsDbFilename": "SQLite database for events.",
    "events.bpqApplName": "BPQ application name to monitor.",
    "events.bpqQueueApiUrl": "BPQ queue monitoring API endpoint.",
    "serviceMonitoring.enableServiceMonitoring": "Monitor BPQ services and notify the callsigns below.",
    "serviceMonitoring.bpqEndpoint": "BPQ telnet host.",
    "serviceMonitoring.bpqPort": "BPQ telnet port.",
    "serviceMonitoring.telnetUsername": "BPQ telnet username.",
    "serviceMonitoring.telnetPassword": "BPQ telnet password.",
    "serviceMonitoring.enabledCallsignsToReceiveServiceNotifications": "Callsigns notified of service changes, one per line.",
    "replication.enabled": "Master switch for replication. Also shows the Replication dashboard.",
    "replication.dappsCallsign": "Must exactly equal the callsign (with SSID) given to this node's DAPPS.",
    "replication.originCallsign": "This instance's identity in the replication stream. Defaults to dappsCallsign if empty.",
    "replication.peers": "The other instances, each as {\"originCallsign\": \"...\", \"dappsCallsign\": \"...\"}.",
    "replication.relay": "Pass other instances' events on, so instances can form a tree. Set the same on every instance.",
    "replication.appSlug": "DAPPS queue name. Must be identical on every instance.",
    "replication.dappsRestUrl": "Base URL of this node's DAPPS REST API.",
    "replication.dappsSysopPassword": "DAPPS sysop password, used by dapps-tools/dapps_queue.py.",
    "replication.streamTtlSeconds": "How long DAPPS keeps trying to deliver an event.",
    "replication.outboxPollSeconds": "Fallback interval for handing events to DAPPS (mainly the retry rate). Applies after the current wait.",
    "replication.inboxPollSeconds": "Inbox poll interval when replication is quiet. Applies after the current wait.",
    "replication.inboxFastPollSeconds": "Inbox poll interval while replication is active. Applies after the current wait.",
    "replication.inboxFastPollWindowSeconds": "How long the fast rate lasts after the latest write or inbound event.",
    "replication.reconcileIntervalSeconds": "How often a digest is sent to each peer. Applies after the current wait, which can be up to the old interval.",
    "replication.ackDelaySeconds": "How long acks are held so several can be combined.",
    "replication.batchSize": "Most events per DAPPS message. Leave at 1 until every peer understands batches.",
    "replication.bootstrapFromTs": "Epoch ms. Only for a brand-new instance joining an existing mesh; leave empty otherwise.",
    "replication.activityRetentionDays": "Days of history kept for the Replication dashboard.",
    "dashboard.enabled": "Run this web dashboard. If you turn it off, turn it back on by editing env.json and restarting WPS.",
    "dashboard.host": "Listen address. 0.0.0.0 accepts connections on every interface; 127.0.0.1 only from this machine.",
    "dashboard.port": "Dashboard HTTP port.",
}


class ConfigError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _read():
    with open(ENV_PATH) as f:
        return json.load(f), os.stat(ENV_PATH).st_mtime_ns


def _write(config):
    '''Atomically replaces env.json, keeping the previous version as env.json.bak.'''
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=4)
        f.write("\n")
    with open(ENV_PATH) as src, open(BACKUP_PATH, "w") as dst:
        dst.write(src.read())
    os.replace(tmp, ENV_PATH)


def _secret_paths(node, prefix=""):
    paths = {}
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths.update(_secret_paths(value, path + "."))
        elif key in SECRET_KEYS:
            paths[path] = value
    return paths


def _get_path(node, path):
    for part in path.split("."):
        node = node[part]
    return node


def _set_path(node, path, value):
    *parents, leaf = path.split(".")
    for part in parents:
        node = node[part]
    node[leaf] = value


def _del_path(node, path):
    *parents, leaf = path.split(".")
    for part in parents:
        node = node.get(part, {})
    node.pop(leaf, None)


def _template_value(path):
    try:
        return _get_path(env_template, path)
    except (KeyError, TypeError):
        return None


def _kind(value):
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return {str: "string", list: "list", dict: "object"}[type(value)]


def _leaf_paths(node, prefix=""):
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _leaf_paths(value, path + ".")
        else:
            yield path, value


def restart_pending(config=None):
    '''Restart-only settings whose value in env.json differs from what this process started with.'''
    if config is None:
        config, _ = _read()
    pending = []
    for path, value in _leaf_paths(config):
        if applies(path) != "restart" or path in HIDDEN:
            continue
        try:
            started = _get_path(STARTUP_ENV, path)
        except (KeyError, TypeError):
            started = None
        if value != started:
            pending.append(path)
    return pending


def load():
    '''The current env.json for the Configuration page, with secrets blanked and hidden keys removed.'''
    config, mtime = _read()
    masked = copy.deepcopy(config)
    for path in HIDDEN:
        _del_path(masked, path)
    secrets = {}
    for path, value in _secret_paths(config).items():
        secrets[path] = bool(value)
        _set_path(masked, path, "")
    return {
        "config": masked,
        "mtime": str(mtime),
        "path": ENV_PATH,
        "secrets": secrets,
        "restart_pending": restart_pending(config),
        "meta": {
            "help": HELP, "choices": CHOICES, "nullable": NULLABLE, "sections": SECTION_TITLES,
            "ports": sorted(PORT_KEYS), "structured": sorted(STRUCTURED),
            "live": sorted(LIVE), "script": SCRIPT,
        },
    }


def _validate(old, new, prefix=""):
    '''Checks new has exactly old's keys, each with the same type. Returns the changed paths.'''
    if set(old) != set(new):
        added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
        raise ConfigError(f"Keys can't be added or removed here (added {added or 'none'}, removed {removed or 'none'} under '{prefix or 'top level'}')")
    changed = []
    for key, old_value in old.items():
        path, value = f"{prefix}{key}", new[key]
        if isinstance(old_value, dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{path} must be an object")
            changed += _validate(old_value, value, path + ".")
            continue
        expected = NULLABLE.get(path) or _kind(old_value)
        if expected == "null":
            expected = _kind(_template_value(path))
        actual = _kind(value)
        if actual != expected and not (actual == "null" and path in NULLABLE) and expected != "null":
            raise ConfigError(f"{path} must be a {expected}, not a {actual}")
        if value == old_value:
            continue  # an existing out-of-range value isn't this save's problem
        if path in CHOICES and value not in CHOICES[path]:
            raise ConfigError(f"{path} must be one of {', '.join(CHOICES[path])}")
        if path in PORT_KEYS and not (isinstance(value, int) and 1 <= value <= 65535):
            raise ConfigError(f"{path} must be a whole number from 1 to 65535")
        changed.append(path)
    return changed


def save(body):
    '''
    body: {"config": <edited config, secrets blank>, "secrets": {path: new value}, "mtime": <from load>}.
    Writes env.json and returns the changed paths, split by when they take effect, or raises
    ConfigError. Doesn't apply anything itself - the caller warm-reloads.
    '''
    if not isinstance(body, dict) or not isinstance(body.get("config"), dict):
        raise ConfigError("Expected a JSON object with a config object")
    with _WRITE_LOCK:
        current, mtime = _read()
        if str(mtime) != str(body.get("mtime")):
            raise ConfigError("env.json has changed since this page loaded it. Reload to see the current version, then make your changes again.", 409)

        new = copy.deepcopy(body["config"])
        stored = _secret_paths(current)
        updates = body.get("secrets") or {}
        for path in [*stored, *HIDDEN]:
            try:
                _set_path(new, path, _get_path(current, path))
            except (KeyError, TypeError):
                pass  # structure mismatch - reported by _validate
        for path, value in updates.items():
            if path not in stored:
                raise ConfigError(f"{path} is not a secret setting")
            if not isinstance(value, str):
                raise ConfigError(f"{path} must be a string")
            _set_path(new, path, value)

        changed = _validate(current, new)
        if changed:
            _write(new)
            console_log(f"env.json updated from the dashboard: {', '.join(changed)}")
        mtime = os.stat(ENV_PATH).st_mtime_ns
    return {
        "changed": changed,
        "live": [p for p in changed if applies(p) == "live"],
        "script": [p for p in changed if applies(p) == "script"],
        "restart": [p for p in changed if applies(p) == "restart"],
        "mtime": str(mtime),
    }


def set_password(password):
    '''
    Stores a new dashboard password (hashed) in env.json and in the live env, so it applies at
    once. Returns the new hash, which keys the caller's new session.
    '''
    if not isinstance(password, str) or len(password) < dashboard_auth.MIN_PASSWORD_LENGTH:
        raise ConfigError(f"The password must be at least {dashboard_auth.MIN_PASSWORD_LENGTH} characters")
    stored = dashboard_auth.hash_password(password)
    with _WRITE_LOCK:
        current, _ = _read()
        current.setdefault("dashboard", {})["passwordHash"] = stored
        _write(current)
    env.setdefault("dashboard", {})["passwordHash"] = stored
    console_log("Dashboard password set")
    return stored
