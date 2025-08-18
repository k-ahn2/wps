import sqlite3, json
import datetime

# Environment Variables
env_source = open("env.json")
env = json.load(env_source)
env_source.close()

# Set the log level for the database logger
# Permitted values: 'INFO', 'ERROR'
MIN_LOG_LEVEL = env['minDbLogLevel']
DB_LOGFILE = open("db.log", "a")
DB_FILENAME = env['dbFilename']

def db_logger(function_name, log, log_entry_level = 'INFO'):
    if MIN_LOG_LEVEL == 'ERROR' and log_entry_level == 'INFO':
        return
    DB_LOGFILE.write(datetime.datetime.now().isoformat() + ' ' + log_entry_level + ' ' + function_name + ': ' + str(log) + '\n') 
    DB_LOGFILE.flush()

# Initialize the SQLite database connection
# Set threadsafety to 3 to allow multiple threads to share the same connection
# https://docs.python.org/3/library/sqlite3.html#sqlite3.threadsafety
sqlite3.threadsafety = 3 
db = sqlite3.connect(DB_FILENAME, check_same_thread=False)

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

    create_posts_table = '''
    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post TEXT
    );
    '''
    CONN_DB_CURSOR.execute(create_posts_table)

    db.commit()

def sourceValueToJsonValue(value):
    # Takes a string value from the input json and determines how it needs to be formatted for the query

    if str(value).isnumeric() or isinstance(value, float):
        # If the value is a number only, or a number with decimal places, return it as is
        return value
    if type(value) == list:
        # If the value is a list, convert it to a JSON string
        return f"json('{json.dumps(value)}')"
    else:
        # Else retrurn the value as a string with quotes
        return f"'{value}'"
    
