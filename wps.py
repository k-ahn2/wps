from env import *
import uuid, time, datetime
import threading
import socket
import json
import zlib, base64
from db import *
import struct

# Environment Variables
env_source = open("env.json", "r")
env = json.load(env_source)
env_source.close()

# Push notifications initialisation, if enabled
if env['notificationsEnabled']:
    import onesignal
    from onesignal.api import default_api
    from onesignal.model.notification import Notification
    ONESIGNAL_PROD_ID = env['notificationsProdId']
    ONESIGNAL_PROD_REST_KEY = env['notificationsProdRestKey']
else:
    print('Push Notifications: Disabled')

# Logging
MIN_LOG_LEVEL = env['minWpsLogLevel']
WPS_LOGFILE = open("wps.log", "a")

def wps_logger(function_name, callsign, log, log_entry_level = 'INFO'):
    if MIN_LOG_LEVEL == 'ERROR' and log_entry_level == 'INFO':
        return
    WPS_LOGFILE.write(f"{datetime.datetime.now().isoformat()} {log_entry_level} {callsign} {function_name} {str(log)}\n") 
    WPS_LOGFILE.flush()

# TCP Socket Setup
HOST = '0.0.0.0'
PORT = env['socketTcpPort']
S = socket.socket()
S.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
S.bind((HOST, PORT))
S.listen()
ALL_THREADS = []

# Global TCP Connections Array
CONNECTIONS = []

# String to return when someone manually connects and sends unknown text
invalid_connect_reponse = """Welcome to WhatsPac Server\r
I didn't recognise that command and guess you have connected manually.\r
To use this service you need to connect using the WhatsPac Client - head to http://whatspac.m0ahn.co.uk:88 and follow the instructions there.\r
You'll now be disconnected, thanks!\r
"""

# Channel names for push notifcations
channel_names = env['channels']

# Compression delimiter as received from the client
# che(192) is sent, split into two bytes by the encoding and received as chr(195) and chr(128)
compression_delimiter_base64 = chr(195) + chr(128)

# Batch Sizes
MB_BATCH_SIZE = 4 # Number of messages to send in batch type 'mb'
CPB_BATCH_SIZE = 4 # Number of posts to send in batch type 'pb'
    
def send_push_notification(heading, message, player_id):
    '''
    Send a OneSignal push notification
    '''

    if env['notificationsEnabled'] == False:
        return {"result": "success", "data": "Notification triggered but sending disabled"}

    ONESIGNAL_ID = ONESIGNAL_PROD_ID
    configuration = onesignal.Configuration(
        app_key = ONESIGNAL_PROD_REST_KEY
    )

    notification = Notification()
    notification.set_attribute('app_id', ONESIGNAL_ID)
    notification.set_attribute('external_id', str(uuid.uuid4()))
    notification.set_attribute('headings', { 'en': heading })
    notification.set_attribute('contents', { 'en': message })
    notification.set_attribute('include_player_ids', [player_id])
    
    # Enter a context with an instance of the API client
    with onesignal.ApiClient(configuration) as api_client:
        # Create an instance of the API class
        api_instance = default_api.DefaultApi(api_client)
    
    try:
        notificationResponse = api_instance.create_notification(notification)
        return {"result": "success", "data": notificationResponse}
    except Exception as e:
        return {"result": "failure", "error": e}

def frame_and_compress_json_object(json_obj):
    '''
    Function pre-processes a JSON object to prepare it for sending
    1. Converts the JSON object to a string with and strips white space between delimiters
    2. Creates a compressed version of the string and adds a compression delimiters
    3. Returns either the compressed or uncompressed string, whichever is shorter

    TODO: Current compression function converts to Base64, which adds 33% overhead. Current compressions achieves up to 40% reduction in size, removing Base64 would improve this further
    '''
    
    uncompressed = json.dumps(json_obj, separators=(',', ':')) + '\r'
    compressed = chr(192) + compress(json.dumps(json_obj, separators=(',', ':'))) + chr(192) + '\r'

    if len(compressed) < len(uncompressed):
        return compressed
    else:
        return uncompressed

def frame_and_compress_json_object_bytes(json_obj):
    
    uncompressed = f"{json.dumps(json_obj, separators=(',', ':'))}\r".encode()
    compressed = chr(195).encode() + compress_bytes(json.dumps(json_obj, separators=(',', ':'))) + chr(195).encode() + '\r'.encode()

    if len(compressed) < len(uncompressed):
        return compressed
    else:
        return uncompressed

def divide_into_batches(array, batch_size):    
    '''
    Takes an input array and divides it into batches of a specified size
    Returns a new array containing the individual batches
    Used for batch sending of messages and posts
    '''
    
    for i in range(0, len(array), batch_size): 
        yield array[i:i + batch_size]

def compress(string_to_compress):
    '''
    Compress and encode with Base64
    '''
    compressed = zlib.compress(string_to_compress.encode('utf-8'), 9)
    encoded = base64.b64encode(compressed).decode('utf-8')
    return encoded

def decompress(string_to_decompress):
    '''
    Decode Baase64 and decompress
    '''
    decoded = base64.b64decode(string_to_decompress)
    decompressed = zlib.decompress(decoded).decode('utf-8')
    return decompressed

def compress_bytes(string_to_compress):
    '''
    Compress string and return as bytes
    '''    
    return zlib.compress(bytes(string_to_compress, 'utf-8'), 9)

def decompress_bytes(string_to_decompress):
    '''
    Decompress bytes as string and return as string
    '''        
    decompressed = zlib.decompress(string_to_decompress).decode('utf-8')
    return decompressed

def connect_handler(CONN_DB_CURSOR, callsign, connect_object, CONN):
    '''
    Receives type `c` JSON from client
    Creates user if they don't exist
    Returns connnect header in response, showing new message and post counts
    Runs all code required to update the client
    '''

    client_channel_subscriptions = connect_object.get('cc', [])
    name_from_client = connect_object.get('n', '-')
    client_version = connect_object.get('v', 0)
    connect_timestamp = round(time.time())

    callsign_search = dbUserSearch(CONN_DB_CURSOR, callsign)
    wps_logger("CONNECT HANDLER", callsign, f"User search result: {callsign_search}")
    close_connection(CONN_DB_CURSOR, CONN_DB_CURSOR, callsign, CONN) if callsign_search['result'] == 'failure' else None

    # Create user if not seen already
    if callsign_search['data'] == None:
        is_new_user = 1
        
        default_subscriptions = env.get('autoSubscribeToChannelIds', [])
        
        new_user_object = {
            "callsign": callsign,
            "name": name_from_client,
            "last_connected": connect_timestamp,
            "name_last_updated": connect_timestamp,
            "channel_subscriptions": default_subscriptions,
        }

        wps_logger("CONNECT HANDLER", callsign, f"New user to create: {new_user_object}")
        create_user_response = dbCreateNewUser(CONN_DB_CURSOR, new_user_object)
        wps_logger("CONNECT HANDLER", callsign, f"Create user response: {create_user_response}")
        close_connection(CONN_DB_CURSOR, CONN_DB_CURSOR, callsign, CONN) if create_user_response['result'] == 'failure' else None

        user_database_record = new_user_object
    else:
        is_new_user = 0
        user_database_record = callsign_search['data']
        
        # Handle historic lastseen field, now replaced with last_connected and last_disconnected
        if 'last_connected' not in user_database_record:
            if 'lastseen' in user_database_record:
                user_database_record['last_connected'] = user_database_record['lastseen']
            else:
                user_database_record['last_connected'] = connect_timestamp
                
            dbCleanupDepracatedLastSeenKey(CONN_DB_CURSOR, callsign)

        wps_logger("CONNECT HANDLER", callsign, "Existing user found")         

    wps_logger("CONNECT HANDLER", callsign, "All connections:")
    for c in CONNECTIONS:
        wps_logger("CONNECT HANDLER", callsign, f"Connection: {c['callsign']}")

    ###
    # Update user status
    ###

    previous_connect_timestamp = user_database_record['last_connected']

    user_updated_fields = { 
        "last_connected": connect_timestamp, 
        "notifications_since_last_logout": [], 
        "channel_notifications_since_last_logout": [], 
        "is_online": 1,
        "last_client_version": client_version
    }
    
    wps_logger("CONNECT HANDLER", callsign, f"Is New User = {is_new_user}")
    
    if is_new_user == 0 and user_database_record['name'] != name_from_client:
        wps_logger("CONNECT HANDLER", callsign, f"Name Update from {user_database_record['name']} to {name_from_client}")        
        user_updated_fields['name'] = name_from_client
        user_updated_fields['name_last_updated'] = connect_timestamp

    user_db_update = dbUserUpdate(CONN_DB_CURSOR, callsign, user_updated_fields)
    wps_logger("CONNECT HANDLER", callsign, f"User update response: {user_db_update}")
    close_connection(CONN_DB_CURSOR, callsign, CONN) if user_db_update['result'] == 'failure' else None
    user_db_record = user_db_update['data']

    # Different handling if this is a connect from a new user or a new browser
    if connect_object["lm"] == 0 and len(client_channel_subscriptions) == 0:
        print(f"{callsign}, {client_version} Connect New {'User' if is_new_user == 1 else 'Browser'}, {datetime.datetime.now().isoformat()}")
        first_time_connect_handler(CONN_DB_CURSOR, callsign, CONN, is_new_user)
    else:
        print(f"{callsign} {client_version} Existing Connect, {datetime.datetime.now().isoformat()}")
        existing_connect_handler(CONN_DB_CURSOR, callsign, connect_object, CONN, user_db_record, previous_connect_timestamp)

