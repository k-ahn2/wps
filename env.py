import copy, json, os

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
        "originCallsign": "",
        "peers": [],
        "appSlug": "wps-repl",
        "dappsRestUrl": "http://127.0.0.1:5000",
        "dappsSysopPassword": "",
        "streamTtlSeconds": 604800,
        "outboxPollSeconds": 5,
        "inboxPollSeconds": 5,
        "reconcileIntervalSeconds": 300,
        "bootstrapFromTs": None
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
            print(f"{key} missing from env.json, adding with default value {value}")
            env[key] = copy.deepcopy(value)
            changed = True
        elif isinstance(value, dict) and isinstance(env.get(key), dict):
            if _merge_defaults(env[key], value):
                changed = True
    return changed


if os.path.exists("env.json"):
    with open("env.json", "r") as f:
        env_source = open("env.json", "r")
        env = json.load(f)

        key_added = _merge_defaults(env, env_template)

    if key_added:
        with open("env.json", "w") as f:
            json.dump(env, f, indent=4)
else:
    print("env.json not found, creating default env.json")
    env = copy.deepcopy(env_template)
    with open("env.json", "w") as f:
        json.dump(env, f, indent=4)