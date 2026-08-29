from env import *
from logger import *
from state import *
import db
import handlers
import threading
import socket
import json
import time
import importlib
import os
import sys

# wps.py is the TCP layer: it owns the listening socket and every open connection's raw
# recv/buffer/framing loop. It never contains message-processing/business logic itself - that
# all lives in handlers.py, and every database interaction lives in db.py. Both are called only
# via `handlers.<func>(...)` / `db.<func>(...)` (module-attribute lookup at call time, never
# `from handlers import *` / `from db import *`) so that a warm reload of either
# (importlib.reload, triggered by pressing 'r' - see code_reload_key_listener below) swaps in
# new code for every open connection without ever touching this file's socket, accept loop, or
# per-connection threads. Shared state that must survive that reload (CONNECTIONS, BOTS,
# CHANNELS_CACHE, ...) lives in state.py, imported by both this module and handlers.py.

print(f"{timestamp()} ### WPS Starting ###")

# Environment Variables
env_source = open("env.json", "r")
env = json.load(env_source)
env_source.close()

print(f"{timestamp()} WPS Event Logging: {'Enabled' if env.get('events', {}).get('enableWpsEvents', False) else 'Disabled'}")
print(f"{timestamp()} BPQ Queue Monitoring: {'Enabled' if env.get('events', {}).get('enableBpqEvents', False) else 'Disabled'}")
print(f"{timestamp()} Bots: {'Enabled' if env.get('botsEnabled', False) else 'Disabled'}")

# TCP Socket Setup
HOST = '0.0.0.0'
PORT = env['socketTcpPort']
S = socket.socket()
S.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
S.bind((HOST, PORT))
S.listen()

def reload_handlers():
    '''
    Warm-reloads the handlers module - all message processing/business logic - in place via
    importlib.reload, without restarting the process or dropping any TCP connection.
    connected_session_handler only ever calls into handlers.<func>(...), looking the function up
    on the module at call time, so reload() rewriting handlers.__dict__ in place is picked up by
    the very next message on every open connection. Shared state (CONNECTIONS, BOTS,
    CHANNELS_CACHE, ...) lives in state.py, not handlers.py, so none of it is lost.
    '''
    try:
        importlib.reload(handlers)
        print(f"{timestamp()} Reloaded handlers module (processing logic)")
    except Exception as reload_e:
        print(f"{timestamp()} ERROR: failed to reload handlers module: {reload_e}")

def reload_db():
    '''
    Warm-reloads the db module - every database interaction - in place via importlib.reload,
    without restarting the process or dropping any TCP connection. handlers.py only ever calls
    into db.<func>(...), looking the function up on the module at call time, so reload()
    rewriting db.__dict__ in place is picked up by the very next call. get_db_connection() opens
    a fresh sqlite3 connection per call rather than caching one at module level, so there's no
    stale connection to worry about across a reload.
    '''
    try:
        importlib.reload(db)
        print(f"{timestamp()} Reloaded db module (database logic)")
    except Exception as reload_e:
        print(f"{timestamp()} ERROR: failed to reload db module: {reload_e}")

def reload_bots():
    '''
    Warm-reloads every loaded bot module's code in place via importlib.reload, without
    restarting the process or dropping connections. Each bot's tick thread looks up
    functions like tick()/handle_command() by name in the module's own __dict__ at call
    time, and reload() rewrites that same dict in place, so the tick thread and the command
    dispatch in channels_post_handler both pick up the new code immediately - no need to
    re-run init() or restart the thread. Bot state lives in the DB, not the module, so
    nothing is lost.
    '''
    if not BOTS:
        return

    for cid, mod in BOTS.items():
        try:
            importlib.reload(mod)
            print(f"{timestamp()} Reloaded bot module '{mod.__name__}' (channel {cid})")
        except Exception as reload_e:
            print(f"{timestamp()} ERROR: failed to reload bot module '{mod.__name__}': {reload_e}")

def reload_code():
    '''
    Warm-reloads all reloadable code - db.py, handlers.py, and any loaded bot modules - in one
    go. db.py is reloaded first since handlers.py (and bots, indirectly) depend on it.
    '''
    reload_db()
    reload_handlers()
    reload_bots()