def first_time_connect_handler(CONN_DB_CURSOR, callsign, CONN, is_new_user):
    '''
    Handles a connect from a new browser, either because the user is new or an existing user has connected from a new browser
    If an existing user from a new browser, returns the last 10 messages per person messaged
    '''

    wps_logger("FIRST TIME CONNECT HANDLER", callsign, "Running first time connect handler")

    response = {
        "t": "c",
        "mc": 0,
        "pc": 0,
        "w": 1 if is_new_user else 0
    }
    CONN.send(frame_and_compress_json_object(response).encode())
    
    ###
    # Return the user records
    ###

    messaged_users_result = dbGetMessagedUsers(CONN_DB_CURSOR, callsign)
    close_connection(CONN_DB_CURSOR, callsign, CONN) if messaged_users_result['result'] == 'failure' else None
    wps_logger("FIRST TIME CONNECT HANDLER", callsign, f"Messaged users result: {messaged_users_result}")
    messaged_users = messaged_users_result['data']
    
    user_response = { 
        "t": "u",
        "u": []
    }
    
    for user in messaged_users:
        
        resp = {
            "tc": user['callsign'],
            "n": user.get('name', '-'),
            "ls": user['last_disconnected'] if user['last_disconnected'] > user['last_connected'] else user['last_connected'],
        }
        user_response['u'].append(resp)
    
    if len(user_response['u']) > 0:
        wps_logger("FIRST TIME CONNECT HANDLER", callsign, "Sending user first time connect batch")
        CONN.send(frame_and_compress_json_object(user_response).encode())

    ###
    # First time connect handler
    ###

    for recipient in messaged_users:
        connect_batch_array = {
            "t": "mb",
            "md": {
                "mt": 10,
                "mc": 10
            },
            "m": []
        }

        get_last_messages_response = dbGetLastMessages(CONN_DB_CURSOR, callsign, recipient['callsign'], 10)
        close_connection(CONN_DB_CURSOR, callsign, CONN) if get_last_messages_response['result'] == 'failure' else None
        messages = get_last_messages_response['data']
        
        for message in messages:
            del message['ms']
            del message['t']
            del message['lts']
            connect_batch_array['m'].append(message)
        
        wps_logger("FIRST TIME CONNECT HANDLER", callsign, f"Sending first connect batch for callsign {recipient['callsign']}")
        CONN.send(frame_and_compress_json_object(connect_batch_array).encode())
    
    ###
    # Return users currently online
    ###

    online_users_connect_handler(CONN_DB_CURSOR, callsign, CONN)

def existing_connect_handler(CONN_DB_CURSOR, callsign, connect_object, CONN, user_db_record, previous_connect_timestamp):
    '''
    Handles a connect from an existing user and browser
    '''

    last_message = connect_object.get('lm', 0)
    last_message_emoji = connect_object.get('le', 0)
    last_message_edit = connect_object.get('led', 0)
    last_ham_timestamp = connect_object.get('lhts', 0)
    channel_subscriptions = connect_object.get('cc', [])
    client_version = connect_object.get('v', 0)

    ###
    # Get the minimum client version number held in env.json
    ###

    version_source = open("env.json")
    version = json.load(version_source)
    min_client_version = version['minClientVersion']
    recommended_client_version = version['recommendedClientVersion']
    version_source.close()
    wps_logger('CONNECT HANDLER', callsign, f"Minimum client version is {min_client_version}")
    wps_logger('CONNECT HANDLER', callsign, f"Recommended client version is {recommended_client_version}")
    
    ###
    # Send connect response
    ###

    new_messages_response = dbGetMessages(CONN_DB_CURSOR, callsign, last_message)
    new_messages = new_messages_response['data']
    new_messages_count = len(new_messages)

    connect_response = {
        "t": "c",
        "mc": new_messages_count,
        "pc": 0
    }

    # Only return if there is a newer min version
    if client_version < recommended_client_version:
        connect_response['v'] = recommended_client_version

    wps_logger('CONNECT HANDLER', callsign, f"Channel subscriptions {channel_subscriptions}")

    for channel in channel_subscriptions:
        
        new_posts = dbGetPosts(CONN_DB_CURSOR, channel['cid'], channel['lp'])
        new_posts_len = len(new_posts['data'])

        wps_logger('CONNECT HANDLER', callsign, f"Channel {channel} count {new_posts_len}")
        
        connect_response['pc'] += new_posts_len

    CONN.send(frame_and_compress_json_object(connect_response).encode())
    #CONN.send(struct.pack('>H', 3800))
    #CONN.send(frame_and_compress_json_object_bytes(connect_response))
    wps_logger('CONNECT HANDLER', callsign, f"Connect response to client {connect_response}")

    ###
    # Terminate any client version < min_version
    # Runs after the connect response so the client can see the upgrade prompt
    ###

    if client_version < min_client_version:
        time.sleep(5)
        wps_logger('CONNECT HANDLER', callsign, f"Client version {client_version} less than minimum version {min_client_version}", 'ERROR')
        close_connection(CONN_DB_CURSOR, callsign, CONN)
        return

    ###
    # Return users currently online
    ###

    online_users_connect_handler(CONN_DB_CURSOR, callsign, CONN)

    ###
    # Return new messages
    ###

    # Remove fields not required to be sent over the air
    for message in new_messages:
        message.pop("ms", None) # Added by the client
        message.pop("lts", None) # Server only
        message.pop("t", None) # Knows the type is m as it's in a message batch

    message_batch = {
        "t": "mb",
        "md": {
            "mt": new_messages_count,
            "mc": 0
        },
        "m": []
    }
    
    if new_messages_count > 0:

        new_messages_batched = list(divide_into_batches(new_messages, MB_BATCH_SIZE))
        
        running_message_count = 0
        for messages_batch in new_messages_batched:
            if (running_message_count + MB_BATCH_SIZE) < new_messages_count:
                running_message_count += MB_BATCH_SIZE
            else:
                running_message_count = new_messages_count

            message_batch['m'] = messages_batch
            message_batch['md']['mc'] = running_message_count
            wps_logger('CONNECT HANDLER', callsign, f"New messages response batch - up to message {running_message_count} of total {new_messages_count}")
            CONN.send(frame_and_compress_json_object(message_batch).encode()) 
            #CONN.send(frame_and_compress_json_object_bytes(message_batch))

    ###
    # Return new message edits
    ###    
    
    wps_logger('CONNECT HANDLER', callsign, "Starting message edit check")
    
    # Returns the whole message
    new_message_edits = dbGetMessageEdits(CONN_DB_CURSOR, callsign, last_message, last_message_edit)

    if len(new_message_edits['data']) > 0:
        
        # Create a blank parent return object
        message_edits_batch = {
            "t": "medb",
            "med": []
        }
        
        for message in new_message_edits['data']:
            wps_logger('CONNECT HANDLER', callsign, f"Edited Message: {message}")
            
            # Create a blank message level response
            edited_message = {
                "_id": message['_id'],
                "edts": message['edts'],
                "m": message['m']
            }
            message_edits_batch['med'].append(edited_message)

        wps_logger('CONNECT HANDLER', callsign, f"New edits to return: {message_edits_batch}")
        CONN.send(frame_and_compress_json_object(message_edits_batch).encode())  
    
    ###
    # Return new emojis
    ###
    
    wps_logger('CONNECT HANDLER', callsign, "Starting emoji check")
    
    # Returns the whole message
    new_message_emojis = dbGetMessageEmojis(CONN_DB_CURSOR, callsign, last_message, last_message_emoji)

    if len(new_message_emojis['data']) > 0:
       
        # Create a blank parent return object
        message_emojis_batch = {
            "t": "memb",
            "mem": []
        }
        
        for message in new_message_emojis['data']:
            wps_logger('CONNECT HANDLER', callsign, f"Message with updated Emoji(s): {message}")
            
            # Create a blank message level response
            latest_emojis = {
                "_id": message['_id'],
                "e": message['e'],
                "ets": message['ets']
            }
            message_emojis_batch['mem'].append(latest_emojis)

        wps_logger('CONNECT HANDLER', callsign, f"Emoji batch to return: {message_emojis_batch}")
        CONN.send(frame_and_compress_json_object(message_emojis_batch).encode()) 

    ###
    # Return the user records, so the user has last seen times and name update
    ###

    messaged_users_result = dbGetMessagedUsers(CONN_DB_CURSOR, callsign)
    
    user_response = { 
        "t": "u",
        "u": []
    }
    
    for user in messaged_users_result['data']:
        
        if user['name'] == None or user['name_last_updated'] == None:
            wps_logger('CONNECT HANDLER', callsign, f"User {user['callsign']} has no name or name_last_updated, skipping")
            continue
        
        if user['last_connected'] > previous_connect_timestamp or user['name_last_updated'] > previous_connect_timestamp:
            
            resp = {
                "tc": user['callsign'],
                "n": user.get('name', '-'),
                "ls": user['last_connected'],
            }
            user_response['u'].append(resp)

    wps_logger('CONNECT HANDLER', callsign, f"User updates response: {user_response}")

    if len(user_response['u']) > 0:
        CONN.send(frame_and_compress_json_object(user_response).encode())

    ###
    # Process channels
    ### 
    
    # Quit if no channels are subscribed
    if 'cc' not in connect_object:
        connect_object['cc'] = []
    
    user_server_channel_subscriptions = user_db_record.get('channel_subscriptions', [])

    wps_logger('CONNECT HANDLER', callsign, f"Client Subscriptions: {connect_object['cc']}")
    wps_logger('CONNECT HANDLER', callsign, f"Server Subscriptions: {user_server_channel_subscriptions}")
    # Check for a mismatch between the server and client view of channel subscriptions
    if len(connect_object['cc']) != len(user_server_channel_subscriptions):
        wps_logger('CONNECT HANDLER', callsign, "Warn, mismatch in client and server channel subscriptions")

    for channel_object in connect_object['cc']:
        channels_connect_handler(CONN_DB_CURSOR, channel_object, callsign, CONN)

    ###
    # Return name changes
    ###

    response = { "t": "he", "h": [] }

    updated_hams_result = dbGetUpdatedHams(CONN_DB_CURSOR, last_ham_timestamp)

    for ham in updated_hams_result['data']:
        
        response["h"].append({
            "c": ham['callsign'],
            "n": ham['name'],
            "ts": ham.get('name_last_updated', 0)
        })

    if len(response["h"]) > 0:
        wps_logger("CONNECT HANDLER", callsign, f"User name change response {response}")
        CONN.send(frame_and_compress_json_object(response).encode()) 

