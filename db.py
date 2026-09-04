import sqlite3, json, time
import datetime
from logger import *

# Environment Variables
env_source = open("env.json")
env = json.load(env_source)
env_source.close()

DB_FILENAME = env['dbFilename']

def get_db_connection():
    '''
    Opens a new SQLite connection. Each thread must call this to get its own
    connection (and cursor) rather than sharing one across threads - a single
    shared connection is not safe for concurrent writers even with
    check_same_thread=False. WAL mode allows concurrent readers alongside a
    writer.
    '''
    conn = sqlite3.connect(DB_FILENAME)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def dbInit(CONN_DB_CURSOR):
    create_users_table = '''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT
    );
    '''
    CONN_DB_CURSOR.execute(create_users_table)

    create_messages_table = '''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT
    );
    '''
    CONN_DB_CURSOR.execute(create_messages_table)

    create_messages_index = '''
    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_message_id ON messages (json_extract(message, '$._id'));
    '''
    CONN_DB_CURSOR.execute(create_messages_index)

    create_posts_table = '''
    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post TEXT
    );
    '''
    CONN_DB_CURSOR.execute(create_posts_table)

    # Single row (id = 1) holding the channel list last loaded from channels.json and
    # the timestamp it was last changed, so clients can tell whether their copy is stale
    create_channels_table = '''
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY,
        channels TEXT,
        ts INTEGER
    );
    '''
    CONN_DB_CURSOR.execute(create_channels_table)

    CONN_DB_CURSOR.connection.commit()

def sourceValueToJsonValue(value):
    '''
    Determines the SQL placeholder fragment and the parameter to bind for a
    value taken from client-supplied JSON. Preserves the prior type handling
    (numeric passthrough / list-as-json / quoted string) but via parameter
    binding instead of string interpolation, so a value can never break out
    of the query text.
    Returns a tuple of (placeholder_sql, bind_value).
    '''
    if isinstance(value, bool):
        return "?", str(value)
    if isinstance(value, (int, float)):
        return "?", value
    if isinstance(value, str) and value.isnumeric():
        return "?", int(value)
    if isinstance(value, list):
        return "json(?)", json.dumps(value)
    return "?", str(value)

