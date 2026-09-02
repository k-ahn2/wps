# Protocol - Channels

## Table of Contents 

Overview

1. [Client to Server / Server to Client Responses](#client-to-server--server-to-client-responses)

Singular Types

1. [Type cp - Channel Post](#type-cp---channel-post)
2. [Type cped - Channel Post Edit](#type-cped---channel-post-edit)
3. [Type cpr - Channel Post Response](#type-cpr---channel-post-response)
4. [Type cpem - Channel Post Emoji](#type-cpem---channel-post-emoji)
5. [Type cs - Channel Subscribe](#type-cs---channel-subscribe)
6. [Type pch - Paused Channel Headers](#type-pch---paused-channel-headers)
7. [Type cu - Channel Unpause](#type-cu---channel-unpause)
8. [Type chl - Channel List](#type-chl---channel-list)

Batch Variants

1. [Type cpb - Channel Post Batch](#type-cpb---channel-post-batch)
2. [Type cpedb - Channel Post Edit Batch](#type-cpedb---channel-post-edit-batch)
3. [Type cpemb - Channel Post Emoji Batch](#type-cpemb---channel-post-emoji-batch)

Bots

1. [Bots - Calling the Channel Post Handler Directly](#bots---calling-the-channel-post-handler-directly)

[Return to README](/README.md)

## Client to Server / Server to Client Responses

Every Client to Server action in this document has a corresponding Server to Client response. **The only exception is emoji updates** ([`cpem`](#type-cpem---channel-post-emoji)), where WPS deliberately sends no delivery confirmation back to the sending client - see the [`cpem`](#type-cpem---channel-post-emoji) section for the rationale.

Most channel actions produce **two** distinct server responses: one back to the client that sent the action, and one out to the other connected clients affected by it (typically the channel's subscribers). Clients that are offline when an action occurs pick up the applicable object at their next connect.

| Client to Server | Response to the sending client | Response to other connected clients |
| - | - | - |
| [`cp`](#type-cp---channel-post) - Channel Post | [`cpr`](#type-cpr---channel-post-response) delivery receipt (unless suppressed - see [Bots](#bots---calling-the-channel-post-handler-directly)) | [`cp`](#type-cp---channel-post) to every connected subscriber of the `cid` |
| [`cped`](#type-cped---channel-post-edit) - Channel Post Edit | [`cpr`](#type-cpr---channel-post-response) delivery receipt | [`cped`](#type-cped---channel-post-edit) to every connected subscriber of the `cid` |
| [`cpem`](#type-cpem---channel-post-emoji) - Channel Post Emoji | *None* - no delivery confirmation is sent | [`cpem`](#type-cpem---channel-post-emoji) (latest full emoji state for the post) to every connected subscriber of the `cid` |
| [`cs`](#type-cs---channel-subscribe) - Channel Subscribe | [`cs`](#type-cs---channel-subscribe) confirming the subscribe / unsubscribe (with new post count on subscribe) | *None* |
| [`cpb`](#type-cpb---channel-post-batch) - Channel Post Batch | [`cpb`](#type-cpb---channel-post-batch) containing the requested posts | *None* |
| [`cu`](#type-cu---channel-unpause) - Channel Unpause | [`cpb`](#type-cpb---channel-post-batch) containing the requested posts | *None* |
| [`chl`](#type-chl---channel-list) - Channel List | [`chl`](#type-chl---channel-list) containing the channel list (or count-only result) | *None* |

## Type cp - Channel Post

Sends a new Post to a given channel

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cp`|String|Always type `cp` for Channel Post|
|Channel Id|`cid`|`1`|Number|id of the channel|
|From Call|`fc`|`T3EST`|String|Callsign of the sender|
|Timestamp|`ts`|`1750804825979`|Number|Milliseconds since epoch|
|Post|`p`|`Testing 123`|String|The posted message|
|**Optional Fields**|
|Reply Timestamp|`rts`|`1750804825979`|Number|The timestamp of the post being replied to
|Reply From Call|`rfc`|`T3EST`|String|The sender of the post being replied to
|Gap|`g`|`1`|Boolean|If a user doesn't request all outstanding posts for a channel, this flag signifies the first new post after the posts gap
|Receipt|`r`|`0`|Number|Set to `0` to suppress the `cpr` delivery receipt for this post - see [Bots - Calling the Channel Post Handler Directly](#bots---calling-the-channel-post-handler-directly)
|**Server Only Fields**|
|Delivery Timestamp|`dts`|`1750804826875`|Number|The timestamp the server received and processed the message. This is returned to the client in the `cpr` response for the client to calculate the delivery time to server

### JSON Example

A simple Post
```json
{
   "t": "cp",
   "cid": 6,
   "fc": "T3EST",
   "ts": 1750804825979,
   "p": "Testing 123",
}
```

A reply to a Post
```json
{
   "t": "cp",
   "cid": 6,
   "fc": "T3EST",
   "ts": 1750805783394,
   "p": "Blah",
   "rts": 1750804825979,
   "rfc": "T3EST"
}
```

The first post after a posts gap, when a user chooses not to donwload all oustanding posts
```json
{
   "t": "cp",
   "cid": 6,
   "fc": "T3EST",
   "ts": 1750804825979,
   "p": "Testing 123",
   "g": 1
}
```

### Server to Client

A `cp` triggers two server responses:

1. **To the sending client** - WPS returns a [`cpr` - Channel Post Response](#type-cpr---channel-post-response) delivery receipt, confirming the post was received and processed. This is suppressed if the post was sent with `r: 0` - see [Bots - Calling the Channel Post Handler Directly](#bots---calling-the-channel-post-handler-directly).
2. **To the channel's subscribers** - WPS looks up every subscriber for the given `cid` and, for each one currently connected, relays the `cp` object in real-time. Subscribers who are offline receive it at their next connect (via [`cpb` - Channel Post Batch](#type-cpb---channel-post-batch)).

## Type cped - Channel Post Edit

Edit an existing Post

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cped`|String|Always type `cped` for Channel Post Ecit
|Channel Id|`cid`|`6`|Number|id of the channel|
|Timestamp|`ts`|`1750804825979`|Number|Timestamp of original post|
|Post|`p`|`Testing 123`|String|The updated post|
|Edited Timestamp|`edts`|`1750804825979`|Number|Timestamp of the edit|

### JSON Example

```json
{
   "t": "cped",
   "cid": 6,
   "ts": 1750804825979,
   "p": "Test1",
   "edts": 1750805550246
}
```

### Server to Client

A `cped` triggers two server responses:

1. **To the sending client** - WPS returns a [`cpr` - Channel Post Response](#type-cpr---channel-post-response) delivery receipt, confirming the edit was received and processed.
2. **To the channel's subscribers** - WPS looks up every subscriber for the given `cid` and, for each one currently connected, relays the `cped` object in real-time. Subscribers who are offline receive it at their next connect (via [`cpedb` - Channel Post Edit Batch](#type-cpedb---channel-post-edit-batch)).

## Type cpr - Channel Post Response

The server confirmation it has received a new Post or a Post edit

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cpr`|String|Always type `cpr` for Channel Post Response
|Timestamp|`ts`|`1750804825979`|Number|Timestamp of the post
|Delivery Timestamp|`dts`|`1750804827975`|Number|The timestamp the server received and processed the message. Used by the client to calculate the delivery time to server

### JSON Example

```json
{
   "t": "cpr",
   "ts": 1750804825979,
   "dts": 1750804827975
}
```

## Type cpem - Channel Post Emoji

Add or remove an emoji to / from a Post.

WPS doesn't send delivery confirmation responses for emoji additions or removals - they are not deemed essential to the integrity of WPS. 

If the emoji reaches the server, it should always be delivered to the connected client in real-time, or, get picked up at next connect.

There are some edge cases where a client could send an emoji and the packet network fails before delivery to the server. In this edge case, the sender may see the emoji on their client, but it hasn't been delivered.

Ater every emoji add or remove, both for real-time connections and during the connect sequence, WPS will always return the latest full emoji state for a message. For example, if a message has 1 emoji and 2nd is added, WPS will return both 1st and 2nd in the update.

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cpem`|String|`cpem` for Channel Post Emoji 
|Action|`a`|`1` or `0`|String|`1` for Emoji Add or `0` for Emoji Remove
|Timestamp|`ts`|`1750361450494`|Number|The ts of post to add or remove the emoji
|Channel Id|`cid`|`6`|Number|id of the channel|
|Emoji Timestamp|`ets`|`1750804825979`|Number|Timestamp of the emoji update in MILLISECONDS|
|Emoji|`e`|`1f44d`|String|The unicode value of the emoji to add or remove

### JSON Example

Emoji Add
```json
{
   "t": "cpem",
   "a": 1,
   "ts": 1750361450494,
   "cid": 6,
   "ets": 1750804825979,
   "e": "1f44d"
}
```

Emoji Remove
```json
{
   "t": "cpem",
   "a": 0,
   "ts": 1750361450494,
   "cid": 6,
   "ets": 1750804825979,
   "e": "1f44d"
}
```

### Server to Client

If the recipient of the Emoji is connected in real-time, WPS relays the same `cpem` object

## Type cs - Channel Subscribe

Subscribe or unsubscribe from a channel

Updates `channel_subscriptions` on the user record - see [The User Object: write paths 9 & 10](/docs/protocol/USER.md#9-channel-subscribe-protocol-type-cs-s--1).

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cs`|String|`cs` for Channel Subscribe
|Subscribe|`s`|`1`|Number|`1` to subscribe, `0` to unsubscribe
|Channel Id|`cid`|`6`|Number|id of the channel|
|Last Channel Post|`lcp`|`1750361450494`|Number|Usually 0 because the user hasn't previously subscribed, but will send the `ts` of the last post for this channel if one exists on the client 

### JSON Example

Channel Subscribe
```json
{
   "t": "cs",
   "s": 1,
   "cid": 1,
   "lcp": 0
}
```

Channel Unsubscribe
```json
{
   "t": "cs",
   "s": 0,
   "cid": 1,
   "lcp": 0
}
```

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cs`|String|`cs` for Channel Subscribe
|Channel Id|`cid`|`6`|Number|id of the channel|
|Subscribe|`s`|`1`|Number|`1` to confirm subscribed, `0` to confirm unsubscribed
|Post Count|`pc`|`25`|Number|Only applicable for Subscribe, this is the number of new posts in the channel. Used by the client to prompt the user how many to download

### JSON Example

``` json
{
   "t": "cs", 
   "cid": 1, 
   "s": 1, 
   "pc": 0
}
```

``` json
{
   "t": "cs", 
   "cid": 1, 
   "s": 0, 
}
```

## Type cpb - Channel Post Batch

Request and send a batch of channel Posts

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cpb`|String|`cpb` for Channel Post Batch
|Channel Id|`cid`|`6`|Number|id of the channel|
|Post Count|`pc`|`17`|Number|The number of posts to return. Would return the last 17 posts in the channel, sent to the client in ascending (oldest first) order 

### JSON Example

```json
{
   "t": "cpb",
   "cid": 6,
   "pc":17
}
```

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cpb`|String|`cpb` for Channel Post Batch
|Channel Id|`cid`|`6`|Number|id of the channel|
|Meta|`m`|`{}`|Object| pt = Post Total, in the overall batch <BR>pc = Post Count, the cumulative total after this batch is processed<br>```{ "pt": 17, "pc":4 }```
|Posts|`p`|`[]`|Array|Array of `cp` objects to return to the client. Would include any applicable post fields if added - e.g. emojis, edit and reply

### JSON Example

``` json
{
   "t": "cpb",
   "cid": 6,
   "m": {
      "pt": 17,
      "pc":4
   },
   "p": [
      {
         "fc": "M8ABC",
         "ts": 1750359728258, 
         "p": "Test 1"
      },
      {
         "fc": "T3EST",
         "ts": 1750359773884,
         "p": "Test 2"
      },
      {
         "fc": "T3EST",
         "ts": 1750359775310,
         "p": "Test 3"
      },
      {
         "fc": "T3EST",
         "ts": 1750359846362, 
         "p": "Test 4"
      }
   ]
}
```

## Type cpedb - Channel Post Edit Batch

Send a batch of Post edits

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cpedb`|String|`cpedb` for Channel Post Edit Batch|
|Edits|`e`|`[]`|Array of Objects|Array of edit update objects to apply|
|**Edit Objects**|
|Channel Id|`cid`|`6`|Number|id of the channel|
|Timestamp|`ts`|`1750361450494`|Number|The `ts` of post to apply the edit|
|Edit Timestamp|`edts`|`1750804825979`|Number|Timestamp of the edit|
|Post|`p`|`Edited post 1`|String|The updated post|

### JSON Example

``` json
{
   "t": "cpedb", 
   "ed": [
      {
         "cid": 6, 
         "ts": 1753218544884, 
         "edts": 1753219900378, 
         "p": "Edited post 1"
      }, 
      {
         "cid": 6, 
         "ts": 1753218545801, 
         "edts": 1753219905540, 
         "p": "Edited post 2"
      }, 
      {
         "cid": 6, 
         "ts": 1753218546168, 
         "edts": 1753219911479, 
         "p": "Edited post 3"
      }, 
      {
         "cid": 6, 
         "ts": 1753218546559, 
         "edts": 1753219914900, 
         "p": "4Edited post 4"
      }
   ]
}
```

## Type cpemb - Channel Post Emoji Batch

Send a batch of emoji updates. Always sends the latest complete view of emojis for a Post

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cpemb`|String|`cpemb` for Channel Post Emoji Batch
|Emojis|`e`|`[]`|Array of Objects|Array of emoji update objects to apply
|**Emoji Objects**|
|Channel Id|`cid`|`6`|Number|id of the channel|
|Timestamp|`ts`|`1750361450494`|Number|The `ts` of post to add or remove the emoji
|Emoji Timestamp|`ets`|`1750804825979`|Number|Timestamp of the emoji update|
|Callsigns|`c`|`[]`|Array|Array of callsigns who have applied this emoji

### JSON Example

``` json
{
   "t": "cpemb", 
   "e": [
      {
         "cid": 5, 
         "ts": 1753180608945, 
         "ets": 1753190718755, 
         "e": [
            {
               "e": "1f44d", 
               "c": [ "M1BFP", "2E0HKD", "T3EST"]
            }
         ]
      }
   ]
}
```

## Type pch - Paused Channel Headers

Returned when the number of pending posts in a given channel is greater than the `maxNewPostsToReturnPerChannelOnConnect` env variable. Returns a count of posts per channel, allowing the client application to give the user options - e.g. download everything, or download the last x posts

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`pch`|String|`pch` for Paused Channel Headers
|Channel Headers|`ch`|`[]`|Array of Objects|Array of channels and post counts
|**Channel Headers**|
|Channel Id|`cid`|`0`|Number|id of the channel|
|Posts Total|`pt`|`712`|Number|Number of pending posts|

### JSON Example

``` json
{
   "t": "pch",
   "ch": [
      {
         "cid": 0,
         "pt":  712
      },
      {
         "cid": 6,
         "pt":  152
      },
   ]
}
```

## Type cu - Channel Unpause

Instruction from the client to WPS to unpause a channel, including details on the posts to return. WPS removes the `cid` from `paused_channels` on the user record, then returns a `cpb` containing the requested posts. See [The User Object: write path 11](/docs/protocol/USER.md#11-unpause-channel-protocol-type-cu).

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`cu`|String|`cu` for Channel Unpause
|Channel Id|`cid`|`0`|Number|id of the channel|
|**Then one of either**|
|Last Timestamp|`lts`|`1753180608945`|Number|Returns all posts since timestamp
|Post Count|`pc`|`50`|Number|Returns the last `pc` posts in the channel

### JSON Example

Return all posts since last timestamp
``` json
{
   "t": "cu",
   "cid": 0,
   "lts": 1753180608945
}
```

Return the latest 50 posts
``` json
{
   "t": "cu",
   "cid": 0,
   "pc": 50
}
```

## Type chl - Channel List

Fetches the channel list, sourced from `channels.json`. The entire contents of `channels.json` are cached in memory on startup, shared by all connections, and re-checked against the file on disk on every new client connect - if it has changed, the cache and the `channels` table are refreshed with a fresh timestamp. This means `channels.json` can be edited to add, rename or remove channel groups and channels without restarting WPS - the updated list becomes available within one connect cycle.

Also supports a count-only mode, letting the client cheaply check whether it needs to download an updated list before requesting the full one.

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`chl`|String|Always type `chl` for Channel List|
|Last Channels Timestamp|`lcts`|`1755000000000`|Number|The timestamp of the channel list already held by the client, in milliseconds since epoch. `0` if none held|
|**Optional Fields**|
|Count Only|`co`|`1`|Boolean|If present, the server only returns whether an update is available (and the current timestamp), suppressing the full channel list|

### JSON Example

Fetch the full channel list
```json
{
   "t": "chl",
   "lcts": 0
}
```

Check whether an update is available, without downloading the full list
```json
{
   "t": "chl",
   "lcts": 1755000000000,
   "co": 1
}
```

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`chl`|String|Always type `chl` for Channel List|
|Timestamp|`ts`|`1755000100000`|Number|The timestamp the channel list held by the server was last changed, in milliseconds since epoch|
|Update Available|`u`|`1` or `0`|Boolean|Only present when `co` was set - `1` if `ts` is newer than the client's `lcts`, otherwise `0`|
|Channel Groups|`cg`|`[]`|Array of Objects|Only present when `co` was not set. The entire `cg` contents of `channels.json`|
|Channels|`c`|`[]`|Array of Objects|Only present when `co` was not set. The entire `c` contents of `channels.json`|
|**Channel Group Objects**|
|Channel Group Id|`cgid`|`1`|Number|id of the channel group|
|Group Name|`gn`|`Packet`|String|The channel group's display name|
|**Channel Objects**|
|Channel Group Id|`cgid`|`1`|Number|id of the channel group this channel belongs to. Present when the channel belongs to a group - see `gid` below for the ungrouped case|
|Channel Id|`cid`|`1`|Number|id of the channel|
|Name|`cn`|`packet-general`|String|The channel's display name|
|Description|`cd`|`Anything Packet Radio goes here!`|String|The channel's description|
|**Optional Channel Fields**|
|Group Id (Ungrouped)|`gid`|`null`|null|Present instead of `cgid`, and always `null`, when the channel doesn't belong to a channel group|
|Auto Subscribe|`as`|`true`|Boolean|If present and `true`, clients should auto-subscribe users to this channel|
|Read Only|`ro`|`true`|Boolean|If present and `true`, the channel is read-only - e.g. announcements posted by the server|
|Bot|`b`|`true`|Boolean|If present and `true`, this channel is bot-managed. `bots/bots.json` is the master record of active bots - for each key there, WPS requires a channel here with the matching `cid` flagged `"b": true`, and a `bots/<name>.py` module. Bots are only loaded if `botsEnabled` is `true` in `env.json`. See [Bots in the README](/README.md#bots)|

### JSON Example

Full channel list response
```json
{
   "t": "chl",
   "ts": 1755000100000,
   "cg": [
      { "cgid": 0, "gn": "General" },
      { "cgid": 1, "gn": "Packet" }
   ],
   "c": [
      { "cgid": 0, "cid": 0, "cn": "packet-tech", "cd": "Packet technical discussion" },
      { "cgid": 1, "cid": 1, "cn": "packet-general", "cd": "Anything Packet Radio goes here!" },
      { "gid": null, "cid": 100, "cn": "announcements", "cd": "General news and announcements relevant to the community", "as": true, "ro": true },
      { "cgid": 3, "cid": 14, "cn": "pacagotchi", "cd": "Pacagotchi", "b": true }
   ]
}
```

Count-only response
```json
{
   "t": "chl",
   "ts": 1755000100000,
   "u": 1
}
```

## Bots - Calling the Channel Post Handler Directly

A bot running in-process with WPS (i.e. imported into the same Python process, rather than connecting as a packet client) can skip the socket/packet layer entirely and call `post_handler()` directly.

```python
post_handler(CONN_DB_CURSOR, post, callsign, CONN, suppress_cpr=True)
```

`post` is a standard [`cp` object](#type-cp---channel-post). `suppress_cpr` defaults to `False`; set it to `True` so WPS doesn't attempt to deliver a `cpr` back to the bot, since there is no client connection to receive it.

### Bots Posting Over a Packet Connection

A bot that instead connects like a normal client over the packet network doesn't have access to the `suppress_cpr` argument directly. In this case, add `r: 0` to the `cp` object it sends - this tells WPS to suppress the `cpr` for that post.

``` json
{
   "t": "cp",
   "cid": 6,
   "fc": "BOT1",
   "ts": 1750804825979,
   "p": "Testing 123",
   "r": 0
}
```