def online_users_connect_handler(CONN_DB_CURSOR, callsign, CONN):
    ###
    # Returns all online users to the client, called only at the point of connect
    ###    

    wps_logger('ONLINE STATUS', callsign, 'Starting Online Users Handler')

    ###
    # Return Online Users
    ###

    online_users = dbGetOnlineUsers(CONN_DB_CURSOR)
    
    online_response = { 
        "t": "o",
        "o": []
    }

    for c in online_users['data']:
        online_response["o"].append(c['callsign'])

    if len(online_response["o"]) > 0:
        wps_logger('ONLINE STATUS', callsign, f"Online users response: {online_response}")
        CONN.send(frame_and_compress_json_object(online_response).encode())

def channels_connect_handler(CONN_DB_CURSOR, channel_object, callsign, CONN):
    '''
    Called on connect if the connect object contains channel subscriptions
    Is called once per channel subscribed
    Returns new posts in batches of 4
    Returns historic posts edited or with new emojis
    '''
        
    wps_logger('CONNECT HANDLER', callsign, f"Starting Connect Handler for channel {channel_object['cid']}")
    
    ###
    # Channels Post Batch Array
    ###

    channel_posts_batch_array = {
        "t": "cpb",
        "cid": channel_object['cid'],
        "m": {
            "pt": 0,
            "pc": 0
        },
        "p": []
    }

    ###
    # Return new channel posts
    ###
    
    new_channel_posts_response = dbGetPosts(CONN_DB_CURSOR, channel_object['cid'], channel_object['lp'])
    new_channel_posts = new_channel_posts_response['data']

    channel_posts_batch_array['m']['pt'] = len(new_channel_posts)

    new_posts = []
    
    if len(new_channel_posts) > 0:

        new_posts_batched = list(divide_into_batches(new_channel_posts, CPB_BATCH_SIZE))
        
        post_count = 0
        for post_batch in new_posts_batched:
            if (post_count + CPB_BATCH_SIZE) < channel_posts_batch_array['m']['pt']:
                post_count += CPB_BATCH_SIZE
            else:
                post_count = channel_posts_batch_array['m']['pt']

            channel_posts_batch_array['p'] = post_batch
            channel_posts_batch_array['m']['pc'] = post_count
            wps_logger('CONNECT HANDLER', callsign, f"Channel connect response batch: {channel_posts_batch_array}")
            CONN.send(frame_and_compress_json_object(channel_posts_batch_array).encode()) 

    ###
    # Channels Emojis Batch Array
    ###

    wps_logger('CONNECT HANDLER', callsign, "Starting channel emoji check")

    channel_emojis_batch_array = {
        "t": "cpemb",
        "e": []
    }

    posts_with_emoji_updates_response = dbGetPostEmojis(CONN_DB_CURSOR, channel_object['cid'], channel_object['le'], channel_object['lp'])
    close_connection(CONN_DB_CURSOR, callsign, CONN) if posts_with_emoji_updates_response['result'] == 'failure' else None
    posts_with_emoji_updates = posts_with_emoji_updates_response['data']

    for post in posts_with_emoji_updates:
        emoji_update = {
            "cid": post['cid'],
            "ts": post['ts'],
            "ets": post['ets'],
            "e": []
        }

        for emoji in post['e']:
            emoji_update['e'].append(emoji) if len(emoji['c']) > 0 else None
        
        channel_emojis_batch_array['e'].append(emoji_update)
    
    if len(channel_emojis_batch_array['e']) > 0:
        wps_logger('CONNECT HANDLER', callsign, f"Channel emoji update batch: {channel_emojis_batch_array}")
        CONN.send(frame_and_compress_json_object(channel_emojis_batch_array).encode())

    ###
    # Return post edits
    ###    
    
    wps_logger('CONNECT HANDLER', callsign, "Starting post edit check")
    
    posts_with_edit_updates_response = dbGetPostEdits(CONN_DB_CURSOR, channel_object['cid'], channel_object['led'], channel_object['lp'])
    posts_with_edit_updates = posts_with_edit_updates_response['data']

    # Create a blank parent return object
    edited_posts_resp = {
        "t": "cpedb",
        "ed": []
    }
    
    for edited_post in posts_with_edit_updates:
        wps_logger('CONNECT HANDLER', callsign, f"Edited Post: {edited_post}")
        
        # Check edit is not already returned in the new posts response
        found_in_new_posts = False
        for new_post in new_posts:
            if new_post['ts'] == edited_post['ts'] and new_post['cid'] == edited_post['cid']:
                wps_logger('CONNECT HANDLER', callsign, "Post found in connect batch, not resending")
                found_in_new_posts = True
                break
        
        if not found_in_new_posts:
            post_edit_to_return = {
                "cid": edited_post['cid'],
                "ts": edited_post['ts'],
                "edts": edited_post['edts'],
                "p": edited_post['p']
            }
            edited_posts_resp['ed'].append(post_edit_to_return) 

    if len(edited_posts_resp['ed']) > 0:
        wps_logger('CONNECT HANDLER', callsign, f"New edits to return: {edited_posts_resp}")
        CONN.send(frame_and_compress_json_object(edited_posts_resp).encode())  

def pairing_handler(CONN_DB_CURSOR, callsign, CONN):
    '''
    Sets a flag and timestamp on the user record to enable pairing of Packet Alerts
    '''

    pair_start_time = round(time.time())

    enable_pairing_update = { 
        "pair_enabled": True, 
        "pair_start_time": pair_start_time 
    }

    enable_pairing_response = dbUserUpdate(CONN_DB_CURSOR, callsign, enable_pairing_update)
    wps_logger("CONNECT HANDLER", callsign, f"Enable pairing response: {enable_pairing_response}")
    close_connection(CONN_DB_CURSOR, callsign, CONN) if enable_pairing_response['result'] == 'failure' else None

    response = {
        "t": "p",
        "e": True,
        "st": pair_start_time
    }
    CONN.send((json.dumps(response, separators=(',', ':'))+'\r').encode())
    return 

