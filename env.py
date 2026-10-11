import copy, json, os
from state import console_log
import dashboard_auth

# Check or Create the Environemnt variables file, env.json

# Checks env.json for required keys and adds them if not present
# Creates a default env.json file if it doesn't exist

env_template = {
    "environment": "Dev",
    "apps": [
        {
            "appCode": "TST",
            "appName": "Test App",
            "recommendedClientVersion": "0.0.0",
            "minClientVersion": "0.0.0"
        }
    ],
    "socketTcpPort": 63001,
    "dbFilename": "wps.db",
    "events": {
        "enableWpsEvents": False,
        "enableBpqEvents": False,
        "eventsDbFilename": "events.db",
        "bpqApplName": "WPS",
        "bpqQueueApiUrl": "http://127.0.0.1:8008/api/tcpqueues?8"
    },
    "minWpsLogLevel": "ERROR",
    "minDbLogLevel": "ERROR",
    "notificationsEnabled": False,
    "notificationsProdId": "",
    "notificationsProdRestKey": "",
    "botsEnabled": False,
    "wpsClients": [
        "Frames: http://frames.oarc.uk",
        "WhatsPyc: [Link]",
        "Pacord: [Link]"
    ],
    "autoSubscribeToChannelIds": [],
    "maxNewPostsToReturnPerChannelOnConnect": 100,
    "wpsLoggingEnabled": True,
    "dbLoggingEnabled": True,
    "daysToRetainLogFiles": 5,
    "serviceMonitoring": {
        "enableServiceMonitoring": False,
        "bpqEndpoint": "127.0.0.1",
        "bpqPort": 8010,
        "telnetUsername": "sysop",
        "telnetPassword": "",
        "enabledCallsignsToReceiveServiceNotifications": []
    },
    "replication": {
        "enabled": False,
        "dappsCallsign": "",
        "originCallsign": "",
        "peers": [],
        "relay": False,
        "appSlug": "wps-repl",
        "dappsRestUrl": "http://127.0.0.1:5000",
        "dappsSysopPassword": "",
        "streamTtlSeconds": 604800,
        "outboxPollSeconds": 5,
        "inboxPollSeconds": 5,
        "inboxFastPollSeconds": 1,
        "inboxFastPollWindowSeconds": 300,
        "reconcileIntervalSeconds": 900,
        "ackDelaySeconds": 30,
        "batchSize": 1,
        "bootstrapFromTs": None,
        "activityRetentionDays": 7
    },
    "dashboard": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8095,
        "passwordHash": ""
    },
}

def _merge_defaults(env, template):
    '''
    Backfills any key missing from env, checking inside nested dicts too - a key added to
    an existing section (e.g. a new replication.* setting) needs this to be picked up on
    restart, since the section itself ("replication") is already present and a shallow
    top-level-only check would never look inside it.
    '''
    changed = False
    for key, value in template.items():
        if key not in env:
            console_log(f"{key} missing from env.json, adding with default value {value}")
            env[key] = copy.deepcopy(value)
            changed = True
        elif isinstance(value, dict) and isinstance(env.get(key), dict):
            if _merge_defaults(env[key], value):
                changed = True
    return changed


def _migrate(env):
    '''
    The web dashboard's settings used to live in replication.dashboard, with a plaintext
    password. They now have their own top-level dashboard section, since the dashboard serves
    the admin pages whether or not replication is on. Moves them across once, hashing the
    password, so an existing dashboard keeps its address and login.
    '''
    old = env.get('replication', {}).get('dashboard') if isinstance(env.get('replication'), dict) else None
    if not isinstance(old, dict) or 'dashboard' in env:
        return False
    del env['replication']['dashboard']
    env['dashboard'] = {
        "enabled": old.get('enabled', True),
        "host": old.get('host', "0.0.0.0"),
        "port": old.get('port', 8095),
        "passwordHash": dashboard_auth.hash_password(old['password']) if old.get('password') else "",
    }
    console_log("Moved replication.dashboard settings in env.json to the top-level dashboard section")
    return True


def _load():
    with open("env.json", "r") as f:
        loaded = json.load(f)
    changed = _migrate(loaded)
    return loaded, _merge_defaults(loaded, env_template) or changed


if os.path.exists("env.json"):
    env, key_added = _load()

    if key_added:
        with open("env.json", "w") as f:
            json.dump(env, f, indent=4)
else:
    console_log("env.json not found, creating default env.json")
    env = copy.deepcopy(env_template)
    with open("env.json", "w") as f:
        json.dump(env, f, indent=4)

# The settings as they were when this process started. This module is never reloaded, so
# STARTUP_ENV never changes: code acting on a setting that needs a restart (the TCP port, the
# database file, replication's identity and peers...) reads it from here, so a warm reload
# can't half-apply it. Everything else reads `env`, which reload() updates in place.
STARTUP_ENV = copy.deepcopy(env)


def reload():
    '''
    Re-reads env.json into `env` in place, so every module holding it (`from env import *`)
    sees the new values. Called by a warm reload (wps.py's reload_code) before the code modules
    are reloaded. Raises, leaving `env` untouched, if env.json can't be read or parsed. Keys are
    replaced one at a time rather than clearing the dict, so a reader never sees it empty.
    '''
    new, _ = _load()
    env.update(new)
    for key in [k for k in env if k not in new]:
        del env[key]