def code_reload_key_listener():
    '''
    Background thread: watches the terminal for the 'r' key (no Enter needed) and warm-
    reloads db.py, handlers.py and all bot modules via reload_code() when pressed. Only
    meaningful when stdin is an interactive TTY - callers should check sys.stdin.isatty()
    before starting this thread.
    '''
    import select, termios, tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 1)
            if ready and sys.stdin.read(1).lower() == 'r':
                reload_code()
    except Exception as exc:
        print(f"{timestamp()} Code reload key listener stopped: {exc}")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def load_bots_config():
    '''
    Reads bots/bots.json - keyed by bot name (the Python module under the bots/ package to
    import), with an object holding the channel id (cid) the bot operates on plus any
    bot-specific config as the value. Returns {} if bots/bots.json doesn't exist, since bots
    are optional.
    '''

    try:
        with open(os.path.join("bots", "bots.json")) as bots_source:
            return json.load(bots_source)
    except FileNotFoundError:
        return {}

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
            wps_logger("CONNECTED SESSION HANDLER", callsign, f"JSON conversation error: {e}")
            return False
        return True
    
    wps_logger("CONNECTED SESSION HANDLER", "-----", "Thread starting")
    wps_logger("CONNECTED SESSION HANDLER", "-----", str(CONN))
    
    # First Socket data is always the callsign
    callsign = CONN.recv(1024).decode()
    callsign = callsign.replace('\r\n', '').upper()
    wps_logger("CONNECTED SESSION HANDLER", callsign, f"First data received is: {callsign}")

    # Strip the alias, if there is one
    if callsign.find("-") != -1:
        callsign = callsign.split('-')
        callsign = callsign[0]
        wps_logger("CONNECTED SESSION HANDLER", callsign, f"Alias removed, callsign is now: {callsign}")

    # Basic callsign check - does it contain a number?
    if not any(char.isdigit() for char in callsign):
        wps_logger("CONNECTED SESSION HANDLER", callsign, "Callsign seems INVALID, DISCONNECTING")
        CONN.shutdown(socket.SHUT_RDWR)
        return

    wps_logger("CONNECTED SESSION HANDLER", callsign, "Callsign seems valid, continuing")

    CONN_DB_CURSOR = db.get_db_connection().cursor()

    # Check if the callsign is already connected, if so silently remove existing connections
    # without triggering the disconnect handler or broadcasting a disconnect notification
    # Find and remove under the lock so this can't race a concurrent append/removal
    # from another thread's connect/disconnect/reconnect handling.
    with CONNECTIONS_LOCK:
        existing_connections = [C for C in CONNECTIONS if C['callsign'] == callsign]
        for C in existing_connections:
            # Remove from CONNECTIONS first so that when the old thread's exception handler fires,
            # close_connection will not find this callsign and will not send a disconnect notification
            CONNECTIONS[:] = [conn for conn in CONNECTIONS if conn is not C]

        # Now continue and add the new connection
        CONNECTIONS.append({ "callsign": callsign, "socket": CONN })

    for C in existing_connections:
        wps_logger("CONNECTED SESSION HANDLER", callsign, "Callsign already connected, silently removing existing connection")
        print(f"{timestamp()} {callsign} reconnected, silently removing existing connection")
        try:
            C['socket'].shutdown(socket.SHUT_RDWR)
            C['socket'].close()
        except Exception as e:
            wps_logger("CONNECTED SESSION HANDLER", callsign, f"Exception closing existing connection socket: {e}")

    # Print the updated connected callsigns to the console
    rc = []
    for c in connections_snapshot():
        rc.append(c['callsign'])
    print(f"{timestamp()} Connections After Connect: {str(rc)}")
    
    # Create an empty buffer and start listening for the first data
    CONNECTION_RX_BUFFER = ''
    first_rx = True

    while True:
        # This loop runs forever unless the connection is closed or the code terminates on error
        try:
            if not CONN._closed:
                socket_rx = CONN.recv(1024)
            else:
                wps_logger("CONNECTED SESSION HANDLER", callsign, "Socket in closed state, ending thread")
                break
            
            wps_logger("CONNECTED SESSION HANDLER", callsign, f"Received: {repr(socket_rx)}") 
            socket_rx = socket_rx.decode()

            # If the first data is not the start of a JSON or Compressed object, this probably is a manual connect. Send invalid connect response and disconnect
            # Or, the first data is the node disconnecting (which is very rare but has happened), send a disconnect
            if first_rx:
                first_rx = False
                
                if socket_rx[:28] == '*** Disconnected from Stream':
                    wps_logger("CONNECTED SESSION HANDLER", callsign, "First data is a node disconnect")
                    handlers.close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break
                
                if socket_rx[:15] == 'SERVICE_MONITOR':
                    CONN.send(("WPS_IS_ALIVE"+'\r').encode())
                    time.sleep(10)
                    try:
                        CONN.shutdown(socket.SHUT_RDWR)
                        CONN.close()
                    except Exception as e:
                        wps_logger("DISCONNECT HANDLER", callsign, f"Socket shutdown exception {e} happened")
                    break

                if socket_rx[:1] != '{' and socket_rx[:1] != chr(195):
                    wps_logger("CONNECTED SESSION HANDLER", callsign, "First RX not JSON or a Compressed Packet, disconnecting", 'ERROR') 
                    CONN.sendall((build_invalid_connect_response()+'\r').encode())
                    time.sleep(10)
                    handlers.close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break
            
            wps_logger("CONNECTED SESSION HANDLER", callsign, f"CONNECTION_RX_BUFFER is: {repr(CONNECTION_RX_BUFFER)}") 

            if len(socket_rx) == 0:
                wps_logger("CONNECTED SESSION HANDLER", callsign, "Received empty string, assumed lost connection. Disconnecting")
                handlers.close_connection(CONN_DB_CURSOR, callsign, CONN)
                break
            
            CONNECTION_RX_BUFFER = CONNECTION_RX_BUFFER + socket_rx
            wps_logger("CONNECTED SESSION HANDLER", callsign, f"After appending new RX to CONNECTION_RX_BUFFER, it is now {repr(CONNECTION_RX_BUFFER)}")

            # Check if the last characters in the buffer are \r\n, if not, wait for more data
            buffer_has_complete_data = False
            if CONNECTION_RX_BUFFER[-2:] == '\r\n':
                buffer_has_complete_data = True
                wps_logger("CONNECTED SESSION HANDLER", callsign, "CONNECTION_RX_BUFFER ends with \\r\\n, has complete data to process")

            # Split on the /r/n and process
            messages_to_process = CONNECTION_RX_BUFFER.split('\r\n')
            wps_logger("CONNECTED SESSION HANDLER", callsign, f"CONNECTION_RX_BUFFER after splitting is: {messages_to_process}")

            # Check if the last element is empty string, meaning the buffer ended with \r\n and there is no partial data left
            # If so, clear the RX Buffer as all data has been processed
            if messages_to_process[-1] == '' and buffer_has_complete_data:
                wps_logger("CONNECTED SESSION HANDLER", callsign, "Removing last element from array as it is empty string and buffer has complete data, clearing CONNECTION_RX_BUFFER")
                del messages_to_process[-1]
                CONNECTION_RX_BUFFER = ''
            else:
                # Last element is not empty string and buffer does not have complete data, meaning there is partial data left
                # Keep this in the RX Buffer after processing all other data
                wps_logger("CONNECTED SESSION HANDLER", callsign, "Last element is NOT empty string, must be part of the next packet. Removing from array and updating CONNECTION_RX_BUFFER")
                CONNECTION_RX_BUFFER = messages_to_process.pop(-1)
                wps_logger("CONNECTED SESSION HANDLER", callsign, f"CONNECTION_RX_BUFFER is now: '{CONNECTION_RX_BUFFER}'")

            if len(messages_to_process) > 0:
                wps_logger("CONNECTED SESSION HANDLER", callsign, f"Now, array should have {len(messages_to_process)} complete packets to process")
            
            # Process each element in the array - which must be complete packets, either compressed or plain text JSON
            while len(messages_to_process) > 0:
                
                wps_logger("CONNECTED SESSION HANDLER", callsign, f"Array to process is: {messages_to_process}")
                message = messages_to_process.pop(0)
                wps_logger("CONNECTED SESSION HANDLER", callsign, f"Next element to process is: {message}")

                if len(message) == 0 and len(messages_to_process) != 0:
                    wps_logger("CONNECTED SESSION HANDLER", callsign, "Zero length data to process and not last message in the array, something is wrong. Terminating connection", "ERROR")
                    handlers.close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break

                # if a full line terminated with \r\n, the last index will be an empty string
                if len(message) == 0:
                    wps_logger("CONNECTED SESSION HANDLER", callsign, "Empty string, clearing RX Buffer. Must have processed entire contents of RX Buffer")
                    CONNECTION_RX_BUFFER = ''
                    continue
                
                # Handle node disconnect
                if message[:16] == '*** Disconnected':
                    wps_logger("CONNECTED SESSION HANDLER", callsign, "Received node disconnect, exiting")
                    handlers.close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break


                # If compression delimiters start and finish, decompress before continuing
                if (message[:2] == compression_delimiter_base64 and message[-2:] == compression_delimiter_base64):
                    message = message[2:-2]
                    wps_logger("CONNECTED SESSION HANDLER", callsign, f"Decompressing {repr(message)}")
                    message_length_before = len(message)
                    message = handlers.decompress(message)
                    wps_logger("CONNECTED SESSION HANDLER", callsign, f"Decompressed message: {message}")
                    wps_logger("CONNECTED SESSION HANDLER", callsign, f"Message length was {message_length_before} and now is {len(message)}")

                # Convert to JSON
                # Processing assumes valid JSON. If this fails, it means a corrupt message and is FATAL. Raise an ERROR and disconnect. 
                if is_json(message):
                    message_json = json.loads(message)
                    wps_logger("CONNECTED SESSION HANDLER", callsign, "Valid json, continuing")
                else:
                    wps_logger("CONNECTED SESSION HANDLER", callsign, "Received string is not valid JSON", "ERROR")
                    wps_logger("CONNECTED SESSION HANDLER", callsign, f"String attempting to convert '{message}'", "ERROR")
                    wps_logger("CONNECTED SESSION HANDLER", callsign, f"Full buffer '{CONNECTION_RX_BUFFER}'", "ERROR")
                    handlers.close_connection(CONN_DB_CURSOR, callsign, CONN)
                    break
                
                # Now there's a JSON object, pass to the correct handler
                handlers.process_message(CONN_DB_CURSOR, message_json, callsign, CONN)

        except Exception as e:
            wps_logger("CONNECTED SESSION HANDLER", callsign, f"Exception {e} happened. Disconnecting", "ERROR")
            handlers.close_connection(CONN_DB_CURSOR, callsign, CONN)
            wps_logger("CONNECTED SESSION HANDLER", callsign, "Thread ending")
            break

