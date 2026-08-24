# WPS - A Messaging Service and Protocol for Packet Radio

WPS is a backend service and Layer 7 application protocol that provides messaging services over Packet Radio. Currently built to be published via a BPQ or Xrouter node, WPS is directly exposed to the AX.25 packet network and can be systematically accessed by end user applications. 

WPS was built specifically to enable the functionality in the WhatsPac front end, but could be used by any Packet Radio messaging application that implements its protocol.

WPS is capable of operating effectively without any internet dependency over link speeds of 1200 baud, albeit the latest 2400 and 3600 baud speeds offered by the NinoTNC are typically used and are the recommended minimum.

WPS runs entirely in Python, starts with just three files, has minimal dependencies, minimal setup and runs with single command. It can be run manually or as a service.

> [!IMPORTANT]
> WPS is in active development and is changing on a regular basis - please remember to watch the repo to be alerted when there are new versions

## Table of Contents
1. [WPS Schematic](#wps-schematic)
2. [Key functions](#key-functions)
3. [Server Capabilities](#server-capabilities)
4. [Future](#future)
5. [How WPS Works - An Overview](#how-wps-works---an-overview)
6. [Timestamps and Delivery Sequence](#timestamps-and-delivery-sequence)
7. [How WPS handles JSON](#how-wps-handles-json)
8. [Sending a JSON object to WPS (Javascript Example)](#sending-a-json-object-to-wps---a-javascript-example)
9. [Bots](#bots)


### WPS Installation and Protocol Documentation

Links to documentation in the `/docs` directory

1. [Installation](docs/installation/INSTALLATION.md)
2. [Protocol - General](docs/protocol/GENERAL.md)
3. [Protocol - Channels](docs/protocol/CHANNELS.md)
4. [Protocol - Messages](docs/protocol/MESSAGES.md)


## WPS Schematic
<img src="docs/wps.png" alt="blah" width="500px"/>

## Key Functions
- **Direct Messaging:** Message send and receive (similar to SMS, WhatsApp, Signal or iMessage)
- **Channels:** Post to themed channels (similar to a Rocket Chat, Slack or Discord)
- **Who is Online:** WPS updates clients when a user connects or disconnects
- **Reply:** Users can send new messages and posts, or reply to existing
- **Emojis:** Include and react to messages and posts with Emojis
- **Edits:** Edit messages and posts after sending
- **Auto Registration:** New users are automatically registered upon connecting
- **Push Notifications:** Send push notfications when there is new activity (requires integration with a Push notification service)
- **Callsign Lookup:** Enquire if a callsign is registered
- **Name Change:** WPS distrbutes display name updates, if changed
- **Last Seen Times:** See when users you have messaged were last connected
- **Events:** Capture events in a separate database for monitoring and analytics - e.g. user connected, user disconnected, bytes sent
- **Delivery Receipts:** WPS responds to new and edited messages and posts with a delivery receipt, guaranteeing server delivery
- **Version Control:** Advise the client a new software version is available, configurable within WPS in real-time
- **User Avatars:** Attach custom avatars / images
- **BOT Framework:** Build custom BOTs that integrate with channels

## Server Capabilities
- **Compression:** WPS compresses every packet before sending, then sends whichever of the compressed or uncompressed version is shorter
- **Data Batching:** WPS batches bulk post and message downloads, optimising compression and delivery
- **Logging:** WPS includes error logging by default, with extensive info logging configurable if required
- **Run as Service:** WPS runs as a standard linux service (and assume could on Windows too)

## Future
- **Replication:** Supporting the ability to replicate to other WPS instances hosted on the Packet Network
- **Websocket / REST APIs:** For connecting directly over TCP/IP, it's the intent that WPS will offer Websocket and REST APIs for access. Possible use cases are via Hamnet or local sysop access

## How WPS Works - An Overview

WPS is designed for system access only - it does not provide a human interface for direct user access. To connect to WPS:

1. An application opens an AX.25 connection to the node hosting WPS
2. The application sends `WPS` (or whichever name configured) and the node opens the TCP connection to WPS. WPS expects the first string to be the connecting user's callsign 
3. The application then sends JSON compatible with the WPS Protocol and returns the corresponding JSON in response

> [!TIP]
> BPQ and Xrouter support publicising an application directly onto the AX.25 network with a callsign and alias. If configured, steps 1 and 2 can be merged. A connecting application can invoke WPS directly upon connecting

WPS is a reactive service - activity is only triggered upon receipt of an instruction from a connected user. It is also connection aware - if it recieves a message from T3EST addressed to M8ABC and M8ABC is connected, WPS will send the message to M8ABC in real-time

As an example, the sequence for a new message is:
1. WPS receives a type `m` JSON object from a connected application, meaning a new message
2. WPS writes the message to the database
3. WPS returns a delivery receipt to the connected application. 
4. WPS then decides:
   - if the recipient is connected, send in real-time
   - if the recipient is not connected, check whether registered for push notifications, send one if yes (NB: requires additional integrations)
   - if the recipient is not registered, end processing
5. If not sent in real-time, when the recipient connects and sends a type `c` JSON object, WPS will then return the new message(s)

## Timestamps and Delivery Sequence

Timestamps are used extensively in the design of WPS:
- Due to the potential variability within the RF packet network - where delivery times to the server can very depending on the number of hops, traffic and/or network drop outs - the timestamp assigned to a message or post by a connected application is used on both the server and all recipients. This ensures the sequencing of posts and messages remains as intended by the sender
- For Posts, the timestamp is also used to:
  - tell the sender how long a message took to reach the WPS by returning the server delivery timestamp `dts` to the client
  - tell connected recipients of a post the end-to-end delivery time, calculated by comparing the `ts` of the post to the timestamp it was received
- When downloading new posts, either on connect, subscribe or in real time, WPS always sends Posts and Messages in timestamp ASCENDING order - i.e. oldest first. This ensures if a user gets disconnected, the client can resume from the last message.

> [!WARNING]
> WPS works on the assumption that modern OSs have time synchronisation and therefore accurate clocks. Beware if offline or time synchronisation is not setup that sending posts and messages with an incorrect system clock could cause issues with certain functions

## How WPS handles JSON

WPS receives everything from the packet network and node as a string, with discrete packets delimited by `\r\n` (13 decimal & 10 decimal), Additional delimiters are used for compressed packets. 

WPS preprocesses received strings by:
- adding the string to an RX buffer
- splits the buffer on `\r\n` into an array
- for each string in the array:
   - if enclosed in compression delimiters, attempt decompression
   - if enclosed in `{}`, attempt conversion to a JSON object
   - if last in the array and is neither, it's imcomplete and is returned to the RX buffer, awaiting the next packet

If either of the decompression and/or JSON conversion fails, this is considered a FATAL error and WPS disconnects the user and log an `ERROR` in `wps.log`

The only exceptions to the above are the first and second strings received:
- The first string recieved is always the callsign - e.g. `T3EST\r\n`. This is sent by the node and happens before any subsequent processing
- If the second string fails conversion, this is likely a manual connect by a human. WPS returns a friendly message (configurable in `wps.py`) and then disconnects

> [!IMPORTANT]
> WPS strips the SSID, if one is received from the node. The WPS user is always the callsign minus any SSID.

> [!NOTE]
> The use of JSON offered many advantages when developing WPS:
> <br> 1. Conversion to JSON offers a very simple way of assuring data integrity across a number of fragmented packets, a feature of the varying PACLENs used across the network
> <br> 2. JSON is simple to use by both WPS and connected applications
> <br> 3. JSON offers complete flexibility to add / amend data when required
> <br> 4. JSON compresses well due to its repetitive use of certain characters. Overall compression typically achieves up to a 40% reduction in packet length
> <br><br>WPS could easily add support for a different payload construct without material effort. It already recognises and supports two payload types - compressed and native JSON

## Sending a JSON object to WPS - A Javascript Example

With an open channel to WPS, connected applications should:
1. Convert the JSON object to a string via `JSON.stringify` (Javascript), `json.dumps` (Python) or equivalent
2. Add a `chr(13)` and `chr(10)` or `\r\n` or equivalent, then send.

Javascript Example:

```javascript
const samplePost = {
   "t": "cp",
   "cid": 1,
   "fc": "T3EST",
   "ts": 1771755000289,
   "p": "1"
}
send(`${JSON.stringify(samplePost)}\r\n`)
```

## Bots

WPS includes a lightweight bot framework that allows channel bots to be added without modifying the core WPS code. Each bot is a self-contained Python module placed in the `bots/` directory.

Bots are only loaded if `botsEnabled` is set to `true` in `env.json` - when `false` (the default), `bots/bots.json` is not read and no bots are started.

`bots/bots.json` is the master record of which bots are active. For every key in `bots/bots.json`, WPS expects:
- A matching `bots/<name>.py` module
- A channel in `channels.json` with the configured `cid`, flagged `"b": true`

If either is missing, WPS logs an error at startup and skips loading that bot.

### Built-in Bot: Pacagotchi

Pacagotchi is a Tamagotchi-style pet that lives in a WPS channel and is cared for collectively by everyone on the network.

**Setup:** `botsEnabled` must be `true` in `env.json`, its channel in `channels.json` must be flagged `"b": true`, and it must be registered in `bots/bots.json`, keyed by bot name (the module under `bots/` to import). The `bots/bots.json` value is an object holding at least the channel id (`cid`) it should respond on - the bot also reads its parameters from here:

```json
{
    "pacagotchi": {
        "cid": 14,
        "tick_interval": 300,
        "age_juvenile": 3600,
        "age_adult": 172800,
        "hunger_drop_per_tick": 2,
        "happiness_drop_bored": 2,
        "health_drop_starving": 4,
        "health_drop_dirty": 2,
        "health_drop_ill_late": 8,
        "sleep_tick_multiplier": 0.25,
        "auto_sleep_after": 10800,
        "sleep_min_hours": 6,
        "sleep_max_hours": 10,
        "poop_rise_every_n_ticks": 7,
        "junk_illness_threshold": 3,
        "ill_death_timeout": 10800
    }
}
```

**Commands** (posted to the channel):

| Command | Description |
| - | :- |
| `/spawn` | Create a new pet (only when none exists or the previous one died) |
| `/feed [food]` | Feed the pet. Junk food keywords (`burger`, `pizza`, `chips`, `sweets`, `cake`, `donut`, `crisps`, `biscuit`) make it happier but risk illness after 3 in a row |
| `/play` | Play with the pet — raises happiness but costs hunger |
| `/clean` | Clean up poop — neglected poop causes illness |
| `/medicate` | Cure illness (requires health > 20%) |
| `/sleep` | Rest the pet — raises health, lowers happiness |
| `/pet` | Show the pet's current status and ASCII art |
| `/stats` | Full stats including top caretakers leaderboard |
| `/help` | Command reference |

The pet ticks every 5 minutes: hunger drops, poop accumulates, and health is affected by neglect. If health reaches zero the pet dies — use `/spawn` to start fresh.

### Adding a New Bot

1. Create a Python file in `bots/`, e.g. `bots/mybot.py`, exposing these three functions:

```python
def init(db_connection):
    """Called once at startup. Create any required DB tables here."""
    pass

def start_tick_thread(db_connection, broadcast_fn, channel_id):
    """
    Start a background thread for time-driven behaviour (optional).
    broadcast_fn(cursor, cid, text, from_callsign) posts a message to the channel.
    """
    pass

def handle_command(cursor, post_text, from_callsign):
    """
    Called when a slash command is posted to the bot's channel.
    Return {"text": str, "fc": str} to reply, or None to stay silent.
    """
    if not post_text.startswith("/"):
        return None
    return {"text": "Hello from mybot!", "fc": "MYBOT"}
```

2. Flag its channel `"b": true` in `channels.json`:

```json
{
    "cgid": 3,
    "cid": 12,
    "cn": "mybot",
    "cd": "My Bot",
    "b": true
}
```

3. Register it in `bots/bots.json`, keyed by bot name, with an object holding at least the channel id (`cid`) it should respond on. Add any other bot-specific config keys your module wants to read from that same object:

```json
{
    "mybot": {
        "cid": 12
    }
}
```

4. Ensure `botsEnabled` is `true` in `env.json`, then restart WPS — the bot is loaded automatically with no other changes required.