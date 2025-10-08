from env import *
import sqlite3

EVENTS_DB_FILENAME = env['events']['eventsDbFilename']

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
            insert_query = f"""
            INSERT INTO events (event) 
            VALUES ('{json.dumps(event_to_insert, separators=(',', ':'))}')
            """
            
            cursor.execute(insert_query)
            conn.commit()
            return

        except Exception as e:
            print(f"Error logging event: {e}")
    
    return None

events_db_init()