def startup_and_listen():
    print(f"{timestamp()} Using database {env['dbFilename']}")
    print(f"{timestamp()} Listening on TCP Port {env['socketTcpPort']}")

    global_cursor = db.get_db_connection().cursor()

    # Output the SQLite version to the console
    global_cursor.execute('''select sqlite_version()''')
    version = [i[0] for i in global_cursor]
    print(f"{timestamp()} SQLite Version " + version[0])
    print(f"{timestamp()} ### WPS Started ###")

    # Create the database tables, if they don't exist
    db.dbInit(global_cursor)

    # Load the channel list from channels.json into the database
    handlers.sync_channels_from_file(global_cursor)

    # Load bots from bots/bots.json - keyed by bot name, the Python module under the bots/
    # package to import, with an object holding the channel id (cid) the bot operates on plus
    # any bot-specific config as the value. bots/bots.json is the master: every key must have a
    # matching bots/<name>.py module and a channels.json channel with that cid flagged "b": true.
    # Only loaded and processed at all if botsEnabled is set in env.json.
    # BOTS lives in state.py (shared with handlers.py), so clear it in place rather than
    # rebinding the name - handlers.py holds its own reference to the same dict.
    BOTS.clear()

    if env.get('botsEnabled', False):
        bots_config = load_bots_config()

        for bot_name, bot_config in bots_config.items():
            try:
                cid = bot_config['cid']

                channel = handlers.get_channel(cid)
                if channel is None:
                    raise Exception(f"no channel with cid {cid} found in channels.json")
                if not channel.get('b'):
                    raise Exception(f"channel {cid} ('{channel.get('cn')}') is missing \"b\": true in channels.json")

                if not os.path.isfile(os.path.join("bots", f"{bot_name}.py")):
                    raise Exception(f"bots/{bot_name}.py not found")

                mod = importlib.import_module(f"bots.{bot_name}")
                mod.init(db.get_db_connection())
                mod.start_tick_thread(
                    db.get_db_connection(),
                    lambda cursor, c, text, fc: handlers.bot_broadcast_to_channel(cursor, c, text, fc),
                    cid,
                )
                BOTS[cid] = mod
                print(f"{timestamp()} Bot '{bot_name}' enabled on channel {cid}")
            except Exception as bot_init_e:
                print(f"{timestamp()} ERROR: failed to load bot '{bot_name}': {bot_init_e}")

    if sys.stdin.isatty():
        threading.Thread(target=code_reload_key_listener, daemon=True, name='code_reload_key_listener').start()
        print(f"{timestamp()} Press 'r' in this terminal to warm-reload db and processing code{' and bots' if BOTS else ''} without disconnecting users")

    # Confirm users are subscribed to the default channels
    handlers.check_auto_subscriptions(global_cursor)

    # Update all users as offline in the database
    online_users_response = db.dbGetOnlineUsers(global_cursor)
    if online_users_response['result'] == 'failure':
        wps_logger("HANDLER", "-----", "Failed to get online users, something is wrong, exiting")
        print(f"{timestamp()} Failed to get online users, something is wrong, exiting")
        return

    online_users = online_users_response['data']
    for online_user in online_users:
        db.dbUserUpdate(global_cursor, online_user['callsign'], { "is_online": 0 })

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
        print(f"{timestamp()} Stopped by Ctrl+C, closing down WPS")

        if S:
            wps_logger("CONNECTION HANDLER", "-----", "Closing TCP socket listener")
            print(f"{timestamp()} Closing TCP socket listener")
            S.close()

        while (len(CONNECTIONS) > 0):
            wps_logger("CONNECTION HANDLER", "-----", f"Closing connection for {CONNECTIONS[0]['callsign']}")
            CONNECTIONS[0]['socket'].shutdown(socket.SHUT_RDWR)
            time.sleep(2)

        wps_logger("CONNECTION HANDLER", "-----", "WPS Exited")
        print(f"{timestamp()} WPS Exited")
        return

if __name__ == "__main__":
    startup_and_listen()
