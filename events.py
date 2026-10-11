from env import *
import sqlite3
from state import console_log

# Environment Variables. `env` is env.py's shared dict, updated in place by a warm reload, so
# enableWpsEvents applies live (wps.py's reload_code calls events_db_init again to create the
# table if it was just turned on). The database file is fixed at startup.
EVENTS_DB_FILENAME = STARTUP_ENV['events']['eventsDbFilename']

def events_db_init():
    
    if not env['events']['enableWpsEvents']:
        return

    # Initialize the events database and create the events table if it doesn't exist
    with sqlite3.connect(EVENTS_DB_FILENAME) as conn:
        
        cursor = conn.cursor()

        create_events_table = '''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT
        );
        '''
        cursor.execute(create_events_table)
        conn.commit()

def event_logger(timestamp, event_type, callsign, event=None, meta=None):
    
    if not env['events']['enableWpsEvents']:
        return None

    event_to_insert = {
        "ts": timestamp,
        "et": event_type,
        "c": callsign
    }

    if event is not None:
        event_to_insert['e'] = event
    
    if meta is not None:
        event_to_insert['m'] = meta

    with sqlite3.connect(EVENTS_DB_FILENAME) as conn:
        cursor = conn.cursor()
        
        try:
            insert_query = "INSERT INTO events (event) VALUES (?)"

            cursor.execute(insert_query, [json.dumps(event_to_insert, separators=(',', ':'))])
            conn.commit()
            return

        except Exception as e:
            console_log(f"Error logging event: {e}", "ERROR")
    
    return None

events_db_init()