def message_send_handler(CONN_DB_CURSOR, message, callsign, CONN):
    '''
    Handles when the user sends a new message
    Either sends in real-time, or, sends the user a push notification if setup
    Push notifications are only sent for the first message from a user since last login
    '''

    wps_logger("MESSAGE HANDLER", callsign, f"Received: {message}")

    if len(message['m']) == 0:
        wps_logger("MESSAGE HANDLER", callsign, "Zero length message, ignore")
        return None

    # Check if message already in database. Could have been processed but client didn't receive the acknowledgement
    message_search = dbMessageSearch(CONN_DB_CURSOR, message['_id'])
    close_connection(CONN_DB_CURSOR, callsign, CONN) if message_search['result'] == 'failure' else None
    message_search_result = message_search['data']

    try:
        # copy the message and add fields required for the WPS database, but don't need returning to the client
        message_to_write_to_wps_database = json.loads(json.dumps(message))
        message_to_write_to_wps_database['lts'] = round(time.time())
        message_to_write_to_wps_database['ms'] = 1
        
        client_response = {
            "t": "mr",
            "_id": message['_id']
        }

        # Get the message count to the recipient before inserting, so we can send the user record if this is the first message
        message_count_to_recipient_result = dbMessageCountToRecipient(CONN_DB_CURSOR, callsign, message['tc'])

        if message_search_result != None and "_id" in message_search_result.get("_id", {}):
            wps_logger("MESSAGE HANDLER", callsign, "Message already exists, sending client ack and finishing processing")
            CONN.send((json.dumps(client_response, separators=(',', ':'))+'\r').encode())
            return
        else:
            message_insert_response = dbInsertMessage(CONN_DB_CURSOR, message_to_write_to_wps_database)
            close_connection(CONN_DB_CURSOR, callsign, CONN) if message_insert_response['result'] == 'failure' else None
            wps_logger("MESSAGE HANDLER", callsign, f"Client acknowledgment is {client_response}")
            CONN.send((json.dumps(client_response, separators=(',', ':'))+'\r').encode())
            
        sent_in_real_time = False
        # If the recipient is connected, send the message in real-time
        for C in CONNECTIONS:
            wps_logger("MESSAGE HANDLER", callsign, f"Connections {C['callsign']}")
            # Attempt to find the recipient in active connections
            if C['callsign'] == message['tc']:
                wps_logger("MESSAGE HANDLER", callsign, f"{message['tc']} is logged in, sending in real-time")
                wps_logger("MESSAGE HANDLER", callsign, f"Sending to: {C['socket']}")
                
                # Check if the recipient has already been messaged by the sender, if not send the user record
                close_connection(CONN_DB_CURSOR, callsign, CONN) if message_count_to_recipient_result['result'] == 'failure' else None
                message_count_to_recipient = message_count_to_recipient_result['data']

                if message_count_to_recipient == 0:
                    wps_logger("MESSAGE HANDLER", callsign, f"First message, sending user enquiry response")
                    user_enquiry_handler(CONN_DB_CURSOR, { "c": message['fc'] }, callsign, C['socket'])
                
                C['socket'].sendall((json.dumps(message, separators=(',', ':'))+'\r').encode())
                sent_in_real_time = True

        if sent_in_real_time:
            return
        
        # User isn't connected so send a push if enabled
        callsign_search_response = dbUserSearch(CONN_DB_CURSOR, message['tc'])
        close_connection(CONN_DB_CURSOR, callsign, CONN) if callsign_search_response['result'] == 'failure' else None

        callsign_db_record = callsign_search_response['data']

        try:
            notifications_since_last_logout = callsign_db_record['notifications_since_last_logout']
        except:
            notifications_since_last_logout = []

        try:
            push = callsign_db_record['push']
        except:
            push = []

        push_counter = 0
        
        # Check the sending callsign (callsign) hasn't already sent the recieving callsign a notification since they last logged in
        if len(push) > 0 and callsign not in notifications_since_last_logout:
            notifications_since_last_logout.append(callsign)
            wps_logger("MESSAGE HANDLER", callsign, "Found push entries and no notification since last logout")
            for p in push:
                if p['isPushEnabled']:
                    wps_logger("MESSAGE HANDLER", callsign, f"Sending to push to: {p['playerId']}")
                    pushresp = send_push_notification('Message Alert', 'New message(s) from ' + callsign, p['playerId'])

                    wps_logger("MESSAGE HANDLER", callsign, f"Push response: {pushresp}")
                    push_counter = push_counter = 0 + 1
        else:
            wps_logger("MESSAGE HANDLER", callsign, "No push entries or have already messaged")

        if push_counter > 0:
            update = { "notifications_since_last_logout": notifications_since_last_logout }
            update_response = dbUserUpdate(CONN_DB_CURSOR, message['tc'], update)
            close_connection(CONN_DB_CURSOR, callsign, CONN) if update_response['result'] == 'failure' else None

    except Exception as e:
        wps_logger("MESSAGE HANDLER", callsign, f"Error {e}")

def message_edit_handler(CONN_DB_CURSOR, msg_update, callsign, CONN):
    '''
    Handles when the user edits a message
    '''
    
    wps_logger("MESSAGE EDIT HANDLER", callsign, f"Received: {msg_update}")

    update = { "edts": msg_update['edts'], "m": msg_update['m'], "ed": 1 }

    message_edit_response = dbUpdateMessage(CONN_DB_CURSOR, msg_update['_id'], update)
    wps_logger("MESSAGE EDIT HANDLER", callsign, f"Message edit response {message_edit_response}")
    edited_message = message_edit_response['data']
    close_connection(CONN_DB_CURSOR, callsign, CONN) if message_edit_response['result'] == 'failure' else None

    wps_logger("MESSAGE EDIT HANDLER", callsign, f"Message after updating {edited_message}")

    resp = {}
    resp['t'] = "mr"
    resp['_id'] = msg_update['_id']
    wps_logger("MESSAGE EDIT HANDLER", callsign, f"Edit message response is {resp}")
    CONN.send((json.dumps(resp, separators=(',', ':'))+'\r').encode())

    send = {
        "t": "med",
        "_id": msg_update['_id'],
        "m": msg_update['m']
    }

    for C in CONNECTIONS:
        if C['callsign'] == edited_message['tc']:
            wps_logger("MESSAGE HANDLER", callsign, f"{edited_message['tc']} is logged in, sending in real-time")
            wps_logger("MESSAGE HANDLER", callsign, f"Sending to: {C['socket']}")
            C['socket'].sendall((json.dumps(send, separators=(',', ':'))+'\r').encode())

def message_emoji_handler(CONN_DB_CURSOR, emoji_object, callsign):
    '''
    Handles when the user reacts to a message
    Unlike a new message or an edit, they are not guaranteed delivery to the server. No acknowledgement is sent to the client
    '''

    message_to_update_response = dbMessageSearch(CONN_DB_CURSOR, emoji_object['_id'])
    message_to_update = message_to_update_response['data']

    wps_logger("MESSAGE EMOJI HANDLER", callsign, f"Message To Update: {message_to_update}")
    wps_logger("MESSAGE EMOJI HANDLER", callsign, f"Emoji Update: {emoji_object}")

    updated_emojis = message_to_update.get("e", [])

    wps_logger("MESSAGE EMOJI HANDLER", callsign, f"Emojis array before updating: {updated_emojis}")
    updated_emojis.append(emoji_object['e']) if emoji_object['a'] == 1 and emoji_object['e'] not in updated_emojis else None
    updated_emojis.remove(emoji_object['e']) if emoji_object['a'] == 0 and emoji_object['e'] in updated_emojis else None

    wps_logger("MESSAGE EMOJI HANDLER", callsign, f"Emojis after updating: {updated_emojis}")

    update = { "e": updated_emojis, "ets": emoji_object['ets'] }

    message_edit_response = dbUpdateMessage(CONN_DB_CURSOR, emoji_object['_id'], update)
    edited_message = message_edit_response['data']

    wps_logger("MESSAGE EMOJI HANDLER", callsign, f"Message after updating {edited_message}")
    
    real_time_resp = { "t": "mem", "_id": emoji_object['_id'], "e": updated_emojis, "ets": emoji_object['ets'] }

    for C in CONNECTIONS:
        if C['callsign'] == message_to_update['fc']:
            wps_logger("MESSAGE EMOJI HANDLER", callsign, f"{message_to_update['fc']} is logged in, sending emoji update in real-time")
            C['socket'].sendall((json.dumps(real_time_resp, separators=(',', ':'))+'\r').encode())

