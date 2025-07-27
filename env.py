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
    "minWpsLogLevel": "ERROR",
    "minDbLogLevel": "ERROR",
    "notificationsEnabled": False,
    "notificationsProdId": "",
    "notificationsProdRestKey": "",
    "autoSubscribeToChannelIds": [],
    "channels": {}
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