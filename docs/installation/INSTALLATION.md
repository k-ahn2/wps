# Installation

## Table of Contents 

1. [WPS Installation and Prereqs](#wps-installation-and-prereqs)
2. [Node Integration - Interfacing with BPQ or Xrouter](#node-integration---interfacing-with-bpq-or-xrouter)
3. [Configuring `env.json`](#configuring-envjson)
4. [Configuring `channels.json`](#configuring-channelsjson)
5. [WPS System and Log Files](#wps-system-and-log-files)

[Return to README](/README.md)

## WPS Installation and Prereqs

> [!NOTE]
> WPS has only been tested on a Raspberry Pi running Raspbian. There is no known reason it shouldn't run in any Python environment. Please share your feedback so we can update the docs for others!

1. Clone the repository using `git clone https://github.com/k-ahn2/wps`
2. Go to the `wps` directory
3. Run `python3 wps.py`

This will start WPS with a default configuration. When running for the first time, WPS will create and initialise the database `wps.db`, plus `env.json`, `channels.json`, `wps.log` and `db.log`

Check for errors in the console. Confirmation of the TCP Port is shown - check this matches the port in BPQ or Xrouter.

> [!TIP]
> When run this way, attached to an interactive terminal, press `r` at any time to warm-reload database, message-processing and bot code changes into the running server without disconnecting any connected user. This only works with an attached TTY - not when WPS is run as a service. See [Warm Reloading Code](/README.md#warm-reloading-code) for what is and isn't covered.

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
- Configure WPS or BPQ event monitoring

Any new keys should first be added to `env.py`, which will automatically add them to `env.json` on startup. 

| Parameter | Data Type | Default | Notes |
| - | :-: | :-: | :- |
|`environment`|String|`Dev`|Historically used to suppress certain functions outside Production, but currently unused|
|`apps`|Array|see below|One object per supported client app, each with `appCode`, `appName`, `recommendedClientVersion` and `minClientVersion`. On connect, the client sends its 3-character app code in the connect object's `a` key; WPS looks that code up here (re-reading env.json on every connect) to find the versions to check against. If the client sends no `a` key, or the code isn't in this array, the minimum and recommended version checks are skipped|
|**Apps Fields**|
|`appCode`|String|-|3-character code identifying the app (e.g. `FRM`), matched against the connect object's `a` key|
|`appName`|String|-|Human-readable app name, used in logging only|
|`recommendedClientVersion`|String|`0.0.0`|3-tier `major.minor.patch` version. If the client is behind this, the connect header advises an upgrade (via `v`). Must be greater than or equal to `minClientVersion`. Legacy 2-tier / numeric values (e.g. `0.44`) are still accepted and compared component-wise|
|`minClientVersion`|String|`0.0.0`|3-tier `major.minor.patch` version. If the client is behind this, the server sends the connect header (to advise of an upgrade) and then disconnects them|
|`socketTcpPort`|Number|`63001`|TCP Port that WPS listens on. Needs to match the APPLICATION port setup in BPQ or Xrouter|
|`dbFilename`|String|`wps.db`|The filename to use for the Sqlite database. Enables a different filename to be used to differentiate between development and production, for example|
|`minWpsLogLevel`|String|`ERROR`|For application logging in `wps.log`, either `ERROR` for errors only, or `INFO` for everything. WPS contains a lot of INFO logging and could be optimised - beware of `wps.log` file size|
|`minDbLogLevel`|String|`ERROR`|For database logging in `db.log`, either `ERROR` for errors only, or `INFO` for everything. When in INFO mode, `db.log` will contain every database query and the function response - beware of `db.log` file size|
|`notificationsEnabled`|Boolean|`false`|Set to `true` if `notificationsProdId` and `notificationsProdRestKey` are configured and you want to enable notifications|
|`notificationsProdId`|String|`""`|Add the Id of your OneSignal Service|
|`notificationsProdRestKey`|String|`""`|Add the REST API key of your OneSignal Service|
|`botsEnabled`|Boolean|`false`|Set to `true` to load and run channel bots from `bots/bots.json` on startup. When `false`, bots are not loaded or processed at all, regardless of `bots/bots.json`'s contents|
|`autoSubscribeToChannelIds`|Array|`[]`|Add any channel ids required for auto subscription. WPS will check all users are subscribed to these channels on startup|
|`maxNewPostsToReturnPerChannelOnConnect`|Number|`100`|During connect, if total number of posts to return to the client for a given channel is more than this number, return paused channel headers only via `pch`
|`events`|Object|`{}`|Contains the configuration settings for WPS event logging|
|**Events Fields**|
|`enableWpsEvents`|Boolean|`False`|Enable the WPS event logging capability, used for capturing select activities such as user connnect, user disconnect and bytes sent
|`eventsDbFilename`|String|`events.db`|The Sqlite database to use
|`enableBpqEvents`|Boolean|`False`|If True, the BPQ Queue Monitor will run and query BPQ for queue statistics
|`bpqApplName`|String|`WPS`|The name of BPQ application to monitor
|`bpqQueueApiUrl`|String|`"http://127.0.0.1:8008/api/tcpqueues?8"`|The BPQ Queue Monitoring API endpoint

### Sample `env.json`

```json
{
    "environment": "Dev",
    "apps": [
        {
            "appCode": "FRM",
            "appName": "Frames",
            "recommendedClientVersion": "0.0.0",
            "minClientVersion": "0.95"
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
    "notificationsEnabled": false,
    "notificationsProdId": "",
    "notificationsProdRestKey": "",
    "botsEnabled": false,
    "autoSubscribeToChannelIds": [100, 1],
    "maxNewPostsToReturnPerChannelOnConnect": 100
}
```

## Configuring `channels.json`

`channels.json` holds the channel list WPS serves to clients. It has two top-level keys:
- `c` - the array of channels. Required
- `cg` - the array of channel groups, used to organise channels in the client UI. Optional - omit or leave empty if you don't need grouping

If `channels.json` doesn't exist when WPS starts (e.g. on first run), it's created automatically with a single "General" group containing one "Lounge" channel (`cid` 0) - the simplest possible setup, shown below. WPS reloads `channels.json` on every client connect, so it can then be edited to add, rename or remove channels and groups without restarting WPS.

### Sample `channels.json`

The default created on first run - one group containing one channel:

```json
{
    "cg": [
        {
            "cgid": 0,
            "gn": "General"
        }
    ],
    "c": [
        {
            "cgid": 0,
            "cid": 0,
            "cn": "Lounge",
            "cd": "General discussion"
        }
    ]
}
```

For grouping channels, adding auto-subscribed or read-only channels, or linking a channel to a bot, see the full field reference and examples in [Protocol - Channels](/docs/protocol/CHANNELS.md#type-chl---channel-list).

## WPS System and Log Files

| File | Overview |
| - | :- |
|`wps.py`|The TCP layer - listens for connections, handles the accept loop and each connection's raw receive/buffer/framing/thread handling. Contains no message-processing logic itself, so it never needs restarting just to deploy a processing change - see [Warm Reloading Code](/README.md#warm-reloading-code)|
|`handlers.py`|All message-processing/business logic - every message type handler, dispatch/routing, push notifications and channel cache sync. Called from `wps.py` and warm-reloadable without disconnecting users - see [Warm Reloading Code](/README.md#warm-reloading-code)|
|`state.py`|Shared in-memory state used by both `wps.py` and `handlers.py` (open connections, loaded bots, the channel cache). Deliberately never reloaded, so this state survives a warm reload|
|`logger.py`|Application and database logging helpers (`wps_logger`, `db_logger`) shared by `db.py` and `handlers.py`|
|`db.py`|Contains functions to handle every interaction between the WPS application and the database - e.g. `dbUserSearch`, `dbUserUpdate` or `dbGetOnlineUsers`. Called from `wps.py` and `handlers.py` and warm-reloadable without disconnecting users - see [Warm Reloading Code](/README.md#warm-reloading-code)|
|`wps.log`|Application logging, default ERROR only|
|`db.log`|Database logging, default ERROR only|
|`backup.py`|Run to create a JSON file containing every user, message and post object in the database. Reads `env.json` to determine the database filename from `dbFilename`. Any Sqlite supported backup method would also be valid|
|`env.py`|Used to create env.json with a default configuration if it doesn't exist, or, check all required keys are present and add any new or that are missing|
|`env.json`|Environment configuration variables|
|`channels.json`|Channel groups and channel definitions. Created automatically with a default "General" group and "Lounge" channel if it doesn't exist. See [Protocol - Channels](/docs/protocol/CHANNELS.md#type-chl---channel-list)|
|`bots/bots.json`|Registers channel bots, keyed by bot name with an object holding the channel id (`cid`) they respond on plus any bot-specific config as the value. Only loaded if `botsEnabled` is `true` in `env.json`. See the [Bots section in the README](/README.md#bots)|
|`bots/`|Directory containing bot Python modules, one file per bot named to match its key in `bots/bots.json`|
|`bpq_queue_monitor.py`|Run this file separately to query the BPQ API for AX.25 queue information. Requires setup and enabling in `env.json`|