def user_enquiry_handler(CONN_DB_CURSOR, user_enquiry, callsign, CONN):
    '''
    Used to enquire whether a user is registered or not
    DEPRECATED, Ham Enquiry will be evolved to handle this in future
    '''

    user_enquiry_response = dbUserSearch(CONN_DB_CURSOR, user_enquiry['c'])
    wps_logger("USER ENQUIRY HANDLER", callsign, f"User enquiry response: {user_enquiry_response}")
    user_enquiry_response = user_enquiry_response['data']

    if user_enquiry_response != None:        
        last_connected = user_enquiry_response.get('last_connected')
        last_disconnected = user_enquiry_response.get('last_disconnected', 0)
        
        response = {
            "t": "ue",
            "r": True,
            "tc": user_enquiry['c'],
            "n": user_enquiry_response['name'],
            "ls": last_disconnected if last_disconnected > last_connected else last_connected
        }
    else:
        response = {
            "t": "ue",
            "r": False,
            "tc": user_enquiry['c']
        }        

    CONN.send((json.dumps(response, separators=(',', ':'))+'\r').encode())
    return    

def ham_enquiry_handler(CONN_DB_CURSOR, ham_enquiry, callsign, CONN):
    '''
    Accepts an array of callsigns and returns the name and last connected or disconnected timestamp
    Is used by channels and will be used by messages in future
    '''
        
    wps_logger("HAM ENQUIRY HANDLER", callsign, "Starting")

    if len(ham_enquiry["h"]) == 0:
        return

    response = {
        "t": "he",
        "h": []
    }

    for individual_ham in ham_enquiry["h"]:
    
        ham_enquiry_response = dbUserSearch(CONN_DB_CURSOR, individual_ham)
        wps_logger("USER ENQUIRY HANDLER", callsign, f"Ham enquiry response: {ham_enquiry_response}")
        ham_enquiry_response = ham_enquiry_response['data']
        
        if ham_enquiry_response != None:
            response["h"].append({

                "c": ham_enquiry_response['callsign'],
                "n": ham_enquiry_response.get('name', ''),
                "ts": ham_enquiry_response.get('name_last_updated', 0),
            })

    wps_logger("HAM ENQUIRY HANDLER", callsign, f"Response: {response}")
    CONN.send(frame_and_compress_json_object(response).encode()) 
    return  

def post_handler(CONN_DB_CURSOR, post, callsign, CONN):
    '''
    Handles when the user sends a new post to a channel
    Either sends in real-time to connected subscribers, or, sends the user a push notification if setup
    Push notifications are only sent for the first message from a user since last login
    '''

    wps_logger("CHANNELS POST HANDLER", callsign, f"Received: {post}")

    if len(post['p']) == 0:
        wps_logger("CHANNELS POST HANDLER", callsign, "Zero length message, ignore")
        return None
    
    # Check for existing post, incase this is a resend
    post_search_result = dbPostSearch(CONN_DB_CURSOR, post['cid'], post['ts'])
    close_connection(CONN_DB_CURSOR, callsign, CONN) if post_search_result['result'] == 'failure' else None
    post_search = post_search_result['data']

    try:
        delivery_timestamp = round(time.time()*1000)
        post['dts'] = delivery_timestamp

        client_response = {
            "t": "cpr",
            "ts": post['ts'],
            "dts": delivery_timestamp
        }

        if post_search != None and post_search['p'] == post['p']:
            wps_logger("CHANNELS POST HANDLER", callsign, "Existing post found and posted text the same, not inserting again")
            CONN.send((json.dumps(client_response, separators=(',', ':'))+'\r').encode())
            return
        else:
            wps_logger("CHANNELS POST HANDLER", callsign, "No existing post found, inserting")
            wps_logger("CHANNELS POST HANDLER", callsign, f"Post to insert: {post}")
            post_insert_response = dbInsertPost(CONN_DB_CURSOR, json.loads(json.dumps(post)))
            close_connection(CONN_DB_CURSOR, callsign, CONN) if post_insert_response['result'] == 'failure' else None
            wps_logger("CHANNELS POST HANDLER", callsign, f"Client acknowledgment is {post_insert_response}")
            CONN.send((json.dumps(client_response, separators=(',', ':'))+'\r').encode())
        
        subscribers_to_receive_push_notification_response = dbChannelSubscribers(CONN_DB_CURSOR, callsign, post['cid'])
        close_connection(CONN_DB_CURSOR, callsign, CONN) if subscribers_to_receive_push_notification_response['result'] == 'failure' else None
        subscribers_to_receive_push_notification = subscribers_to_receive_push_notification_response['data']
        wps_logger("CHANNELS POST HANDLER", callsign, f"Subscribers to receive push notification: {subscribers_to_receive_push_notification_response}")

        callsigns_to_process = [s['callsign'] for s in subscribers_to_receive_push_notification]

        sent_post_in_real_time = []

        # Send to connected subscribers in real time
        wps_logger("CHANNELS POST HANDLER", callsign, f"Connections {CONNECTIONS}")

        for C in CONNECTIONS:
            if C['callsign'] != post['fc'] and C['callsign'] in callsigns_to_process:
                wps_logger("CHANNELS POST HANDLER", callsign, f"Sending real-time to: {C['callsign']}")
                C['socket'].sendall((json.dumps(post, separators=(',', ':'))+'\r').encode())
                sent_post_in_real_time.append(C['callsign'])

        # Send to remaining subscribers not online and with push enabled      
        for subscriber in subscribers_to_receive_push_notification:
            wps_logger("CHANNELS POST HANDLER", callsign, f"Processing {subscriber['callsign']}")

            if subscriber['callsign'] in sent_post_in_real_time:
                wps_logger("CHANNELS POST HANDLER", callsign, "Already sent in real-time")
                continue
            
            if post['cid'] in subscriber['channel_notifications_since_last_logout']:
                wps_logger("CHANNELS POST HANDLER", callsign, "Already received push for this channel since last logout")
                continue

            for playerId in subscriber['enabled_player_ids']:
                wps_logger("CHANNELS POST HANDLER", callsign, f"Sending push to {playerId}")
                push_resp = send_push_notification('Channel Post Alert', 'New Post(s) in #' + channel_names[str(post['cid'])], playerId)
                wps_logger("CHANNELS POST HANDLER", callsign, f"Push response: {push_resp}")
                post_update_response = dbUpdateUserPushNotifications(CONN_DB_CURSOR, subscriber['callsign'], post['cid'])
                close_connection(CONN_DB_CURSOR, callsign, CONN) if post_update_response['result'] == 'failure' else None
                wps_logger("CHANNELS POST HANDLER", callsign, f"User update response is {post_update_response}")

        return None

    except Exception as e:
        wps_logger("CHANNELS POST HANDLER", callsign, f"Error {e}")

def post_edit_handler(CONN_DB_CURSOR, post_update, callsign, CONN):
    '''
    Handles when the user edits a post
    '''

    wps_logger("POST EDIT HANDLER", callsign, f"Received: {post_update}")
    
    post_edit_update = {
        "edts": post_update['edts'], 
        "p": post_update['p'], 
        "ed": 1 
    }

    post_edit_response = dbUpdatePost(CONN_DB_CURSOR, post_update['cid'], post_update['ts'], post_edit_update)
    wps_logger("POST EDIT HANDLER", callsign, f"Message edit response: {post_edit_response}")
    close_connection(CONN_DB_CURSOR, callsign, CONN) if post_edit_response['result'] == 'failure' else None
    
    client_acknowledgement = {
        "t": "cpr",
        "ts": post_update['ts']
    }
    CONN.send((json.dumps(client_acknowledgement, separators=(',', ':'))+'\r').encode())

    update_to_connected_clients = {
        "t": "cped",
        "ts": post_update['ts'],
        "edts": post_update['edts'],
        "cid": post_update['cid'],
        "p": post_update['p']
    }

    channel_subscribers_response = dbChannelSubscribers(CONN_DB_CURSOR, callsign, post_update['cid'])
    close_connection(CONN_DB_CURSOR, callsign, CONN) if channel_subscribers_response['result'] == 'failure' else None
    channel_subscriber_objects = channel_subscribers_response['data']
    subscribing_callsigns = [s['callsign'] for s in channel_subscriber_objects]

    for C in CONNECTIONS:
        if C['callsign'] != callsign and C['callsign'] in subscribing_callsigns:
            wps_logger("MESSAGE HANDLER", callsign, f"Sending to: {C['socket']}")
            C['socket'].sendall((json.dumps(update_to_connected_clients, separators=(',', ':'))+'\r').encode())

