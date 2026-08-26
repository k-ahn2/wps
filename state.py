import threading
import datetime
import time

# Shared, process-lifetime state used by both the TCP layer (wps.py) and the warm-reloadable
# processing layer (handlers.py). This module is never reloaded via importlib.reload, so
# nothing that lives here is lost when handlers.py is warm-reloaded.

def timestamp():
    return datetime.datetime.now().isoformat(timespec='seconds')

def timestamp_milliseconds():
    return round(time.time() * 1000)

# Threads spawned per accepted TCP connection
ALL_THREADS = []

# Global TCP Connections Array
CONNECTIONS = []
CONNECTIONS_LOCK = threading.Lock()

def connections_snapshot():
    '''
    Returns a shallow copy of CONNECTIONS, safe to iterate without
    racing concurrent appends/removals from other threads.
    '''
    with CONNECTIONS_LOCK:
        return list(CONNECTIONS)

# Bot modules keyed by channel id (int), populated at startup
BOTS = {}

# In-memory cache of the entire contents of channels.json plus the timestamp it was last
# changed, shared by all connections. Populated at startup and refreshed on every new connect
# via sync_channels_from_file, so all connections see updates without a DB round trip.
CHANNELS_CACHE = {"channels": {"cg": [], "c": []}, "ts": 0}
CHANNELS_CACHE_LOCK = threading.Lock()

# String to return when someone manually connects and sends unknown text
invalid_connect_reponse = """Welcome to WPS\r
I didn't recognise that command and guess you have connected manually.\r
To use this service you need to connect using the WhatsPac Client - head to http://whatspac.oarc.uk and follow the instructions there.\r
You'll now be disconnected, thanks!\r
"""

# Compression delimiter as received from the client
# che(192) is sent, split into two bytes by the encoding and received as chr(195) and chr(128)
compression_delimiter_base64 = chr(195) + chr(128)

# Batch Sizes
MB_BATCH_SIZE = 4 # Number of messages to send in batch type 'mb'
CPB_BATCH_SIZE = 4 # Number of posts to send in batch type 'pb'