def dbUserSearch(CONN_DB_CURSOR, callsign):
    try:
        select_query = f"""
        SELECT user
        FROM users
        WHERE json_extract(user, '$.callsign') = {sourceValueToJsonValue(callsign)}
        """
        db_logger("dbUserSearch", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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
    fieldsToUpdate = "user = json_set(user, "
    for index, key in enumerate(update_object.keys()):
        fieldsToUpdate += ", " if index != 0 else ''
        fieldsToUpdate += "'$." + key + "', " + str(sourceValueToJsonValue(update_object[key]))
    fieldsToUpdate += ")"

    try:
        update_query = f"""
        UPDATE users
        SET {fieldsToUpdate}
        WHERE json_extract(user, '$.callsign') = '{callsign}' 
        """
        db_logger("dbUserUpdate", "Query: " + ' '.join(update_query.split()))

        CONN_DB_CURSOR.execute(update_query)
        db.commit()
        
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

        insert_query = f"""
        INSERT INTO users (user) 
        VALUES ('{json.dumps(user_object)}')
        """
        db_logger("dbCreateNewUser", "Query: " + ' '.join(insert_query.split()))

        CONN_DB_CURSOR.execute(insert_query)
        db.commit()

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
        select_query = f"""
        SELECT message
        FROM messages
        WHERE 
            (json_extract(message, '$.fc') = {sourceValueToJsonValue(callsign)} OR json_extract(message, '$.tc') = {sourceValueToJsonValue(callsign)}) AND 
            json_extract(message, '$.ts') > {last_message}
        ORDER BY json_extract(message, '$.ts') ASC
        """
        db_logger("dbGetMessages", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
        result = [json.loads(i[0]) for i in CONN_DB_CURSOR]

        for message in result:
            message['m'] = message['m'].replace("''", "'")

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
        select_query = f"""
        SELECT message
        FROM messages
        WHERE 
            (json_extract(message, '$.fc') = {sourceValueToJsonValue(callsign)} OR json_extract(message, '$.tc') = {sourceValueToJsonValue(callsign)}) AND 
            json_extract(message, '$.edts') > {last_message_edit} AND 
            json_extract(message, '$.ts') <= {last_message}
        ORDER BY json_extract(message, '$.ts') ASC
        """
        db_logger("dbGetMessageEdits", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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
        select_query = f"""
        SELECT message
        FROM messages
        WHERE 
            (json_extract(message, '$.fc') = {sourceValueToJsonValue(callsign)} OR json_extract(message, '$.tc') = {sourceValueToJsonValue(callsign)}) AND 
            json_extract(message, '$.ets') > {last_message_emoji} AND
            json_extract(message, '$.ts') <= {last_message}
        ORDER BY json_extract(message, '$.ts') ASC
        """
        db_logger("dbGetMessageEmojis", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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
        select_query = f"""
        SELECT post
        FROM posts
        WHERE 
            json_extract(post, '$.ts') > {last_post} AND 
            json_extract(post, '$.cid') = {channel_id}
        ORDER BY json_extract(post, '$.ts') ASC
        """
        db_logger("dbGetPosts", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
        result = [json.loads(i[0]) for i in CONN_DB_CURSOR]

        for post in result:
            post['p'] = post['p'].replace("''", "'")

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
        select_query = f"""
        SELECT post
        FROM posts
        WHERE 
            json_extract(post, '$.cid') = {channel_id} AND
            json_extract(post, '$.edts') > {last_post_edit} AND
            json_extract(post, '$.ts') <= {last_post}
        ORDER BY 
            json_extract(post, '$.ts') ASC
        """
        db_logger("dbGetPostEdits", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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
        select_query = f"""
        SELECT post
        FROM posts
        WHERE 
            json_extract(post, '$.cid') = {channel_id} AND
            json_extract(post, '$.ets') > {last_post_emoji} AND
            json_extract(post, '$.ts') <= {last_post}
        ORDER BY 
            json_extract(post, '$.ts') ASC
        """
        db_logger("dbGetPostEmojis", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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
        select_query = f"""
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
            WHERE (json_extract(message, '$.fc') = '{callsign}' OR json_extract(message, '$.tc') = '{callsign}') 
            UNION
            SELECT DISTINCT(json_extract(message, '$.tc')) as callsign
            FROM messages 
            WHERE (json_extract(message, '$.fc') = '{callsign}' OR json_extract(message, '$.tc') = '{callsign}')) c
            INNER JOIN
            users u ON c.callsign = json_extract(user, '$.callsign')
        WHERE
            callsign != '{callsign}'
        """
        db_logger("dbGetMessagedUsers", "Query: " + ' '.join(select_query.split()))
        
        CONN_DB_CURSOR.execute(select_query)
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
        delete_query = f"""
        UPDATE users
        SET user = json_remove(user, '$.lastseen')
        WHERE
        json_extract(user, '$.callsign') = '{callsign}'
        """
        db_logger("dbCleanupDepracatedLastSeenKey", "Query: " + ' '.join(delete_query.split()))

        CONN_DB_CURSOR.execute(delete_query)
        db.commit()

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
        message['m'] = message['m'].replace("'", "''")
        
        insert_query = f"""
        INSERT INTO messages (message) 
        VALUES ('{json.dumps(message, separators=(',', ':'))}')
        """
        db_logger("dbInsertMessage", "Query: " + ' '.join(insert_query.split()))

        CONN_DB_CURSOR.execute(insert_query)
        db.commit()

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
            "function": "dbMessageSend",
            "params": message
        }
        db_logger("dbInsertMessage", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbMessageSearch(CONN_DB_CURSOR, message_id):
    try:
        select_query = f"""
        SELECT message
        FROM messages
        WHERE json_extract(message, '$._id') = '{message_id}'
        """
        db_logger("dbMessageSearch", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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

    if 'm' in update:
        update['m'] = update['m'].replace("'", "''")

    fieldsToUpdate = "message = json_set(message, "
    for index, key in enumerate(update.keys()):
        fieldsToUpdate += ", " if index != 0 else ''
        fieldsToUpdate += "'$." + key + "', " + str(sourceValueToJsonValue(update[key]))
    fieldsToUpdate += ")"

    try:
        update_query = f"""
        UPDATE messages
        SET {fieldsToUpdate}
        WHERE json_extract(message, '$._id') = '{message_id}' 
        """
        db_logger("dbUpdateMessage", "Query: " + ' '.join(update_query.split()))

        CONN_DB_CURSOR.execute(update_query)
        db.commit()
        
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
        post['p'] = post['p'].replace("'", "''")

        insert_query = f"""
        INSERT INTO posts (post) 
        VALUES ('{json.dumps(post, separators=(',', ':'))}')
        """
        db_logger("dbInsertPost", "Query: " + ' '.join(insert_query.split()))

        CONN_DB_CURSOR.execute(insert_query)
        db.commit()

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
            "function": "dbMessageSend",
            "params": post
        }
        db_logger("dbInsertPost", "Return: " + str(return_error), 'ERROR')
        return return_error

def dbPostSearch(CONN_DB_CURSOR, channel_id, post_timestamp):
    try:
        select_query = f"""
        SELECT post
        FROM posts
        WHERE 
            json_extract(post, '$.ts') = {sourceValueToJsonValue(post_timestamp)} AND 
            json_extract(post, '$.cid') = {sourceValueToJsonValue(channel_id)}
        """
        db_logger("dbPostSearch", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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
    
    if 'p' in update:
        update['p'] = update['p'].replace("'", "''")
    
    fieldsToUpdate = "post = json_set(post, "
    for index, key in enumerate(update.keys()):
        fieldsToUpdate += ", " if index != 0 else ''
        fieldsToUpdate += "'$." + key + "', " + str(sourceValueToJsonValue(update[key]))
    fieldsToUpdate += ")"

    try:
        update_query = f"""
        UPDATE posts
        SET {fieldsToUpdate}
        WHERE 
            json_extract(post, '$.ts') = {sourceValueToJsonValue(post_timestamp)} AND 
            json_extract(post, '$.cid') = {sourceValueToJsonValue(channel_id)}
        """
        db_logger("dbUpdatePost", "Query: " + ' '.join(update_query.split()))

        CONN_DB_CURSOR.execute(update_query)
        db.commit()
        
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
        select_query = f"""
        SELECT 
            json_extract(user, '$.callsign'),
            IFNULL(json_extract(user, '$.channel_subscriptions'), '[]'),
            IFNULL(json_extract(user, '$.channel_notifications_since_last_logout'), '[]'),
            IFNULL(json_extract(user, '$.push'), '[]')
        FROM 
            users
        WHERE
            json_extract(user, '$.callsign') != '{sending_callsign}'
        """
        db_logger("dbChannelSubscribers", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
        result = []
        for row in CONN_DB_CURSOR:
            callsign = row[0]
            channel_subscriptions = json.loads(row[1]) if row[1] else []
            channel_notifications_since_last_logout = json.loads(row[2]) if row[2] else []
            push_devices = json.loads(row[3]) if row[3] else []
            enabled_push_devices = list(filter(lambda x: x['isPushEnabled'] == True, push_devices))
            
            if channel_id not in channel_subscriptions:
                continue
            
            enabled_player_ids = []
            for device in enabled_push_devices:
                enabled_player_ids.append(device['playerId']) if device['isPushEnabled'] == True else None
                    
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

def dbUpdateUserPushNotifications(CONN_DB_CURSOR, callsign, channel_id):
    # Update the user with the new push devices
    try:
        update_query = f"""
        UPDATE users
        SET user = json_insert(user, '$.channel_notifications_since_last_logout[#]', {channel_id})
        WHERE json_extract(user, '$.callsign') = {sourceValueToJsonValue(callsign)}
        """
        db_logger("dbUpdateUserPushNotifications", "Query: " + ' '.join(update_query.split()))

        CONN_DB_CURSOR.execute(update_query)
        db.commit()

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
        select_query = f"""
        SELECT 
            * 
        FROM
            (SELECT * FROM posts 
            WHERE json_extract(post, '$.cid') = {sourceValueToJsonValue(channel_id)} 
            ORDER BY json_extract(post, '$.ts') DESC LIMIT {sourceValueToJsonValue(bach_size)})
        ORDER BY json_extract(post, '$.ts') ASC;
        """
        db_logger("dbGetPostsBatch", "Query: " + ' '.join(select_query.split()))

        result = []
        CONN_DB_CURSOR.execute(select_query)
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
        select_query = f"""
        SELECT * FROM
            (SELECT message
            FROM messages
            WHERE 
                (json_extract(message, '$.fc') = {sourceValueToJsonValue(callsign)} AND json_extract(message, '$.tc') = {sourceValueToJsonValue(recipient_callsign)}) OR 
                (json_extract(message, '$.fc') = {sourceValueToJsonValue(recipient_callsign)} AND json_extract(message, '$.tc') = {sourceValueToJsonValue(callsign)})
            ORDER BY json_extract(message, '$.ts') DESC
            LIMIT {message_limit})
        ORDER BY json_extract(message, '$.ts') ASC
        """
        db_logger("dbGetLastMessages", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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
        select_query = f"""
        SELECT COUNT(*)
            FROM messages
            WHERE 
                (json_extract(message, '$.fc') = {sourceValueToJsonValue(callsign)} AND json_extract(message, '$.tc') = {sourceValueToJsonValue(recipient_callsign)}) OR 
                (json_extract(message, '$.fc') = {sourceValueToJsonValue(recipient_callsign)} AND json_extract(message, '$.tc') = {sourceValueToJsonValue(callsign)})
        """
        db_logger("dbMessageCountToRecipient", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)
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
        select_query = f"""
        SELECT user
        FROM users
        WHERE json_extract(user, '$.name_last_updated') > {sourceValueToJsonValue(last_ham_update_timestamp)}
        """
        db_logger("dbGetUpdatedHams", "Query: " + ' '.join(select_query.split()))

        CONN_DB_CURSOR.execute(select_query)

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