def post_emoji_handler(CONN_DB_CURSOR, emoji_object, callsign, CONN):
    '''
    Handles when the user reacts to a post
    Unlike a new post or an edit, they are not guaranteed delivery to the server. No acknowledgement is sent to the client
    '''
    
    wps_logger("CHANNELS EMOJI HANDLER", callsign, f"Emoji Update: {emoji_object}")

    post_search_result = dbPostSearch(CONN_DB_CURSOR, emoji_object['cid'], emoji_object['ts'])
    close_connection(CONN_DB_CURSOR, callsign, CONN) if post_search_result['result'] == 'failure' else None
    post = post_search_result['data']

    wps_logger("CHANNELS EMOJI HANDLER", callsign, f"Post To Update: {post}")
    
    post_emojis = post.get("e", [])
    wps_logger("CHANNELS EMOJI HANDLER", callsign, f"Emojis array before updating: {post_emojis}")

    if emoji_object['a'] == 1:

        foundEmoji = False
        for e in post_emojis:
            if e['e'] == emoji_object['e']:
                foundEmoji = True
                if callsign in e['c']:
                    break
                else:
                    e['c'].append(callsign)
                    break
        
        if not foundEmoji:
            c = []
            c.append(callsign)
            post_emojis.append({'e': emoji_object['e'], 'c': c})
            
    elif emoji_object['a'] == 0:

        for e in post_emojis:
            if e['e'] == emoji_object['e']:
                e['c'].remove(callsign) if callsign in e['c'] else None
                break
    
    wps_logger("CHANNELS EMOJI HANDLER", callsign, f"Emojis array after updating: {post_emojis}")
                
    post_update = { 
        "e": post_emojis, 
        "ets": round(time.time()*1000) 
    }

    post_emoji_response = dbUpdatePost(CONN_DB_CURSOR, emoji_object['cid'], emoji_object['ts'], post_update)
    wps_logger("CHANNELS EMOJI HANDLER", callsign, f"Post Update response {post_emoji_response}")
    close_connection(CONN_DB_CURSOR, callsign, CONN) if post_emoji_response['result'] == 'failure' else None
    updated_post = post_emoji_response['data']    

    wps_logger("CHANNELS EMOJI HANDLER", callsign, f"Post after updating: {updated_post}")

    channel_subscribers_response = dbChannelSubscribers(CONN_DB_CURSOR, callsign, emoji_object['cid'])
    close_connection(CONN_DB_CURSOR, callsign, CONN) if channel_subscribers_response['result'] == 'failure' else None
    channel_subscriber_objects = channel_subscribers_response['data']
    subscribing_callsigns = [s['callsign'] for s in channel_subscriber_objects]

    emoji_object['fc'] = callsign
    
    for C in CONNECTIONS:
        if C['callsign'] != callsign and C['callsign'] in subscribing_callsigns:
            wps_logger("CHANNELS EMOJI HANDLER", callsign, f"Sending to: {C['callsign']}")
            C['socket'].sendall((json.dumps(emoji_object, separators=(',', ':'))+'\r').encode())

def channel_subscribe_handler(CONN_DB_CURSOR, subscribe_request, callsign, CONN):
    '''
    Handles when the user either subscribes or unsubscribes from a channel
    Returns an acknowledgement and count of new posts (which is all posts if the client has no data)
    '''
    
    wps_logger("CHANNELS SUBSCRIBE HANDLER", callsign, f"Received: {subscribe_request}")

    callsign_search = dbUserSearch(CONN_DB_CURSOR, callsign)
    close_connection(CONN_DB_CURSOR, callsign, CONN) if callsign_search['result'] == 'failure' else None
    user_record = callsign_search['data']

    channel_subscriptions = user_record.get('channel_subscriptions', [])

    wps_logger("CHANNELS SUBSCRIBE HANDLER", callsign, f"Existing subscriptions: {channel_subscriptions}")

    if subscribe_request['s'] == 1:
        if subscribe_request['cid'] not in channel_subscriptions:
            channel_subscriptions.append(subscribe_request['cid'])
    
        update_object = { "channel_subscriptions": channel_subscriptions }
        user_update_response = dbUserUpdate(CONN_DB_CURSOR, callsign, update_object)
        close_connection(CONN_DB_CURSOR, callsign, CONN) if user_update_response['result'] == 'failure' else None
        wps_logger("CHANNELS SUBSCRIBE HANDLER", callsign, f"User update response: {user_update_response}")

        new_post_count_reponse = dbGetPosts(CONN_DB_CURSOR, subscribe_request['cid'], subscribe_request['lcp'])
        close_connection(CONN_DB_CURSOR, callsign, CONN) if new_post_count_reponse['result'] == 'failure' else None
        new_post_count = len(new_post_count_reponse['data'])
        wps_logger('CONNECT HANDLER', callsign, f"Count is: {new_post_count}")
        
        client_acknowledgement = {
            "t": "cs",
            "cid": subscribe_request['cid'],
            "s": 1,
            "pc": new_post_count
        }
        wps_logger("CHANNELS SEND HANDLER", callsign, f"Subscribe response is {client_acknowledgement}")
        CONN.send((json.dumps(client_acknowledgement, separators=(',', ':'))+'\r').encode())

    elif subscribe_request['s'] == 0:
        if subscribe_request['cid'] in channel_subscriptions:
            channel_subscriptions.remove(subscribe_request['cid'])

        update_object = { "channel_subscriptions": channel_subscriptions }
        user_update_response = dbUserUpdate(CONN_DB_CURSOR, callsign, update_object)
        close_connection(CONN_DB_CURSOR, callsign, CONN) if user_update_response['result'] == 'failure' else None
        wps_logger("CHANNELS SUBSCRIBE HANDLER", callsign, f"User update response: {user_update_response}")

        client_acknowledgement = {
            "t": "cs",
            "cid": subscribe_request['cid'],
            "s": 0
        }
        wps_logger("CHANNELS SEND HANDLER", callsign, f"Unsubscribe response is {client_acknowledgement}")
        CONN.send((json.dumps(client_acknowledgement, separators=(',', ':'))+'\r').encode())

    wps_logger("CHANNELS SUBSCRIBE HANDLER", callsign, f"Subscriptions now: {channel_subscriptions}")
    
    return None

def post_batch_handler(CONN_DB_CURSOR, post_batch_request, callsign, CONN):
    '''
    Handles when a user first subscribes to a channel and they select the number of historic posts to download
    Returns in batches of 4
    TODO: Has some duplication with channels_connect_handler, should be rationalised
    '''

    wps_logger('POST BATCH HANDLER', callsign, f"Posts batch request: {post_batch_request}")

    CPB_BATCH_SIZE = 4

    channel_posts_batch_array = {
        "t": "cpb",
        "cid": post_batch_request['cid'],
        "m": {
            "pt": 0,
            "pc": 0
        },
        "p": []
    }

    posts = dbGetPostsBatch(CONN_DB_CURSOR, post_batch_request['cid'], post_batch_request['pc'])
    close_connection(CONN_DB_CURSOR, callsign, CONN) if posts['result'] == 'failure' else None
    posts = posts['data']

    wps_logger('POST BATCH HANDLER', callsign, f"Posts returned: {posts}")

    channel_posts_batch_array['m']['pt'] = len(posts)
    new_posts_batched = list(divide_into_batches(posts, CPB_BATCH_SIZE))
    
    post_count = 0
    for post_batch in new_posts_batched:
        if (post_count + CPB_BATCH_SIZE) < channel_posts_batch_array['m']['pt']:
            post_count += CPB_BATCH_SIZE
        else:
            post_count = channel_posts_batch_array['m']['pt']

        channel_posts_batch_array['p'] = post_batch
        channel_posts_batch_array['m']['pc'] = post_count
        wps_logger('POST BATCH HANDLER', callsign, f"Channel connect response batch: {channel_posts_batch_array}")
        CONN.send(frame_and_compress_json_object(channel_posts_batch_array).encode()) 

