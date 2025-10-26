# Installation

## Table of Contents 

1. [WPS Installation and Prereqs](#wps-installation-and-prereqs)
2. [Node Integration - Interfacing with BPQ or Xrouter](#node-integration---interfacing-with-bpq-or-xrouter)
3. [Configuring `env.json`](#configuring-envjson)
4. [WPS System and Log Files](#wps-system-and-log-files)

[Return to README](/README.md)

## WPS Installation and Prereqs

> [!NOTE]
> WPS has only been tested on a Raspberry Pi running Raspbian. There is no known reason it shouldn't run in any Python environment. Please share your feedback so we can update the docs for others!

1. Clone the repository using `git clone https://github.com/k-ahn2/wps`
2. Go to the `wps` directory
3. Run `python3 wps.py`

This will start WPS with a default configuration. When running for the first time, WPS will create and initialise the database `wps.db`, plus `env.json`, `wps.log` and `db.log`

Check for errors in the console. Confirmation of the TCP Port is shown - check this matches the port in BPQ or Xrouter.

## Node Integration - Interfacing with BPQ or Xrouter

> [!NOTE]
> Xrouter node setup to be added

> [!WARNING]
> This section requires basic familiarity with BPQ configuration files and ideally custom application setup. Examples shown but please consult the BPQ documentation for more information

### BPQ Config Entries (abridged)
Conf
```
PORT
   PORTNUM=8
   DRIVER=TELNET
   CONFIG
   DisconnectOnClose=1       ; Ensures the client is fully disconnected if the TCP Port disconnects, not returned to the node
   CMDPORT 63001 63002       ; Port and position must match the APPLICATION entry below. HOST 0 is 63001, HOST 1 is 63002
   MAXSESSIONS=25            ; Maxmimum simultaneous connections, set to desired value
   ....
END PORT
```

### BPQ Simple Application Config
`APPLICATION 1,WPS,C 8 HOST 0 TRANS`

### BPQ Config with Callsign and NETROM
`APPLICATION 1,WPS,C 8 HOST 0 TRANS,MB7NPW-9,WTSPAC,200,WTSPAC`

## Configuring `env.json`

There is no requirement to edit `env.json`to get started - the default configuration created by `env.py` will enable WPS to run and function. Edit `env.json` if you need to:
- Change the TCP Port
- Increase WPS Application or Database logging
- Configure notifications 

Any new keys should first be added to `env.py`, which will automatically add them to `env.json` on startup. 

| Parameter | Data Type | Default | Notes |
| - | :-: | :-: | :- |
|`environment`|String|`Dev`|Historically used to suppress certain functions outside Production, but currently unused|
|`minClientVersion`|Number|`0.1`|If a client connects with a version lower than this, the server sends the connect header (to advise of an upgrade) and then disconnects them|
|`recommendedClientVersion`|Number|`0.1`|Server configurable number that is used by client side code to prompt the user to upgrade. WPS reloads this value from env.json on every connect, meaning the value can be updated and used at runtime. Must be greater than or equal to minClientVersion|
|`socketTcpPort`|Number|`63001`|TCP Port that WPS listens on. Needs to match the APPLICATION port setup in BPQ or Xrouter|
|`dbFilename`|String|`wps.db`|The filename to use for the Sqlite database. Enables a different filename to be used to differentiate between development and production, for example|
|`minWpsLogLevel`|String|`ERROR`|For application logging in `wps.log`, either `ERROR` for errors only, or `INFO` for everything. WPS contains a lot of INFO logging and could be optimised - beware of `wps.log` file size|
|`minDbLogLevel`|String|`ERROR`|For database logging in `db.log`, either `ERROR` for errors only, or `INFO` for everything. When in INFO mode, `db.log` will contain every database query and the function response - beware of `db.log` file size|
|`notificationsEnabled`|Boolean|`false`|Set to `true` if `notificationsProdId` and `notificationsProdRestKey` are configured and you want to enable notifications|
|`notificationsProdId`|String|`""`|Add the Id of your OneSignal Service|
|`notificationsProdRestKey`|String|`""`|Add the REST API key of your OneSignal Service|
|`autoSubscribeToChannelIds`|Array|`[]`|Add any channel ids required for auto subscription. WPS will check all users are subscribed to these channels on startup|
|`channels`|Object|`{}`|Add a key / value pair corresponding to the channel id (`cid`) and channel name. Used by notifications to include the Channel Name in the notification description - e.g. when a new post arrives in `cid` = 1, send "New Post from #packet-general"|
|`events`|Object|`{}`|Contains the configuration settings for WPS event logging|
|**Events Fields**|
|`enableWpsEvents`|Boolean|`False`|Enable the WPS event logging capability, used for capturing select activities such as user connnect, user disconnect and bytes sent
|`eventsDbFilename`|String|`events.db`|The Sqlite database to use
|`enableBpqEvents`|Boolean|`False`|If True, the BPQ Queue Monitor will run and query BPQ for queue statistics
|`bpqApplName`|String|`events.db`|The name of BPQ application to monitor
|`bpqQueueApiUrl`|String|`"http://127.0.0.1:8008/api/tcpqueues?8"`|The BPQ Queue Monitoring API endpoint

### Sample `env.json`

```json
{
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
    "notificationsEnabled": false,
    "notificationsProdId": "",
    "notificationsProdRestKey": "",
    "autoSubscribeToChannelIds": [100, 1],
    "channels": {
        "0": "packet-tech",
        "1": "packet-general",
        "100": "announcements"
    }
}
```

## WPS System and Log Files

| File | Overview |
| - | :- |
|`wps.py`|The main WPS application - includes all application logic, thread handling and TCP listener|
|`db.py`|Contains functions to handle every interaction between the WPS application and the database - e.g. `dbUserSearch`, `dbUserUpdate` or `dbGetOnlineUsers`|
|`wps.log`|Application logging, default ERROR only|
|`db.log`|Database logging, default ERROR only|
|`backup.py`|Run to create a JSON file containing every user, message and post object in the database. Reads `env.json` to determine the database filename from `dbFilename`. Any Sqlite supported backup method would also be valid|
|`env.py`|Used to create env.json with a default configuration if it doesn't exist, or, check all required keys are present and add any new or that are missing|
|`env.json`|Environment configuration variables|
