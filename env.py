import json, os

# Check or Create the Environemnt variables file, env.json

# Checks env.json for required keys and adds them if not present
# Creates a default env.json file if it doesn't exist

env_template = {
    "environment": "Dev",
    "minClientVersion": 0.1,
    "recommendedClientVersion": 0.1,
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
    "autoSubscribeToChannelIds": [],
    "maxNewPostsToReturnPerChannelOnConnect": 100,
    "channels": {},
    "bots": {},
    "pacagotchiChannelId": 0,
    "pacagotchi": {
        "tick_interval":          300,
        "age_juvenile":           3600,
        "age_adult":              172800,
        "hunger_drop_per_tick":   2,
        "happiness_drop_bored":   2,
        "health_drop_starving":   4,
        "health_drop_dirty":      2,
        "health_drop_ill_late":   8,
        "sleep_tick_multiplier":  0.25,
        "auto_sleep_after":       10800,
        "sleep_min_hours":        6,
        "sleep_max_hours":        10,
        "poop_rise_every_n_ticks": 7,
        "junk_illness_threshold": 3,
        "ill_death_timeout":      10800
    },
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
    }
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
    env = env_template
    with open("env.json", "w") as f:
        json.dump(env_template, f, indent=4)