def close_connection(CONN_DB_CURSOR, callsign, CONN):
    ###
    # Called when a user disconnects or when the server disconnects a user, if an error is encountered
    # Updates the user record to say they're not online and wwith disconnected timestamp
    # Sends user disconnect to connected clients, to update the online list
    # Closes TCP connection which will return an AX:25 disconnect to the client
    ###

    wps_logger("DISCONNECT HANDLER", callsign, "Starting")
    print(callsign, 'disconnected', datetime.datetime.now().isoformat())
    disconnect_timestamp = round(time.time())

    user_updated_fields = { "last_disconnected": disconnect_timestamp, "is_online": 0 }

    user_db_record = dbUserUpdate(CONN_DB_CURSOR, callsign, user_updated_fields)
    wps_logger("DISCONNECT HANDLER", callsign, f"User update response: {user_db_record}")
    
    wps_logger("DISCONNECT HANDLER", callsign, "All connections BEFORE disconnect: ")
    for c in CONNECTIONS:
        wps_logger("DISCONNECT HANDLER", callsign, f"Connection: {c}")        

    for key, C in enumerate(CONNECTIONS):
        if C['callsign'] == callsign:
            # C['socket'].close()
            del CONNECTIONS[key]
    
    wps_logger("DISCONNECT HANDLER", callsign, "All connections AFTER disconnect: ")
    wps_logger('ONLINE STATUS', callsign, f"Disconnected, connection length is {len(CONNECTIONS)}. Updates sent to:")
    for C in CONNECTIONS:
        wps_logger("ONLINE STATUS", callsign, f"Connection: {C}")       
        
        disconnected_response = {
            "t": "ud",
            "c": callsign
        }
        wps_logger('ONLINE STATUS', callsign, f"Disconnect sent to {C['callsign']}")
        socket_send_handler(C, disconnected_response)
        
    # Output purely for the console
    rc = []
    for c in CONNECTIONS:
        rc.append(c['callsign'])
    print('Connections:', str(rc))
    
    try:
        CONN.close()
    except Exception as e:
        wps_logger("DSCONNECT HANDLER", callsign, f"Socket close exception {e} happened")

def service_monitor_handler():
    ###
    # Future service monitoring function, currently unused
    ###
    return { 'status': 1 }

def connected_session_handler(CONN, ADDR):
    """
    Continuously runs whilst there is an active TCP connection
    Runs in its own thread
    Listens for new data
    Recognises and handles compressed and plain text packets
    Buffers incomplete data 
    Validates integrity of the received JSON objects
    For each valid JSON object, calls the corrent handler function to process
    """

    def is_json(string):
        try:
            json.loads(string)
        except Exception as e:
            wps_logger("CONNECTION SESSION HANDLER", callsign, f"JSON conversation error: {e}")
            return False
        return True
    
    wps_logger("CONNECTION SESSION HANDLER", "-----", "Thread starting")
    wps_logger("CONNECTION SESSION HANDLER", "-----", str(CONN))
    
    # First Socket data is always the callsign
    callsign = CONN.recv(1024).decode()
    callsign = callsign.replace('\r\n', '').upper()
    wps_logger("CONNECTION SESSION HANDLER", callsign, f"First data received is: {callsign}")

    # Strip the alias, if there is one
    if callsign.find("-") != -1:
        callsign = callsign.split('-')
        callsign = callsign[0]
        wps_logger("CONNECTION SESSION HANDLER", callsign, f"Alias removed, callsign is now: {callsign}")

    # Basic callsign check - does it contain a number?
    if not any(char.isdigit() for char in callsign):
        wps_logger("CONNECTION SESSION HANDLER", callsign, "Callsign seems INVALID, DISCONNECTING")
        CONN.close()
        return

    wps_logger("CONNECTION_SESSION HANDLER", callsign, "Callsign seems valid, continuing")
    CONNECTIONS.append({ "callsign": callsign, "socket": CONN })
    CONN_DB_CURSOR = db.cursor()
    
    # Print the updated connected callsigns to the console
    rc = []
    for c in CONNECTIONS:
        rc.append(c['callsign'])
    print(f"Connections: {str(rc)}")

    # Log all active connections
    for c in CONNECTIONS:
        wps_logger("CONNECTION SESSION HANDLER", callsign, f"ACTIVE CONNECTION {c}")

    # Tell all the other connected users about the new connection
    for c in CONNECTIONS:
        wps_logger("CONNECTION SESSION HANDLER", callsign, f"PROCESSING CONNECTION {c['callsign']}")
        if c['callsign'] == callsign:
            continue

        wps_logger('ONLINE STATUS', callsign, f"Connect sent to {c['callsign']}")
        connected_response = { "t": "uc", "c": callsign }
        socket_send_handler(c, connected_response)

    # Create an empty buffer and start listening for the first data
    CONNECTION_RX_BUFFER = ''
    first_rx = True

    while True:
        # This loop runs forever unless the connection is closed or the code terminates on error
        try:
            if not CONN._closed:
                socket_rx = CONN.recv(1024)
            else:
                wps_logger("CONNECTION SESSION HANDLER", callsign, "Socket in closed state, ending thread")
                break
            
            wps_logger("CONNECTION SESSION HANDLER", callsign, f"Received: {repr(socket_rx)}") 
            socket_rx = socket_rx.decode()

            # If the first data is not the start of a JSON or Compressed object, this probably is a manual connect. Send invalid connect response and disconnect
            # Or, the first data is the node disconnecting (which is very rare but has happened), send a disconnect
            if first_rx:
                first_rx = False
                
                if socket_rx[:28] == '*** Disconnected from Stream':
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "First data is a node disconnect")
                    close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break
                
                if socket_rx[:1] != '{' and socket_rx[:1] != chr(195):
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "First RX not JSON or a Compressed Packet, disconnecting", 'ERROR') 
                    CONN.send((invalid_connect_reponse+'\r').encode())
                    time.sleep(2)
                    close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break
            
            wps_logger("CONNECTION SESSION HANDLER", callsign, f"RX Buffer is: {repr(CONNECTION_RX_BUFFER)}") 

            if len(socket_rx) == 0:
                wps_logger("CONNECTION SESSION HANDLER", callsign, "Received empty string, assumed lost connection. Disconnecting")
                close_connection(CONN_DB_CURSOR, callsign, CONN)
                break
            
            CONNECTION_RX_BUFFER = CONNECTION_RX_BUFFER + socket_rx
            wps_logger("CONNECTION SESSION HANDLER", callsign, f"After appending new RX to RX Buffer, it is now {repr(CONNECTION_RX_BUFFER)}")

            contains_eol = CONNECTION_RX_BUFFER.find('\r\n')
            wps_logger("CONNECTION SESSION HANDLER", callsign, f"Contains EOL: {contains_eol}")

            # Split on the /r/n and process
            messages_to_process = CONNECTION_RX_BUFFER.split('\r\n')
            wps_logger("CONNECTION SESSION HANDLER", callsign, f"Buffer after splitting is: {messages_to_process}")

            while len(messages_to_process) > 0:
                
                wps_logger("CONNECTION SESSION HANDLER", callsign, f"Array to process is: {messages_to_process}")
                message = messages_to_process.pop(0)
                wps_logger("CONNECTION SESSION HANDLER", callsign, f"Next element to process is: {message}")

                if len(message) == 0 and len(messages_to_process) != 0:
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Zero length data to process and not last message in the array, something is wrong. Terminating connection", "ERROR")
                    close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break

                # if a full line terminated with \r\n, the last index will be an empty string
                if len(message) == 0:
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Empty string, clearing RX Buffer. Must have processed entire contents of RX Buffer")
                    CONNECTION_RX_BUFFER = ''
                    continue
                
                # Handle node disconnect
                if message[:16] == '*** Disconnected':
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Received node disconnect, exiting")
                    close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break

                # Check for compression delimiters at the start of the message but not at the end, or, compression delimiters at the end of the message but not at the start
                # If true, this is a part of a compressed message that hasn't yet been fully received
                if (message[:2] == compression_delimiter_base64 and message[-2:] != compression_delimiter_base64) or (message[:2] != compression_delimiter_base64 and message[-2:] == compression_delimiter_base64):
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Not complete compressed packet so must be waiting on more data, add to RX Buffer and return")
                    CONNECTION_RX_BUFFER = message
                    wps_logger("CONNECTION SESSION HANDLER", callsign, f"RX Buffer is {CONNECTION_RX_BUFFER}")
                    break

                # If compression delimiters start and finish, decompress before continuing
                if (message[:2] == compression_delimiter_base64 and message[-2:] == compression_delimiter_base64):
                    message = message[2:-2]
                    wps_logger("CONNECTION SESSION HANDLER", callsign, f"Decompressing {repr(message)}")
                    message_length_before = len(message)
                    message = decompress(message)
                    wps_logger("CONNECTION SESSION HANDLER", callsign, f"Decompressed message: {message}")
                    wps_logger("CONNECTION SESSION HANDLER", callsign, f"Message length was {message_length_before} and now is {len(message)}")

                # Check for curly braces at the start and end of the message. If not, must be part of an object. Add to buffer and return
                # Only applies to plain text messages part received. If compressed the preceding step would have decompressed a full JSON object
                if (message[:1] == '{' and message[-1:] != '}') or (message[:1] != '{' and message[-1:] == '}'):
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Not complete JSON so must be waiting on more data, add to RX Buffer and return")
                    CONNECTION_RX_BUFFER = message
                    wps_logger("CONNECTION SESSION HANDLER", callsign, f"RX Buffer is {CONNECTION_RX_BUFFER}")
                    break
                
                # TODO: Fix nested JSON edge case failure scenario, affecting non-critical packets (does not affect Messages or Posts)
                # There is only one known scenario where this code could fail::
                # a) The message is in the last element of the array
                # b) It is incomplete, i.e. is pending more data to be received
                # c) The JSON object has nested JSON
                # d) The leftmost character is { as expected
                # e) The rightmost character is } as expected, but this isn't the end of the main JSON object, it's a } in the nested JSON
                # This scenario would only occur if the Packet network split the data exactly on the nested JSON object with the last character being }

                # Convert to JSON. If fails, this is FATAL. Raise an ERROR and disconnect. 
                if is_json(message):
                    message_json = json.loads(message)
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Valid json, continuing")
                else:
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Received string is not valid JSON", "ERROR")
                    wps_logger("CONNECTION SESSION HANDLER", callsign, f"String attempting to convert {message}", "ERROR")
                    wps_logger("CONNECTION SESSION HANDLER", callsign, f"Full buffer {CONNECTION_RX_BUFFER}", "ERROR")
                    close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break
                
                # Now there's a JSON object, pass to the correct handler
                
                message_json["t"] == message_json["t"].lower()

                ### Message Types

                # Message
                if message_json["t"] == "m":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking send handler")
                    message_send_handler(CONN_DB_CURSOR, message_json, callsign, CONN)

                # Message Emoji
                if message_json["t"] == "mem":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking emoji handler")
                    message_emoji_handler(CONN_DB_CURSOR, message_json, callsign)   

                # Message Edit
                if message_json["t"] == "med":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking message edit handler")
                    message_edit_handler(CONN_DB_CURSOR, message_json, callsign, CONN)  

                ### Channel Types

                if message_json["t"] == "cp":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking channel post handler")
                    post_handler(CONN_DB_CURSOR, message_json, callsign, CONN)  

                # Channel Post Edit
                if message_json["t"] == "cped":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking post edit handler")
                    post_edit_handler(CONN_DB_CURSOR, message_json, callsign, CONN)   

                # Channel Post Emoji
                if message_json["t"] == "cpem":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking post emoji handler")
                    post_emoji_handler(CONN_DB_CURSOR, message_json, callsign, CONN)   

                # Channel Subscribe / Unsubscribe
                if message_json["t"] == "cs":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking subscription handler")
                    channel_subscribe_handler(CONN_DB_CURSOR, message_json, callsign, CONN)   

                # Channel Post Batch
                if message_json["t"] == "cpb":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking channel post batch handler")
                    post_batch_handler(CONN_DB_CURSOR, message_json, callsign, CONN)

                ### General Types

                # Connect
                if message_json["t"] == "c":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking connect handler")
                    connect_handler(CONN_DB_CURSOR, callsign, message_json, CONN)

                # Pairing
                if message_json["t"] == "p":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking pairing handler")
                    pairing_handler(CONN_DB_CURSOR, callsign, CONN)
                
                # User Enquiry
                if message_json["t"] == "ue":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking user enquiry handler")
                    user_enquiry_handler(CONN_DB_CURSOR, message_json, callsign, CONN)                 

                # Keep Alive (no subsequent processing, just logged and ignored)
                if message_json["t"] == "k":
                    wps_logger('SOCKET HANDLER', callsign, "Keep alive")

                # Ham Enquiry
                if message_json["t"] == "he":
                    wps_logger("CONNECTION SESSION HANDLER", callsign, "Invoking ham enquiry handler")
                    ham_enquiry_handler(CONN_DB_CURSOR, message_json, callsign, CONN)

        except Exception as e:
            wps_logger("CONNECTION SESSION HANDLER", callsign, f"Exception {e} happened. Disconnecting", "ERROR")
            close_connection(CONN_DB_CURSOR, callsign, CONN)
            wps_logger("CONNECTION SESSION HANDLER", callsign, "Thread ending")
            break

