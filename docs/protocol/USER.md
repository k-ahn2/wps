# Data Model - The User Object

Every registered user is one row in the `users` table. This document describes that
row, every key the `user` object can hold, and **every place in the code that adds or
updates a key**.

## Table of Contents
1. [The users table](#the-users-table)
2. [How the user object is written](#how-the-user-object-is-written)
3. [Value typing](#value-typing)
4. [User object field reference](#user-object-field-reference)
5. [The push sub-object](#the-push-sub-object)
6. [JSON example](#json-example)
7. [Every write path](#every-write-path)
8. [Deprecated keys](#deprecated-keys)
9. [Read-only consumers](#read-only-consumers)

[Return to README](/README.md)

## The users table

Created by `dbInit` - see [db.py](/db.py#L24):

```sql
CREATE TABLE IF NOT EXISTS users (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT
);
```

The table has no per-field columns. `user` is a single JSON blob and every attribute
lives inside it. Reads use `json_extract(user, '$.<key>')`; the user is always located
by `json_extract(user, '$.callsign') = ?`.

## How the user object is written

All four writers live in [db.py](/db.py):

| Function | Mechanism | Effect on keys |
| - | - | - |
| [`dbCreateNewUser`](/db.py#L164) | `INSERT INTO users (user) VALUES (?)` | Writes the whole object once. Requires `callsign` |
| [`dbUserUpdate`](/db.py#L120) | `UPDATE ... SET user = json_set(user, '$.k1', ?, '$.k2', ?, ...)` | For each key in `update_object`: **adds it if missing, overwrites it if present**. Other keys are untouched |
| [`dbUpdateUserPushNotifications`](/db.py#L839) | `json_insert(user, '$.channel_notifications_since_last_logout[#]', ?)` | Appends one channel id to that array |
| [`dbCleanupDepracatedLastSeenKey`](/db.py#L490) | `json_remove(user, '$.lastseen')` | Deletes the legacy `lastseen` key |

Every functional write in the codebase goes through one of these. There is no other
`UPDATE users` / `INSERT INTO users` anywhere.

## Value typing

`dbUserUpdate` and `dbCreateNewUser` values are coerced by
[`sourceValueToJsonValue`](/db.py#L67) before binding. This matters for two keys:

| Python value | Stored as |
| - | - |
| `bool` (`True` / `False`) | **string** `"True"` / `"False"` |
| `int` / `float` | number |
| numeric string (`"12"`) | number `12` |
| `list` | JSON array |
| anything else | string |

So `pair_enabled` is stored as the string `"True"`, and a version string like
`"0.94.13"` stays a string while a bare `0` default is stored as a number.

## User object field reference

Which code writes each key is listed in [Every write path](#every-write-path); this
table is just the keys and what they mean.

| Key | Stored type | Description |
| - | - | - |
| `callsign` | string | Identity / lookup key. Never updated after creation |
| `name` | string | Display name. Source: connect object `n` (default `-`). Updated on connect when it changes |
| `name_last_updated` | number (epoch s) | When `name` last changed. Drives Ham Enquiry (`dbGetUpdatedHams`) |
| `last_connected` | number (epoch s) | Most recent connect. Set on every connect |
| `last_disconnected` | number (epoch s) | Most recent socket close |
| `is_online` | number (`0` / `1`) | `1` while at least one socket for the callsign is open. Only set after the receipt of the user's connect string. Reset to `0` on server startup incase of ungraceful WPS close |
| `last_client_version` | string (or number `0`) | Connect object `v`, e.g. `0.94.13`. `0` if the client omits it. Stored only - nothing reads it |
| `channel_subscriptions` | array of channel ids | Channels the user is subscribed to. WPS uses this to determine whether a user should receive a) updates when connected, b) push notifications when not connected (if enabled) |
| `paused_channels` | array of channel ids | Channels whose backlog exceeds `maxNewPostsToReturnPerChannelOnConnect`, held until the client asks for them. Reset to `[]` on disconnect |
| `notifications_since_last_logout` | array of callsigns | Senders who have already triggered a message push this session. Reset to `[]` on connect |
| `channel_notifications_since_last_logout` | array of channel ids | Channels that have already triggered a post push this session. Reset to `[]` on connect |
| `avatar` | string (base64 data URI) | Set via protocol type `a` |
| `avatar_last_updated` | number (epoch s) | When `avatar` last changed. Drives Avatar Enquiry (`dbGetUpdatedAvatars`) |
| `pair_enabled` | string `"True"` | Boolean coerced to a string (see [Value typing](#value-typing)). Set via protocol type `p`. Never cleared in code |
| `pair_start_time` | number (epoch s) | When the user entered Packet Alerts pairing mode |
| `push` | array of objects | See [The push sub-object](#the-push-sub-object). New entries are **not** created by any handler in this repo |
| `lastseen` | number (epoch s) | Deprecated, never written. Removed on next connect. Replaced by `last_connected` / `last_disconnected` |

## The push sub-object

Each element of the `push` array is an object. Only the "bad player id" fields are ever
written by this codebase - the base entry (`playerId` / `isPushEnabled`) is created
outside it (an earlier version or an admin process); WPS only reads those and flags
entries that OneSignal rejects.

| Key | Type | Written by | Notes |
| - | :-: | - | - |
| `playerId` | string | *(external)* | OneSignal player / device id |
| `isPushEnabled` | bool | *(external)* | Whether this device wants push |
| `isBadPlayerId` | bool `true` | [`cleanup_bad_push_player_id`](/handlers.py#L127) | Added once a push to this id fails |
| `isBadPlayerIdTimestamp` | number (epoch s) | [`cleanup_bad_push_player_id`](/handlers.py#L128) | When it was flagged |
| `isBadPlayerIdReason` | string | [`cleanup_bad_push_player_id`](/handlers.py#L130) | The push response / JSON that caused the flag |

Consumers filter on `isPushEnabled and 'isBadPlayerId' not in entry` -
[`dbChannelSubscribers`](/db.py#L768), [`message_send_handler`](/handlers.py#L1158),
[`service_monitor.py`](/service_monitor.py#L94).

## JSON example

```json
{
   "callsign": "T3EST",
   "name": "Tester",
   "name_last_updated": 1740292240,
   "last_connected": 1740299150,
   "last_disconnected": 1740266497,
   "is_online": 1,
   "last_client_version": "0.94.13",
   "channel_subscriptions": [2, 3, 4],
   "paused_channels": [],
   "notifications_since_last_logout": ["M8ABC"],
   "channel_notifications_since_last_logout": [3],
   "avatar": "data:image/jpeg;base64,/9j/4AAQSkZJRg...",
   "avatar_last_updated": 1750799200,
   "pair_enabled": "True",
   "pair_start_time": 1750799200,
   "push": [
      {
         "playerId": "049b035b-c722-4b2b-b89e-f7ae6ee25835",
         "isPushEnabled": true
      },
      {
         "playerId": "1a2b3c4d-0000-0000-0000-000000000000",
         "isPushEnabled": true,
         "isBadPlayerId": true,
         "isBadPlayerIdTimestamp": 1750800000,
         "isBadPlayerIdReason": "{\"errors\": {\"invalid_player_ids\": [\"1a2b...\"]}}"
      }
   ]
}
```

## Every write path

Ordered by trigger. Each entry lists the keys that call adds or updates.

### 1. First connect - new user created
[`connect_handler`](/handlers.py#L312) -> [`dbCreateNewUser`](/db.py#L164)

- `callsign`
- `name` - connect object `n` (default `-`)
- `last_connected` - connect timestamp
- `name_last_updated` - connect timestamp
- `channel_subscriptions` - `env.autoSubscribeToChannelIds` (default `[]`)

### 2. Every connect - status refresh
[`connect_handler`](/handlers.py#L351) -> [`dbUserUpdate`](/db.py#L120)

- `last_connected` - connect timestamp
- `notifications_since_last_logout` - reset to `[]`
- `channel_notifications_since_last_logout` - reset to `[]`
- `is_online` - `1`
- `last_client_version` - connect object `v` (default `0`)
- `name` - **only if** the client's `n` differs from the stored name ([handlers.py:362](/handlers.py#L362))
- `name_last_updated` - only if `name` changed

### 3. Connect with a large channel backlog
[`existing_connect_handler`](/handlers.py#L631) -> [`dbUserUpdate`](/db.py#L120)

- `paused_channels` - list of channel ids whose pending post count exceeds
  `maxNewPostsToReturnPerChannelOnConnect`

### 4. Enable pairing (protocol type `p`)
[`pairing_handler`](/handlers.py#L982) -> [`dbUserUpdate`](/db.py#L120)

- `pair_enabled` - `True` (stored as `"True"`)
- `pair_start_time` - now, epoch seconds

### 5. Add / update avatar (protocol type `a`)
[`avatar_handler`](/handlers.py#L1009) -> [`dbUserUpdate`](/db.py#L120)

- `avatar_last_updated` - now, epoch seconds
- `avatar` - base64 data URI from `a`

### 6. Push player id flagged bad
[`cleanup_bad_push_player_id`](/handlers.py#L138) -> [`dbUserUpdate`](/db.py#L120)

- `push` - the whole array is written back; the matching entry gains
  `isBadPlayerId`, `isBadPlayerIdTimestamp`, `isBadPlayerIdReason`
  ([handlers.py:126-133](/handlers.py#L126))

### 7. Message push sent to an offline user
[`message_send_handler`](/handlers.py#L1174) -> [`dbUserUpdate`](/db.py#L120) on the recipient (`message['tc']`)

- `notifications_since_last_logout` - sender's callsign appended (only when a push was actually sent)

### 8. Channel-post push sent to an offline subscriber
[`post_handler`](/handlers.py#L1422) -> [`dbUpdateUserPushNotifications`](/db.py#L839) per subscriber

- `channel_notifications_since_last_logout` - `post['cid']` appended via `json_insert`

### 9. Channel subscribe (protocol type `cs`, `s == 1`)
[`channel_subscribe_handler`](/handlers.py#L1629) -> [`dbUserUpdate`](/db.py#L120)

- `channel_subscriptions` - requested `cid` added

### 10. Channel unsubscribe (protocol type `cs`, `s == 0`)
[`channel_subscribe_handler`](/handlers.py#L1653) -> [`dbUserUpdate`](/db.py#L120)

- `channel_subscriptions` - requested `cid` removed

### 11. Unpause channel (protocol type `cu`)
[`unpause_channel_handler`](/handlers.py#L1773) -> [`dbUserUpdate`](/db.py#L120)

- `paused_channels` - the unpaused `cid` filtered out of the list

### 12. Disconnect
[`close_connection`](/handlers.py#L1882) -> [`dbUserUpdate`](/db.py#L120)

- `last_disconnected` - now, epoch seconds
- `paused_channels` - reset to `[]`
- `is_online` - `0`, **only** when no other socket for the callsign remains ([handlers.py:1889](/handlers.py#L1889))

### 13. Server startup - clear stale online flags
[`startup_and_listen`](/wps.py#L425) -> [`dbUserUpdate`](/db.py#L120) for every user returned by [`dbGetOnlineUsers`](/db.py#L405)

- `is_online` - `0`

### 14. Startup auto-subscribe sweep
[`check_auto_subscriptions`](/handlers.py#L1996) -> [`dbUserUpdate`](/db.py#L120)

- `channel_subscriptions` - any missing id from `env.autoSubscribeToChannelIds` appended

### 15. Legacy key cleanup
[`connect_handler`](/handlers.py#L337) -> [`dbCleanupDepracatedLastSeenKey`](/db.py#L490)

- `lastseen` - removed (after its value is copied into `last_connected` in memory if
  `last_connected` was absent, [handlers.py:331-337](/handlers.py#L331))

## Deprecated keys

| Key | Replacement | Removal |
| - | - | - |
| `lastseen` | `last_connected` / `last_disconnected` | [`dbCleanupDepracatedLastSeenKey`](/db.py#L490), triggered from `connect_handler` when a record has no `last_connected`. Still read as a fallback in [`dbGetMessagedUsers`](/db.py#L444) for records not yet migrated |

## Read-only consumers

Where each key is read (for field semantics - none of these write):

| Key | Read by |
| - | - |
| `callsign` | every user query - lookup key |
| `is_online` | [`dbGetOnlineUsers`](/db.py#L405), [`socket_send_handler_other_connected_user`](/handlers.py#L1960) |
| `name`, `name_last_updated` | [`dbGetUpdatedHams`](/db.py#L984), [`dbGetMessagedUsers`](/db.py#L434), `ham_enquiry_handler`, `user_enquiry_handler` |
| `avatar`, `avatar_last_updated` | [`dbGetUpdatedAvatars`](/db.py#L1014), `avatar_enquiry_handler` |
| `last_connected`, `last_disconnected` | [`dbGetMessagedUsers`](/db.py#L434), `user_enquiry_handler`, [stats.py](/stats.py#L26) (`uculsd` stat) |
| `channel_subscriptions` | [`dbChannelSubscribers`](/db.py#L745), [`check_auto_subscriptions`](/handlers.py#L1982), `existing_connect_handler` |
| `channel_notifications_since_last_logout` | [`dbChannelSubscribers`](/db.py#L751), `post_handler` |
| `paused_channels` | [`dbPausedCallsignsForChannel`](/db.py#L800), `unpause_channel_handler` |
| `notifications_since_last_logout` | [`message_send_handler`](/handlers.py#L1141) |
| `push` | [`dbChannelSubscribers`](/db.py#L768), [`message_send_handler`](/handlers.py#L1146), [`cleanup_bad_push_player_id`](/handlers.py#L116), [service_monitor.py](/service_monitor.py#L94) |
| `pair_enabled`, `pair_start_time` | returned to the client in `pairing_handler`; no server-side reader |
| `last_client_version` | *(stored only - no reader in this codebase)* |
