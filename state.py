import threading
import datetime
import time
import json

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

# Fallback list of compatible WPS clients advertised to users who connect manually,
# used only if env.json has no (valid) wpsClients array.
WPS_CLIENTS = [
    "Frames: http://frames.oarc.uk",
    "WhatsPyc: [Link]",
    "Pacord: [Link]",
]

def get_wps_clients():
    '''
    Read the wpsClients array from env.json on every call so the list can be
    maintained without restarting the service. Falls back to WPS_CLIENTS if the
    file is missing/unreadable or the key is absent or not a non-empty list.
    '''
    try:
        with open("env.json", "r") as f:
            clients = json.load(f).get("wpsClients")
        if isinstance(clients, list) and clients:
            return [str(c) for c in clients]
    except (OSError, ValueError):
        pass
    return WPS_CLIENTS

def build_invalid_connect_response(clients=None):
    '''
    String to return when someone manually connects and sends unknown text.
    Each line is CR-terminated (never LF) or the BPQ node drops it.
    '''
    if clients is None:
        clients = get_wps_clients()
    lines = [
        "Welcome to WPS",
        "I didn't recognise that command and guess you have connected manually.",
        "",
        "To use this service you need to connect using a compatible WPS client - checkout:",
        *(f"- {client}" for client in clients),
        "",
        "You'll now be disconnected, thanks!",
    ]
    return "".join(f"{line}\r" for line in lines)

# Compression delimiter as received from the client
# che(192) is sent, split into two bytes by the encoding and received as chr(195) and chr(128)
compression_delimiter_base64 = chr(195) + chr(128)

# Batch Sizes
MB_BATCH_SIZE = 4 # Number of messages to send in batch type 'mb'
CPB_BATCH_SIZE = 4 # Number of posts to send in batch type 'pb'
