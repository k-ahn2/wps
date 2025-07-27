# Protocol - General

## Table of Contents
1. [Type c - Connect](#type-c---connect)
2. [Type p - Enable Pairing](#type-p---enable-pairing)
3. [Type ue - User Enquiry](#type-ue---user-enquiry)
4. [Type he - Ham Enquiry](#type-he---ham-enquiry)
5. [Type uc and ud - User Connect and User Disconnect](#type-uc-and-ud---user-connect-and-user-disconnect)
6. [Type o - Online Users](#type-o---online-users)
7. [Type u - User Updates](#type-u---user-updates)
8. [The Connect Sequence Explained](#the-connect-sequence-explained)

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
|Name|`n`|`Kevin`|String|User's Name|
|Callsign|`c`|`M0AHN`|String|User's Callsign, minus the SSID if added|
|Last Message|`lm`|`1740299150`|Number|Timestamp of last message - seconds since epoch|
|Last Emoji|`le`|`1740266497`|Number|Timestamp of last message emoji - seconds since epoch|
|Last Edit|`led`|`1739318078`|Number|Timestamp of last message edit - seconds since epoch|
|Version|`v`|`0.44`|Number|Version of client, used for logging only|
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
   "n": "Kevin",
   "c": "M0AHN",
   "lm": 1740299150,
   "le": 1740266497,
   "led": 1739318078,
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
|Callsign|`fc`|`M0AHN`|String|The callsign of the user entering pairing mode

### JSON Example

```json
{
   "t": "p",
   "fc": "M0AHN"
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
|Callsign|`c`|`M0AHN`|String|The callsign of the user you're enquiring about

### JSON Example

```json
{
   "t" : "ue",
   "c": "G5ALF"
}
```

### Server to Client
<hr>

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`ue`|String|Always type `ue` for User Enquiry response
|Registered|`r`|`true` or `false`|Boolean|True if registered
|Callsign|`c`|`G5ALF`|String|Callsign of the user enquired about


### JSON Example

```json
{
   "t": "ue",
   "r": false,
   "c": "G5ALF"
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
      "M0AHN"
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
|Callsign|`c`|`M0AHN`|String|User's callsign, from the user record in the WPS database
|Name|`n`|`Kevin`|String|User's name, from the user record in the WPS database
|Timestamp|`ts`|`1738869165936`|String|The timestamp the user's name was updated, from the user record in the WPS database

### JSON Example

```json
{
   "t": "he", 
   "h": [
      {
         "c": "M0AHN", 
         "n": "Kevin", 
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
|Callsign|`c`|`M0AHN`|String|Callsign of user connecting or disconnecting

### JSON Example

Connect
```json
{
   "t": "uc",
   "c": "M0AHN"
}
```

Disconenct
```json
{
   "t": "ud",
   "c": "M0AHN"
}
```
## Type o - Online Users
<hr>

Sent by the server as part of the connect sequence - contains an array of users currently online

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`o`|String|User Connect or User Disconnect
|Callsign Array|`o`|`"G5ALF","M0AHN"`|Array|Array of users currently online (i.e. connected)

### JSON Example

```json
{
   "t": "o",
   "o": [
      "G5ALF",
      "M0AHN"
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
|Callsign|`tc`|`G5ALF`|String|The recipient callsign|
|Name|`n`|`Alfred`|Number|Reciient name|
|Last Seen|`ls`|`1740252223`|Number|Timestamp last connected - seconds since epoch|

### JSON Example

```json
{
   "t": "u",
   "u": [
      {
         "tc": "G5ALF",
         "n": "Alfred",
         "ls": 1740252223
      }
   ]
}
```

## The Connect Sequence Explained

### Overview
The type `c` handling is the most complex in WPS - it triggers the chain of responses required to update the client with all changes that have occurred since last login. 

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
   - new posts, sent in batches of 4 as type `cpb`
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


