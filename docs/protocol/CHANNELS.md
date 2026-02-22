# Protocol - Channels

## Table of Contents 

Singular Types

1. [Type cp - Channel Post](#type-cp---channel-post)
2. [Type cped - Channel Post Edit](#type-cped---channel-post-edit)
3. [Type cpr - Channel Post Response](#type-cpr---channel-post-response)
4. [Type cpem - Channel Post Emoji](#type-cpem---channel-post-emoji)
5. [Type cs - Channel Subscribe](#type-cs---channel-subscribe)
6. [Type pch - Paused Channel Headers](#type-pch---paused-channel-headers)
7. [Type uc - Unpause Channel](#type-uc---unpause-channel)

Batch Variants

1. [Type cpb - Channel Post Batch](#type-cpb---channel-post-batch)
2. [Type cpedb - Channel Post Edit Batch](#type-cpedb---channel-post-edit-batch)
3. [Type cpemb - Channel Post Emoji Batch](#type-cpemb---channel-post-emoji-batch)

[Return to README](/README.md)

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
   "p": "Testing 123"
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
|Emoji Timestamp|`ets`|`1750804825979`|Number|Timestamp of the emoji update|
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

## Type uc - Unpause Channel

Instruction from the client to WPS to unpause a channel, including details on the posts to return. WPS removes the `cid` from `paused_channels` on the user record, then returns a `cpb` containing the requested posts.

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`uc`|String|`uc` for Unpause Channel
|Channel Id|`cid`|`0`|Number|id of the channel|
|**Then one of either**|
|Last Timestamp|`lts`|`1753180608945`|Number|Returns all posts since timestamp
|Post Count|`pc`|`50`|Number|Returns the last `pc` posts in the channel

### JSON Example

Return all posts since last timestamp
``` json
{
   "t": "uc",
   "cid": 0,
   "lts": 1753180608945
}
```

Return the latest 50 posts
``` json
{
   "t": "uc",
   "cid": 0,
   "pc": 50
}
```
