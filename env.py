import json, os

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
}

if os.path.exists("env.json"):
    with open("env.json", "r") as f:
        env_source = open("env.json", "r")
        env = json.load(f)

        key_added = False
        for key, value in env_template.items():
            if key not in env:
                key_added = True
                print(f"{key} missing from env.json, adding with default value {value}")
                env[key] = value
    
    if key_added:
        with open("env.json", "w") as f:
            json.dump(env, f, indent=4)
else:
    print("env.json not found, creating default env.json")
    with open("env.json", "w") as f:
        json.dump(env_template, f, indent=4)