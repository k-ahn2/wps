# Protocol - General

## Table of Contents
1. [Type c - Connect](#type-c---connect)
2. [Type p - Enable Pairing](#type-p---enable-pairing)
3. [Type ue - User Enquiry](#type-ue---user-enquiry)
4. [Type he - Ham Enquiry](#type-he---ham-enquiry)
5. [Type uc and ud - User Connect and User Disconnect](#type-uc-and-ud---user-connect-and-user-disconnect)
6. [Type o - Online Users](#type-o---online-users)
7. [Type u - User Updates](#type-u---user-updates)
8. [Type k - Keep Alive](#type-k---keep-alive)
9. [Type a and ar - Add or Update Avatar](#type-a-and-ar---add-or-update-avatar)
10. [Type ae - Avatar Enquiry](#type-ae---avatar-enquiry)
11. [Type s - Stats](#type-s---stats)
12. [The Connect Sequence Explained](#the-connect-sequence-explained)

[Return to README](/README.md)

## Type c - Connect

Simply, type `c` is a bi-directional exchange of headers between client and server.

This is the first data exchange after connect - the client sends a type `c` object to the server with information to explain the client state, which is mainly based around timestamps of messages and posts. 

The server then returns a type `c` object with information including the new message and post counts. 

But, a type `c` object also triggers the subsequenct sequence of packets required to update the client with all changes since last login. See [The Connect Sequence Explained](#the-connect-sequence-explained) for a detailed explanation

### Client to Server
___

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`c`|String|Always type c|
|Name|`n`|`Tester`|String|User's Name|
|Callsign|`c`|`T3EST`|String|User's Callsign, minus the SSID if added|
|Last Message|`lm`|`1740299150`|Number|Timestamp of last message - seconds since epoch|
|Last Emoji|`le`|`1740266497`|Number|Timestamp of last message emoji - seconds since epoch|
|Last Edit|`led`|`1739318078`|Number|Timestamp of last message edit - seconds since epoch|
|Last Ham Timestamp|`lhts`|`1739318078`|Number|Timestamp of last Ham (i.e. User) update. Currently only changes on Name change|
|Version|`v`|`0.44`|Number|Version of client|
|Channel Connect|`cc`|`[]`|Array of Objects|Contains one JSON object per channel subscribed|
|**Channel Connect Objects**|
|Channel Id|`cid`|`2`|Number|The Channel Id|
|Last Post|`lp`|`1740251305826`|Number|Timestamp of last post - milliseconds since epoch|
|Last Emoji|`le`|`1740252223588`|Number|Timestamp of last post emoji - milliseconds since epoch|
|Last Edit|`led`|`1738869165936`|Number|Timestamp of last post edit - milliseconds since epoch|

### JSON Example

```json
{
   "t": "c",
   "n": "Tester",
   "c": "T3EST",
   "lm": 1740299150,
   "le": 1740266497,
   "led": 1739318078,
   "lhts": 1740292240,
   "v": 0.44,
   "cc":[
      {
         "cid": 3,
         "lp": 1740242101095,
         "le": 1740247089774,
         "led": 1740250727684
      },
      {
         "cid": 4,
         "lp": 1740251305826,
         "le": 1740252223588,
         "led": 1738869165936
      }
   ]
}
```

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`c`|String|Always type ‘c’
|New Message Count|`mc`|`5`|Number|Number of new messages pending download
|Welcome|`w`|`1`|Boolean|Set to 1 if the connect if from a new user, used to display welcome message. Only sent if True
|Version|`v`|`0.44`|Number|Set to the latest recommended or minimum client version. The client can use this to prompt for upgrade if this value is higher than the currently used client version
|New Post Count|`pc`|`35`|Number|Number of new posts pending download

### JSON Example

```json
{
   "t": "c",
   "w": 1,
   "mc": 25,
   "v": 0.44,
   "pc": 35
}
```

## Type p - Enable Pairing

### Client to Server
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`p`|String|Always type `p` for Pairing
|Callsign|`fc`|`T3EST`|String|The callsign of the user entering pairing mode

### JSON Example

```json
{
   "t": "p",
   "fc": "T3EST"
}
```

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`p`|String|Always type `p` for Pairing
|Enabled|`e`|`true`|Boolean|Tells the client pairing is enabled
|Start Time|`st`|`1750799200`|Number|The server start time for pairing, seconds since epoch

### JSON Example

```json
{
   "t": "p",
    "e": true,
    "st": 1750799200
}
```

## Type ue - User Enquiry

Used to determine if a user is registered with WPS

### Client to Server
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`ue`|String|Always type ‘ue’ for User Enquiry
|Callsign|`c`|`T3EST`|String|The callsign of the user you're enquiring about

### JSON Example

```json
{
   "t" : "ue",
   "c": "M8ABC"
}
```

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`ue`|String|Always type `ue` for User Enquiry response
|Registered|`r`|`true` or `false`|Boolean|True if registered
|Callsign|`c`|`M8ABC`|String|Callsign of the user enquired about


### JSON Example

```json
{
   "t": "ue",
   "r": false,
   "c": "M8ABC"
}
```

## Type he - Ham Enquiry

Used by Channels to fetch a user's name. 

### Client to Server
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`he`|String|Always type ‘he’ for Ham Enquiry
|Callsigns as Strings|`h`|`[]`|Array|An array of callsigns, each as a string

### JSON Example

```json
{
   "t": "he",
   "h": [
      "T3EST"
   ]
}
```

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`he`|String|Always type ‘he’ for Ham Enquiry
|Callsigns as Objects|`h`|`[]`|Array|An array of callsign objects
|**Callsign Objects**|
|Callsign|`c`|`T3EST`|String|User's callsign, from the user record in the WPS database
|Name|`n`|`Tester`|String|User's name, from the user record in the WPS database
|Timestamp|`ts`|`1738869165936`|String|The timestamp the user's name was updated, from the user record in the WPS database

### JSON Example

```json
{
   "t": "he", 
   "h": [
      {
         "c": "T3EST", 
         "n": "Tester", 
         "ts": 1738869165936
      }
   ]
}
```

## Type uc and ud - User Connect and User Disconnect

Sent by the server to all connected users when there is a new connect or disconnect

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`uc` or `ud`|String|User Connect or User Disconnect
|Callsign|`c`|`T3EST`|String|Callsign of user connecting or disconnecting

### JSON Example

Connect
```json
{
   "t": "uc",
   "c": "T3EST"
}
```

Disconenct
```json
{
   "t": "ud",
   "c": "T3EST"
}
```
## Type o - Online Users
<hr>

Sent by the server as part of the connect sequence - contains an array of users currently online

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`o`|String|User Connect or User Disconnect
|Callsign Array|`o`|`"M8ABC","T3EST"`|Array|Array of users currently online (i.e. connected)

### JSON Example

```json
{
   "t": "o",
   "o": [
      "M8ABC",
      "T3EST"
   ]
}
```

## Type u - User Updates

> [!IMPORTANT]
> User Updates is depracated and is in the process of being replaced with Ham Enquiry, type `he`.

Sent by the server as part of the connect sequence - contains updated name and last seen times for all users the connecting callsign has messaged, providing it has changed since last login

### Client to Server
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`u`|String|User Connect or User Disconnect
|User Array|`u`|`[]`|Array|Array of user objects
|**User Array Objects**|
|Callsign|`tc`|`M8ABC`|String|The recipient callsign|
|Name|`n`|`Alfred`|Number|Reciient name|
|Last Seen|`ls`|`1740252223`|Number|Timestamp last connected - seconds since epoch|

### JSON Example

```json
{
   "t": "u",
   "u": [
      {
         "tc": "M8ABC",
         "n": "Alfred",
         "ls": 1740252223
      }
   ]
}
```

## Type k - Keep Alive

Recognised by WPS as a Keep Alive, but simply logs receipt and then ceases processing - no further action taken

### Client to Server
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`k`|String|Always `k` for Keep Alive

### JSON Example

```json
{
   "t": "k"
}
```

## Type a and ar - Add or Update Avatar

Adds or Updates an Avatar

### Client to Server
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`a`|String|Always type ‘a’ for type Avatar
|Avatar|`a`|`base64 Image`|String|A base64 encoded image

### JSON Example

```json
{
   "t": "a",
   "a": "data:image/jpeg;base64,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"
}
```

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`ar`|String|Always type ‘ar’ for Avatar Response
|Timestamp|`ts`|`1750799200`|Number|The timestamp the server logged the Avatar update

### JSON Example

```json
{
   "t": "ar", 
   "ts": 1750799200
}
```

## Type ae - Avatar Enquiry

Fetch new Avatars, or a count of new Avatars available

### Client to Server
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`ae`|String|Always type ‘ae’ for type Avatar enquiry
|Timestamp|`lats`|`1750799200`|Number|The timestamp of the last Avatar on the client
|Count Only|`co`|`1`|Boolean|OPTIONAL - if present then return the count only, not the updated avatars

### JSON Example

Fetch new Avatars
```json
{
   "t": "ae", 
   "lats": 1750799200
}
```

Fetch a count of new Avatars
```json
{
   "t": "ae", 
   "lats": 1750799200,
   "co": 1
}
```

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`a`|String|Always type ‘a’ for Avatar
|Timestamp|`ts`|`1750799200`|Number|The timestamp the Avatar was created
|Avatar|`a`|`{}`|Object|An individual Avatar object
|Avatar Count|`ac`|`3`|Number|If `co` is set the request, return the count only and suppress the `a` field

### JSON Example

Return Avatar
```json
{
   "t": "a",
   "c": "M8ABC",
   "ts": 1750799200,
   "a": "data:image/jpeg;base64,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"
}
```

Return Avatar Count
```json
{
   "t": "a",
   "ac": 3
}
```

## Type s - Stats

Fetch Server Stats, as defined in `stats.py`

### Client to Server
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`s`|String|Always type `s` for type Stats

```json
{
   "t": "s"
}
```

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`s`|String|Always type ‘s’ for Stats
|Type|`s`|`{}`|Object|See below


```json
{
   "h": { // headers, can include any key / value pair defined
      "uculsd": 1 // Unique Connecting Users Last Seven Days 
   }, 
   "p": [], // post stats
   "m": [], // message stats
   "s": [], // server stats
}
```

All stats arrays (`p`, `m`, `s`) return objects with two keys:

```json
{
      "s": "Total Posts", // Friendly stat description
      "v": 16144 // Stat value
}
```

### JSON Example

```json
{
   "t": "s",
   "s": {
      "h": {
         "uculsd": 1
      },
      "p": [
         {
               "s": "Total Posts",
               "v": 16144
         },
         {
               "s": "Posts Today So Far",
               "v": 34
         },
         {
               "s": "Total Posts Last 7 Days",
               "v": 234
         },
         {
               "s": "Total Posts Last 30 Days",
               "v": 516
         },
      ],
      "m": [
         {
               "s": "Total Messages",
               "v": 6723
         },
         {
               "s": "Messages Today So Far",
               "v": 16
         },
         {
               "s": "Total Messages Last 7 Days",
               "v": 35
         },
         {
               "s": "Total Messages Last 30 Days",
               "v": 105
         }
      ],
      "s": [
         {
               "s": "Bytes Sent Today So Far",
               "v": 1956
         },
         {
               "s": "Bytes Sent Previous 7 Days",
               "v": 8045
         },
         {
               "s": "Bytes Sent Previous 30 Days",
               "v": 4210504
         }
      ]
    }
}
```

## The Connect Sequence Explained

### Overview
The type `c` handling is the most complex in WPS - it triggers the chain of responses required to update the client with all changes that are pending. 

This is the first data exchange after connect - the client sends a type `c` object to the server. The server uses this data to determine the client state and which messages, posts and/or other updates to return.

### Existing Connect - Last Message Timestamp > 0
<hr>
This covers users that have already registered and have data on the client - the most common scenario.

Upon receipt, WPS returns:
1. A type `c` object sent to the client, containing:
   - the new message and post counts by channel, even if zero
   - whether there is a version upgrade
2. as required:
   - new messages, sent in batches of 4 as type `mb`
   - new message emojis, sent in one batch as type `memb`
   - new message edits, sent in one batch as type `medb`
   - new posts or paused channel headers, depending on:
      - if <= `maxNewPostsToReturnPerChannelOnConnect`, send backlog of posts in batches of 4 as type `cpb`
      - if > `maxNewPostsToReturnPerChannelOnConnect`, return channel headers as type `pch`
   - new post emojis, sent in one batch as type `cpemb`
   - new post edits, sent in one batch as type `cpedb`
   - updated last seen times and name changes as type `u`, for Messaged users
   - updated name changes as type `he`, for Channel users
   - online users as type `o`

The connect processing ensures data is only returned once. For example, if a user connects with a last message timestamp of `1740299150`, it will return:
1. any edits, emojis added and emojis removed for messages already on the client (on or before `1740299150`)
2. all new messages after `1740299150`, which already includes the latest edit and/or emojis

Post handling follows the same logic, by channel subscribed

### New User or New Browser - Last Message Timestamp = 0
<hr>
This covers both scenario where there is no data on the client, either because a) the user has just registered for the first time, or c) the user has connected in a new browser.


For a new user, WPS returns
1. A type `c` object sent to the client, containing:
   - new message counts (because they could have messages waiting)
   - a welcome flag set, of this is the first time the user has connected
   - whether there is a version upgrade
2. New messages, sent individually as type `m`

For an existing user connecting with new browser, WPS returns
1. A type `c` object sent to the client, containing:
   - new message counts (because they could have messages waiting)
   - whether there is a version upgrade
2. All recipients the user has ever communicated with, sent to repopulate the recipient list in the browser, sent as type `u`
3. The last 10 messages exchanged with each recipient, sent in batches of 10 as type `mb`

> [!NOTE]
> There is currently no way to recover every message ever sent if a user connects from a new browser. WPS will implement a method for handling this in future