def dbUserSearch(CONN_DB_CURSOR, callsign):
    try:
        select_query = """
        SELECT user
        FROM users
        WHERE json_extract(user, '$.callsign') = ?
        """
        params = [callsign]
        db_logger("dbUserSearch", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]

        if len(result) > 1:
            raise Exception(f"Multiple users found when searching for {callsign}")

        return_success = {
            "result": "success",
            "data": json.loads(result[0]) if len(result) == 1 else None,
        }

        db_logger("dbUserSearch", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbUserSearch",
            "params": [ callsign ]
        }
        db_logger("dbUserSearch", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbUserUpdate(CONN_DB_CURSOR, callsign, update_object):
    set_fragments = []
    params = []
    for key in update_object.keys():
        placeholder, value = sourceValueToJsonValue(update_object[key])
        set_fragments.append(f"?, {placeholder}")
        params.append(f"$.{key}")
        params.append(value)
    fieldsToUpdate = "user = json_set(user, " + ", ".join(set_fragments) + ")"

    try:
        update_query = f"""
        UPDATE users
        SET {fieldsToUpdate}
        WHERE json_extract(user, '$.callsign') = ?
        """
        params.append(callsign)
        db_logger("dbUserUpdate", "Query: " + ' '.join(update_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(update_query, params)
        CONN_DB_CURSOR.connection.commit()

        user_search = dbUserSearch(CONN_DB_CURSOR, callsign)
        if user_search['result'] == 'failure' or user_search['data'] == None:
            raise Exception(f"Failed to retrieve user {callsign} after update.")

        return_success = user_search['data']
        db_logger("dbUserUpdate", "Return: " + str(return_success))

        return {
            "result": "success",
            "data": return_success,
        }

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbUserUpdate",
            "params": [ callsign, update_object ]
        }
        db_logger("dbUserUpdate", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbCreateNewUser(CONN_DB_CURSOR, user_object):
    try:
        # Check if the user object contains a callsign
        if 'callsign' not in user_object:
            raise Exception("New user object does not contain callsign")

        # Confirm user doesn't already exist
        user_search = dbUserSearch(CONN_DB_CURSOR, user_object['callsign'])
        if (user_search['result'] == 'success' and user_search['data'] != None) or user_search['result'] == 'failure':
            raise Exception(f"User {user_object['callsign']} already exists in the database or other error")

        insert_query = "INSERT INTO users (user) VALUES (?)"
        params = [json.dumps(user_object)]
        db_logger("dbCreateNewUser", "Query: " + ' '.join(insert_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(insert_query, params)
        CONN_DB_CURSOR.connection.commit()

        return_success = {
            "result": "success",
            "data": None,
        }
        db_logger("dbCreateNewUser", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbCreateNewUser",
            "params": user_object
        }
        db_logger("dbCreateNewUser", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetMessages(CONN_DB_CURSOR, callsign, last_message):
    try:
        select_query = """
        SELECT message
        FROM messages
        WHERE
            (json_extract(message, '$.fc') = ? OR json_extract(message, '$.tc') = ?) AND
            json_extract(message, '$.ts') > ?
        ORDER BY json_extract(message, '$.ts') ASC
        """
        params = [callsign, callsign, last_message]
        db_logger("dbGetMessages", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [json.loads(i[0]) for i in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": result,
        }
        db_logger("dbGetMessages", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetMessages",
            "params": [callsign, last_message]
        }
        db_logger("dbGetMessages", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetMessageEdits(CONN_DB_CURSOR, callsign, last_message, last_message_edit):
    # New messages retutned by getMessages already include edits and emojis, so we only need to return edits that were made before
    try:
        select_query = """
        SELECT message
        FROM messages
        WHERE
            (json_extract(message, '$.fc') = ? OR json_extract(message, '$.tc') = ?) AND
            json_extract(message, '$.edts') > ? AND
            json_extract(message, '$.ts') <= ?
        ORDER BY json_extract(message, '$.ts') ASC
        """
        params = [callsign, callsign, last_message_edit, last_message]
        db_logger("dbGetMessageEdits", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": [json.loads(i) for i in result],
        }
        db_logger("dbGetMessageEdits", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetMessageEdits",
            "params": [callsign, last_message, last_message_edit]
        }
        db_logger("dbGetMessageEdits", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetMessageEmojis(CONN_DB_CURSOR, callsign, last_message, last_message_emoji):
    # New messages retutned by getMessages already include edits and emojis, so we only need to return edits that were made before
    try:
        select_query = """
        SELECT message
        FROM messages
        WHERE
            (json_extract(message, '$.fc') = ? OR json_extract(message, '$.tc') = ?) AND
            json_extract(message, '$.ets') > ? AND
            json_extract(message, '$.ts') <= ?
        ORDER BY json_extract(message, '$.ts') ASC
        """
        params = [callsign, callsign, last_message_emoji, last_message]
        db_logger("dbGetMessageEmojis", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": [json.loads(i) for i in result],
        }
        db_logger("dbGetMessageEmojis", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetMessageEmojis",
            "params": [callsign, last_message, last_message_emoji]
        }
        db_logger("dbGetMessageEmojis", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetPosts(CONN_DB_CURSOR, channel_id, last_post):
    try:
        select_query = """
        SELECT post
        FROM posts
        WHERE
            json_extract(post, '$.ts') > ? AND
            json_extract(post, '$.cid') = ?
        ORDER BY json_extract(post, '$.ts') ASC
        """
        params = [last_post, channel_id]
        db_logger("dbGetPosts", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [json.loads(i[0]) for i in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": result,
        }
        db_logger("dbGetPosts", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetPosts",
            "params": [channel_id, last_post]
        }
        db_logger("dbGetPosts", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetPostEdits(CONN_DB_CURSOR, channel_id, last_post_edit, last_post):
    try:
        select_query = """
        SELECT post
        FROM posts
        WHERE
            json_extract(post, '$.cid') = ? AND
            json_extract(post, '$.edts') > ? AND
            json_extract(post, '$.ts') <= ?
        ORDER BY
            json_extract(post, '$.ts') ASC
        """
        params = [channel_id, last_post_edit, last_post]
        db_logger("dbGetPostEdits", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": [json.loads(i) for i in result],
        }
        db_logger("dbGetPostEdits", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetPostEdits",
            "params": [channel_id, last_post, last_post_edit]
        }
        db_logger("dbGetPostEdits", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetPostEmojis(CONN_DB_CURSOR, channel_id, last_post_emoji, last_post):
    try:
        select_query = """
        SELECT post
        FROM posts
        WHERE
            json_extract(post, '$.cid') = ? AND
            json_extract(post, '$.ets') > ? AND
            json_extract(post, '$.ts') <= ?
        ORDER BY
            json_extract(post, '$.ts') ASC
        """
        params = [channel_id, last_post_emoji, last_post]
        db_logger("dbGetPostEmojis", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": [json.loads(i) for i in result],
        }
        db_logger("dbGetPostEmojis", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetPostEmojis",
            "params": [channel_id, last_post, last_post_emoji]
        }
        db_logger("dbGetPostEmojis", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetOnlineUsers(CONN_DB_CURSOR):
    try:
        select_query = """
        SELECT user
        FROM users
        WHERE json_extract(user, '$.is_online') = 1
        """
        db_logger("dbGetOnlineUsers", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
        result = [i[0] for i in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": [json.loads(i) for i in result],
        }
        db_logger("dbGetOnlineUsers", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetOnlineUsers",
            "params": []
        }
        db_logger("dbGetOnlineUsers", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetMessagedUsers(CONN_DB_CURSOR, callsign):

    try:
        select_query = """
        SELECT
            c.callsign,
            json_extract(u.user, '$.name') as name,
            json_extract(u.user, '$.last_connected') as last_connected,
            json_extract(u.user, '$.last_disconnected') as last_disconnected,
            json_extract(u.user, '$.name_last_updated') as name_last_updated,
            json_extract(u.user, '$.lastseen') as lastseen
        FROM
            (SELECT DISTINCT(json_extract(message, '$.fc')) as callsign
            FROM messages
            WHERE (json_extract(message, '$.fc') = ? OR json_extract(message, '$.tc') = ?)
            UNION
            SELECT DISTINCT(json_extract(message, '$.tc')) as callsign
            FROM messages
            WHERE (json_extract(message, '$.fc') = ? OR json_extract(message, '$.tc') = ?)) c
            INNER JOIN
            users u ON c.callsign = json_extract(user, '$.callsign')
        WHERE
            callsign != ?
        """
        params = [callsign, callsign, callsign, callsign, callsign]
        db_logger("dbGetMessagedUsers", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = []
        for row in CONN_DB_CURSOR:
            result.append({
                "callsign": row[0],
                "name": row[1],
                "last_connected": row[2] if row[2] is not None else row[5],
                "last_disconnected": row[3] if row[3] is not None else row[5],
                "name_last_updated": row[4] if row[4] is not None else 0,
            })

        return_success = {
            "result": "success",
            "data": result,
        }

        db_logger("dbGetMessagedUsers", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetUserUpdates",
            "params": [callsign]
        }
        db_logger("dbGetMessagedUsers", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbCleanupDepracatedLastSeenKey(CONN_DB_CURSOR, callsign):
    try:
        delete_query = """
        UPDATE users
        SET user = json_remove(user, '$.lastseen')
        WHERE
        json_extract(user, '$.callsign') = ?
        """
        params = [callsign]
        db_logger("dbCleanupDepracatedLastSeenKey", "Query: " + ' '.join(delete_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(delete_query, params)
        CONN_DB_CURSOR.connection.commit()

        return_success = {
            "result": "success",
            "data": None,
        }
        db_logger("dbCleanupDepracatedLastSeenKey", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbCleanupLastSeen",
            "params": []
        }
        db_logger("dbCleanupDepracatedLastSeenKey", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbInsertMessage(CONN_DB_CURSOR, message):
    try:
        insert_query = "INSERT INTO messages (message) VALUES (?)"
        params = [json.dumps(message, separators=(',', ':'))]
        db_logger("dbInsertMessage", "Query: " + ' '.join(insert_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(insert_query, params)
        CONN_DB_CURSOR.connection.commit()

        return_success = {
            "result": "success",
            "data": None,
        }
        db_logger("dbInsertMessage", "Return: " + str(return_success))
        return return_success

    except sqlite3.IntegrityError:
        # Duplicate _id → ignore gracefully
        # # Could use INSERT OR IGNORE to avoid this, but helpful to know if WPS gets the same message twice.
        db_logger("dbInsertMessage", "Duplicate _id encountered, ignored gracefully but shouldn't have happened", 'ERROR')
        return_success = {
            "result": "success",
            "data": None,
        }
        db_logger("dbInsertMessage", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbInsertMessage",
            "params": message
        }
        db_logger("dbInsertMessage", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbMessageSearch(CONN_DB_CURSOR, message_id):
    try:
        select_query = """
        SELECT message
        FROM messages
        WHERE json_extract(message, '$._id') = ?
        """
        params = [message_id]
        db_logger("dbMessageSearch", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]

        if len(result) > 1:
            raise Exception(f"Multiple messages found when searching for {message_id}")

        return_success = {
            "result": "success",
            "data": json.loads(result[0]) if len(result) == 1 else None,
        }
        db_logger("dbMessageSearch", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbMessageSearch",
            "params": [message_id]
        }
        db_logger("dbMessageSearch", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbUpdateMessage(CONN_DB_CURSOR, message_id, update):

    set_fragments = []
    params = []
    for key in update.keys():
        placeholder, value = sourceValueToJsonValue(update[key])
        set_fragments.append(f"?, {placeholder}")
        params.append(f"$.{key}")
        params.append(value)
    fieldsToUpdate = "message = json_set(message, " + ", ".join(set_fragments) + ")"

    try:
        update_query = f"""
        UPDATE messages
        SET {fieldsToUpdate}
        WHERE json_extract(message, '$._id') = ?
        """
        params.append(message_id)
        db_logger("dbUpdateMessage", "Query: " + ' '.join(update_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(update_query, params)
        CONN_DB_CURSOR.connection.commit()

        message_search = dbMessageSearch(CONN_DB_CURSOR, message_id)
        if message_search['result'] == 'failure' or message_search['data'] == None:
            raise Exception(f"Failed to retrieve user {message_id} after update.")

        return_success = {
            "result": "success",
            "data": message_search['data'],
        }
        db_logger("dbUpdateMessage", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbUpdateMessage",
            "params": [ message_id, update ]
        }
        db_logger("dbUpdateMessage", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbInsertPost(CONN_DB_CURSOR, post):

    try:
        insert_query = "INSERT INTO posts (post) VALUES (?)"
        params = [json.dumps(post, separators=(',', ':'))]
        db_logger("dbInsertPost", "Query: " + ' '.join(insert_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(insert_query, params)
        CONN_DB_CURSOR.connection.commit()

        return_success = {
            "result": "success",
            "data": None,
        }
        db_logger("dbInsertPost", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbInsertPost",
            "params": post
        }
        db_logger("dbInsertPost", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbPostSearch(CONN_DB_CURSOR, channel_id, post_timestamp):
    try:
        select_query = """
        SELECT post
        FROM posts
        WHERE
            json_extract(post, '$.ts') = ? AND
            json_extract(post, '$.cid') = ?
        """
        params = [post_timestamp, channel_id]
        db_logger("dbPostSearch", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]

        if len(result) > 1:
            raise Exception(f"Multiple posts found when searching for {post_timestamp} in channel {channel_id}")

        return_success = {
            "result": "success",
            "data": json.loads(result[0]) if len(result) == 1 else None,
        }
        db_logger("dbPostSearch", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbPostSearch",
            "params": [channel_id, post_timestamp]
        }
        db_logger("dbPostSearch", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbUpdatePost(CONN_DB_CURSOR, channel_id, post_timestamp, update):

    set_fragments = []
    params = []
    for key in update.keys():
        placeholder, value = sourceValueToJsonValue(update[key])
        set_fragments.append(f"?, {placeholder}")
        params.append(f"$.{key}")
        params.append(value)
    fieldsToUpdate = "post = json_set(post, " + ", ".join(set_fragments) + ")"

    try:
        ts_placeholder, ts_param = sourceValueToJsonValue(post_timestamp)
        cid_placeholder, cid_param = sourceValueToJsonValue(channel_id)
        update_query = f"""
        UPDATE posts
        SET {fieldsToUpdate}
        WHERE
            json_extract(post, '$.ts') = {ts_placeholder} AND
            json_extract(post, '$.cid') = {cid_placeholder}
        """
        params.extend([ts_param, cid_param])
        db_logger("dbUpdatePost", "Query: " + ' '.join(update_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(update_query, params)
        CONN_DB_CURSOR.connection.commit()

        post_search = dbPostSearch(CONN_DB_CURSOR, channel_id, post_timestamp)
        if post_search['result'] == 'failure' or post_search['data'] == None:
            raise Exception(f"Failed to retrieve post {post_timestamp} after update.")

        return_success = {
            "result": "success",
            "data": post_search['data'],
        }
        db_logger("dbUpdatePost", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbUpdatePost",
            "params": [ channel_id, post_timestamp, update ]
        }
        db_logger("dbUpdatePost", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbChannelSubscribers(CONN_DB_CURSOR, sending_callsign, channel_id):
    try:
        select_query = """
        SELECT
            json_extract(user, '$.callsign'),
            IFNULL(json_extract(user, '$.channel_subscriptions'), '[]'),
            IFNULL(json_extract(user, '$.channel_notifications_since_last_logout'), '[]'),
            IFNULL(json_extract(user, '$.push'), '[]')
        FROM
            users
        WHERE
            json_extract(user, '$.callsign') != ?
        """
        params = [sending_callsign]
        db_logger("dbChannelSubscribers", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = []
        for row in CONN_DB_CURSOR:
            callsign = row[0]
            channel_subscriptions = json.loads(row[1]) if row[1] else []
            channel_notifications_since_last_logout = json.loads(row[2]) if row[2] else []
            push_devices = json.loads(row[3]) if row[3] else []
            enabled_player_ids = [
                x['playerId']
                for x in push_devices
                if x.get('isPushEnabled') and not x.get('isBadPlayerId')
            ]

            if channel_id not in channel_subscriptions:
                continue

            result.append({
                "callsign": callsign,
                "channel_notifications_since_last_logout": channel_notifications_since_last_logout,
                "enabled_player_ids": enabled_player_ids
            })

        return_success = {
            "result": "success",
            "data": result
        }
        db_logger("dbChannelSubscribers", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbChannelSubscribers",
            "params": [sending_callsign, channel_id]
        }
        db_logger("dbChannelSubscribers", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbPausedCallsignsForChannel(CONN_DB_CURSOR, channel_id):
    try:
        select_query = """
        SELECT
            json_extract(user, '$.callsign'),
            IFNULL(json_extract(user, '$.paused_channels'), '[]')
        FROM
            users
        """
        db_logger("dbPausedCallsignsForChannel", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
        result = []
        for row in CONN_DB_CURSOR:
            callsign = row[0]
            paused_channels = json.loads(row[1]) if row[1] else []

            if channel_id not in paused_channels:
                continue

            result.append(callsign)

        return_success = {
            "result": "success",
            "data": result
        }
        db_logger("dbPausedCallsignsForChannel", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbPausedCallsignsForChannel",
            "params": [channel_id]
        }
        db_logger("dbPausedCallsignsForChannel", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbUpdateUserPushNotifications(CONN_DB_CURSOR, callsign, channel_id):
    # Update the user with the new push devices
    try:
        update_query = """
        UPDATE users
        SET user = json_insert(user, '$.channel_notifications_since_last_logout[#]', ?)
        WHERE json_extract(user, '$.callsign') = ?
        """
        params = [channel_id, callsign]
        db_logger("dbUpdateUserPushNotifications", "Query: " + ' '.join(update_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(update_query, params)
        CONN_DB_CURSOR.connection.commit()

        return_success = {
            "result": "success",
            "data": None,
        }
        db_logger("dbUpdateUserPushNotifications", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbUpdateUserPushNotifications",
            "params": [callsign, channel_id]
        }
        db_logger("dbUpdateUserPushNotifications", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetPostsBatch(CONN_DB_CURSOR, channel_id, bach_size):
    try:
        select_query = """
        SELECT
            *
        FROM
            (SELECT * FROM posts
            WHERE json_extract(post, '$.cid') = ?
            ORDER BY json_extract(post, '$.ts') DESC LIMIT ?)
        ORDER BY json_extract(post, '$.ts') ASC;
        """
        params = [channel_id, bach_size]
        db_logger("dbGetPostsBatch", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        result = []
        CONN_DB_CURSOR.execute(select_query, params)
        for row in CONN_DB_CURSOR:
            result.append(json.loads(row[1]))

        # Remove the Logged Timestamp field, not used by the client
        # Remove the type field, implicit in the cpb type
        # Remove the cid, it's in the header
        for post in result:
            if 'dts' in post:
                del post['dts']
            del post['t']
            del post['cid']

        return_success = {
            "result": "success",
            "data": result,
        }
        db_logger("dbGetPostsBatch", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetPostsBatch",
            "params": [channel_id, bach_size]
        }
        db_logger("dbGetPostsBatch", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetLastMessages(CONN_DB_CURSOR, callsign, recipient_callsign, message_limit):

    try:
        select_query = """
        SELECT * FROM
            (SELECT message
            FROM messages
            WHERE
                (json_extract(message, '$.fc') = ? AND json_extract(message, '$.tc') = ?) OR
                (json_extract(message, '$.fc') = ? AND json_extract(message, '$.tc') = ?)
            ORDER BY json_extract(message, '$.ts') DESC
            LIMIT ?)
        ORDER BY json_extract(message, '$.ts') ASC
        """
        params = [callsign, recipient_callsign, recipient_callsign, callsign, message_limit]
        db_logger("dbGetLastMessages", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": [json.loads(i) for i in result],
        }
        db_logger("dbGetLastMessages", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetLastMessages",
            "params": [callsign, recipient_callsign, message_limit]
        }
        db_logger("dbGetLastMessages", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbMessageCountToRecipient(CONN_DB_CURSOR, callsign, recipient_callsign):

    try:
        select_query = """
        SELECT COUNT(*)
            FROM messages
            WHERE
                (json_extract(message, '$.fc') = ? AND json_extract(message, '$.tc') = ?) OR
                (json_extract(message, '$.fc') = ? AND json_extract(message, '$.tc') = ?)
        """
        params = [callsign, recipient_callsign, recipient_callsign, callsign]
        db_logger("dbMessageCountToRecipient", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)
        result = [i[0] for i in CONN_DB_CURSOR]
        return_success = {
            "result": "success",
            "data": result[0] if len(result) == 1 else 0,
        }
        db_logger("dbMessageCountToRecipient", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbMessageCountToRecipient",
            "params": [callsign, recipient_callsign]
        }
        db_logger("dbMessageCountToRecipient", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetUpdatedHams(CONN_DB_CURSOR, last_ham_update_timestamp):
    try:
        select_query = """
        SELECT user
        FROM users
        WHERE json_extract(user, '$.name_last_updated') > ?
        """
        params = [last_ham_update_timestamp]
        db_logger("dbGetUpdatedHams", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)

        return_success = {
            "result": "success",
            "data": [json.loads(i[0]) for i in CONN_DB_CURSOR]
        }

        db_logger("dbGetUpdatedHams", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetUpdatedHams",
            "params": [ last_ham_update_timestamp ]
        }
        db_logger("dbGetUpdatedHams", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetUpdatedAvatars(CONN_DB_CURSOR, callsign, last_avatar_timestamp):
    try:
        select_query = """
        SELECT
            json_extract(user, '$.callsign') as callsign,
            json_extract(user, '$.avatar') as avatar,
            json_extract(user, '$.avatar_last_updated') as avatar_last_updated
        FROM users
            WHERE json_extract(user, '$.avatar_last_updated') > ?
            AND json_extract(user, '$.callsign') != ?
        ORDER BY json_extract(user, '$.avatar_last_updated') ASC
        """

        params = [last_avatar_timestamp, callsign]
        db_logger("dbGetUpdatedAvatars", "Query: " + ' '.join(select_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(select_query, params)

        return_success = {
            "result": "success",
            "data": []
        }

        for row in CONN_DB_CURSOR:
            return_success['data'].append({
                "callsign": row[0],
                "avatar": row[1],
                "avatar_last_updated": row[2]
            })

        db_logger("dbGetUpdatedAvatars", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetUpdatedAvatars",
            "params": [ callsign, last_avatar_timestamp ]
        }
        db_logger("dbGetUpdatedAvatars", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbGetChannels(CONN_DB_CURSOR):
    try:
        select_query = """
        SELECT channels, ts
        FROM channels
        WHERE id = 1
        """
        db_logger("dbGetChannels", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
        result = [row for row in CONN_DB_CURSOR]

        return_success = {
            "result": "success",
            "data": { "channels": json.loads(result[0][0]), "ts": result[0][1] } if len(result) == 1 else None,
        }
        db_logger("dbGetChannels", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetChannels",
            "params": []
        }
        db_logger("dbGetChannels", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbSyncChannels(CONN_DB_CURSOR, current_channels):
    '''
    Compares current_channels (freshly read from channels.json) against the copy stored in the
    database. If it differs, or nothing has been stored yet, upserts the single channels row
    with a fresh timestamp. Returns the channels and timestamp now current in the database.
    '''
    try:
        existing = dbGetChannels(CONN_DB_CURSOR)
        if existing['result'] == 'failure':
            raise Exception(existing['error'])

        existing_data = existing['data']

        if existing_data is not None and existing_data['channels'] == current_channels:
            return {
                "result": "success",
                "data": existing_data,
            }

        sync_ts = round(time.time() * 1000)
        upsert_query = """
        INSERT INTO channels (id, channels, ts) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET channels = excluded.channels, ts = excluded.ts
        """
        params = [json.dumps(current_channels), sync_ts]
        db_logger("dbSyncChannels", "Query: " + ' '.join(upsert_query.split()) + " | Params: " + str(params))

        CONN_DB_CURSOR.execute(upsert_query, params)
        CONN_DB_CURSOR.connection.commit()

        return_success = {
            "result": "success",
            "data": { "channels": current_channels, "ts": sync_ts },
        }
        db_logger("dbSyncChannels", "Return: " + str(return_success))
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbSyncChannels",
            "params": [current_channels]
        }
        db_logger("dbSyncChannels", "Return: " + str(return_error), 'ERROR')
        return return_error