def socket_send_handler(conn, payload):
    wps_logger("SOCKET SEND HANDLER", conn['callsign'], f"Sending {payload}")
    wps_logger("SOCKET SEND HANDLER", conn['callsign'], f"to {conn}")
    try:
        conn['socket'].send((json.dumps(payload, separators=(',', ':'))+'\r').encode())
    except Exception as e:
        wps_logger("SOCKET SEND HANDLER", conn['callsign'], f"Error {e} when sending to {conn}", "ERROR")
        close_connection(CONN_DB_CURSOR, CONN_DB_CURSOR, conn['callsign'], conn)

def check_auto_subscriptions(cursor):
    try:
        default_subscriptions = env.get('autoSubscribeToChannelIds', [])
        
        select_query = f"""
        SELECT json_extract(user, '$.callsign') as callsign,
                json_extract(user, '$.channel_subscriptions') as channel_subscriptions
        FROM users
        """
        cursor.execute(select_query)
        
        for row in list(cursor):
            channel_subscriptions = [] if row[1] is None else json.loads(row[1])
            callsign = row[0]
            original_length = len(channel_subscriptions)

            for default_subscription in default_subscriptions:
                channel_subscriptions.append(default_subscription) if default_subscription not in channel_subscriptions else None
            
            if len(channel_subscriptions) > original_length:
                user_update_response = dbUserUpdate(cursor, callsign, { "channel_subscriptions": channel_subscriptions })
                if user_update_response['result'] == 'failure':
                    wps_logger("AUTO SUBSCRIBE HANDLER", row[0], f"Failed to update user subscriptions for {row[0]}", "ERROR")

    except Exception as e:
        wps_logger("AUTO SUBSCRIBE HANDLER", "-----", f"Error processing auto-subscription: {e}", "ERROR")
        return

def startup_and_listen():
    print('WPS Started')
    print(f"Using database {env['dbFilename']}")
    print(f"Listening on TCP Port {env['socketTcpPort']}")

    global_cursor = db.cursor()

    # Output the SQLite version to the console
    global_cursor.execute('''select sqlite_version()''')
    version = [i[0] for i in global_cursor]
    print("SQLite Version " + version[0])

    # Create the database tables, if they don't exist
    dbInit(global_cursor)

    # Confirm users are subscribed to the default channels
    check_auto_subscriptions(global_cursor)

    # Update all users as offline in the database
    online_users_response = dbGetOnlineUsers(global_cursor)
    if online_users_response['result'] == 'failure':
        wps_logger("HANDLER", "-----", "Failed to get online users, something is wrong, exiting")
        print("Failed to get online users, something is wrong, exiting")
        return
    
    online_users = online_users_response['data']
    for online_user in online_users:
        dbUserUpdate(global_cursor, online_user['callsign'], { "is_online": 0 })
    
    try:
        while True:
            wps_logger("CONNECTION HANDLER", "-----", "Wating for next connection ..")
            CONN, ADDR = S.accept()

            wps_logger("CONNECTION HANDLER", "-----", f"{ADDR} Connected")
            
            t = threading.Thread(name='connected_session_handler_thread', target=connected_session_handler, args=(CONN, ADDR))
            t.start()

            ALL_THREADS.append(t)
            
    except KeyboardInterrupt:
        wps_logger("CONNECTION HANDLER", "-----", "Stopped by Ctrl+C")
        print("\nStopped by Ctrl+C")
        if S:
            wps_logger("CONNECTION HANDLER", "-----", "Closing TCP socket listener")
            print("Closing TCP socket listener")
            S.close()
        for C in CONNECTIONS:
            wps_logger("CONNECTION HANDLER", "-----", f"Closing connection for {C['callsign']}")
            print("Closing connection for " + C['callsign'])
            close_connection(CONN_DB_CURSOR, CONN_DB_CURSOR, C['callsign'], C['socket'])
            time.sleep(2)

        for t in ALL_THREADS:
            t.join()

        wps_logger("CONNECTION HANDLER", "-----", "WPS Exited")
        print("WPS Exited")
        return

if __name__ == "__main__":
    startup_and_listen()