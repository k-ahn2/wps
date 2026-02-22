# Protocol - Messages

## Table of Contents

Singular Types

1. [Type m - Message](#type-m---message)
2. [Type med - Message Edit](#type-med---message-edit)
3. [Type mr - Message Delivery Response](#type-mr---message-delivery-response)
4. [Type mem - Message Emoji](#type-mem---message-emoji)

Batch Variants

1. [Type mb - Message Batch](#type-mb---message-batch)
2. [Type medb - Message Edit Batch](#type-medb---message-edit-batch)
3. [Type memb - Message Emoji Batch](#type-memb---message-emoji-batch)

[Return to README](/README.md)

## Type m - Message

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`m`|String|Type `m` for Message
|Message Id|`_id`|`1740312733123-M8ABC`|String|OPTIONAL _id field specific to the message. WPS creates `{ts}-{fc}` if not supplied, which can be mirrored on the client without the need to send this field over the air
|From Call|`fc`|`M8ABC`|String|Sending Callsign
|To Call|`tc`|`T3EST`|String|Receiving Callsign
|Message|`m`|`This is a test`|String|The actual message
|Timestamp|`ts`|`1740312733123`|Number|Timestamp of message - milliseconds since epoch
|**Optional Fields**|
|Reply Id|`r`|`1740312711123-T3EST`|String|The id of the message being replied to
|**Server Only Fields**|
|Message Status|`ms`|`1`|Number|0 = Client Sent<BR>1 = Server Received<BR>2 = Recipient Delivered<BR>3 = Recipient Read.<BR>Currently unused - future use case. Server value always currently 1|
|Logged Timestamp|`lts`|`1740312745123`|Number|The timestamp the server received and processed the message

> [!NOTE]
> WPS will store and forward any optional fields sent by the client. 

### JSON Example

```json
{
   "t": "m",
   "_id": "1740312733123-M8ABC",
   "fc": "M8ABC",
   "tc": "T3EST",
   "m": "This is a test",
   "ms": 1,
   "ts": 1740312733123
}
```

### Server to Client

Returns type `mr`

## Type med - Message Edit

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`med`|String|Type `med` for Message Edit
|Id|`_id`|`1740312733123-M8ABC`|String|_id of the edited message - must be common between Client and Server
|Edited Message|`m`|`This is a test`|String|The edited message in full
|Edited Flag|`ed`|`1`|Number|Currently used to determine if a message has been edited
|Edited Timestamp|`edts`|`1740312733123`|Number|Edited timestamp of message - milliseconds since epoch

### JSON Example

```json
{
    "t": "med",
    "_id": "d25e2702-2023-4906-93f0-5c60a4c18b4d", 
    "m": "Blah 2", 
    "edts": 1750713928123
}
```

### Server to Client

Returns type `mr`

## Type mr - Message Delivery Response

Returned to the client to confirm receipt of a new message or message edit

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`mr`|String|Always type `mr` for Message Response
|Id|`_id`|`1740312733123-M8ABC`|String|_id - common between Client and Server

### JSON Example

```json
{
   "t": "mr", 
   "_id": "1740312733123-M8ABC"
}
```

## Type mem - Message Emoji

WPS doesn't send delivery confirmation responses for emoji additions or removals - they are not deemed essential to the integrity of WPS. 

If the emoji reaches the server, it should always be delivered to the connected client in real-time, or, get picked up at next connect.

There are some edge cases where a client could send an emoji and the packet network fails before delivery to the server. In this edge case, the sender may see the emoji on their client, but it hasn't been delivered.

Ater every emoji add or remove, both for real-time connections and during the connect sequence, WPS will always return the latest full emoji state for a message. For example, if a message has 1 emoji and 2nd is added, WPS will return both 1st and 2nd in the update.

### Client to Server

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`mem`|String|Type `mem` for Message Emoji
|Action|`a`|`1`|Number|`1` for Add, `0` for Remove
|Id|`_id`|`1740312733123-M8ABC`|String|_id of the message to apply the emoji
|Timestamp|`ets`|`1750713928123`|Number|Timestamp the emoji is created on the client
|Emoji|`e`|`1f44d`|String|The unicode value of the emoji to add or remove

### JSON Example

Emoji Add
```json
{
   "t": "mem",
   "a": 1,
   "_id": "1740312733123-M8ABC",
   "e": "1f44d",
   "ets": 1750713928
}
```

Emoji Remove
```json
{
   "t": "mem",
   "a": 0,
   "_id": "1740312733123-M8ABC",
   "e": "1f44d",
   "ets": 1750713928
}
```

### Server to Client (Connected Clents Only)

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`mem`|String|Type `mem` for Message Emoji
|Id|`_id`|`1740312733123-M8ABC`|String|_id of the message to apply the emoji
|Timestamp|`ets`|`1750713928`|Number|Timestamp the emoji is applied at the server, replicated to the client
|Array of Emojis|`e`|`[ "1f44d", "1f603" ]`|Array|All emojis for the message|

### JSON Example

```json
{ 
   "t": "mem", 
   "_id": "1740312733123-M8ABC", 
   "e": [
      "1f44d",
      "1f603"
   ],
   "ets": 1750713928
}
```

## Type mb - Message Batch

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`mb`|String|Type `mb` for Message Batch
|Meta Data|`md`|`{}`|Object| `mt` = Message Total, in the overall batch <BR>`mc` = Message Count, the cumulative total after this batch is processed<br>```{ "mt": 27, "mc": 2 }```
|Messages|`m`|`[]`|Array|Array of `m` objects to return to the client. Would include any applicable message fields if added - e.g. emojis, edit and reply

### JSON Example

```json
{
   "t": "mb", 
   "md": {
      "mt": 27, 
      "mc": 2
   }, 
   "m": [
      {
         "_id": "1750380586123-M8ABC", 
         "fc": "M8ABC", 
         "tc": "T3EST", 
         "m": "Test 1", 
         "ts": 1750380586123, 
         "e": ["1f622"], 
         "ets": 1751390750
      }, 
      {
         "_id": "1740312735123-M8ABC", 
         "fc": "M8ABC", 
         "tc": "T3EST", 
         "m": "Test 2", 
         "ts": 1740312735123, 
         "e": ["1f603"], 
         "ets": 1751389832123
      }
   ]
}
```

## Type medb - Message Edit Batch

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`medb`|String|Type `medb` for Message Batch
|Messages|`med`|`[]`|Array|Array of `med` objects to return to the client

### JSON Example

```json
{
   "t": "medb", 
   "m": [
      {
         "_id": "1751389832123-M8ABC",
         "edts": 1751389832123,
         "m": "Edited Message Text"
      },
      {
         "_id": "1751389832345-M8ABC",
         "edts": 1751389832345,
         "m": "Another Edited Message Text"
      }      
   ]
}
```

## Type memb - Message Emoji Batch

### Server to Client

| Friendly Name | Key | Sample Values | Data Type | Notes |
| - | :-: | :-: | :-: | - |
|Type|`t`|`memb`|String|Type `memb` for Message Batch
|Messages|`mem`|`[]`|Array|Array of `mem` objects to return to the client

### JSON Example

```json
{
   "t": "memb", 
   "m": [
      {
         "_id": "1751389832123-M8ABC",
         "e": ["1f603"],
         "ets": 1751389832123
      }
   ]
}
```
