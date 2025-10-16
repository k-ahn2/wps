import requests, time, datetime
from events import *
from env import *

APPL_NAME = env['events']['bpqApplName']
CALLSIGNS_WITH_ACTIVE_QUEUES = []
POLLING_INTERVAL_SECONDS = 5

print(f"Starting BPQ queue monitor for application '{APPL_NAME}' with polling interval of {POLLING_INTERVAL_SECONDS} seconds.")
print(f"BPQ Queue API URL: {env['events']['bpqQueueApiUrl']}")
print(f"Using database file: {EVENTS_DB_FILENAME}")

def bpq_queue_monitor():
        
    try:
        response = requests.get(env['events']['bpqQueueApiUrl'], timeout=3)

        if response.status_code != 200:
            raise Exception(f"Unexpected status code: {response.status_code}")
        
        queue_data = response.json()
        
        timestamp = round(time.time())

        for queue in queue_data['QState']:
            
            if queue['APPL'] != APPL_NAME:
                continue
            
            event_to_log = {
                "tcpqueue": queue.get('tcpqueue', None),
                "packets": queue.get('packets', None),
                "port": queue.get('port', None),
                "type": queue.get('type', None)
            }

            # Log event if there is activity in the queue
            if (queue['tcpqueue'] > 0 or queue['packets'] > 0):
                print(f"{datetime.datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')} Queue detected for {queue['callSign']} on port {queue['port']} via {queue['type']}: TCP Queue={queue['tcpqueue']}, Packets={queue['packets']}")
                CALLSIGNS_WITH_ACTIVE_QUEUES.append(queue['callSign']) if queue['callSign'] not in CALLSIGNS_WITH_ACTIVE_QUEUES else None
                event_logger(timestamp=round(time.time()*1000), event_type="BPQ_QUEUE", callsign=queue['callSign'], event=event_to_log)
            # Log final event for a previously active queue that is now cleared
            elif queue['callSign'] in CALLSIGNS_WITH_ACTIVE_QUEUES and queue['tcpqueue'] == 0 and queue['packets'] == 0:
                print(f"{datetime.datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')} Queue cleared for {queue['callSign']}")
                CALLSIGNS_WITH_ACTIVE_QUEUES.remove(queue['callSign'])
                event_logger(timestamp=round(time.time()*1000), event_type="BPQ_QUEUE_CLEARED", callsign=queue['callSign'], event=event_to_log)

    except Exception as e:
        print(f"Error monitoring BPQ queue: {e}") 

if env['events']['enableBpqEvents']:
    while True:
        bpq_queue_monitor()
        time.sleep(POLLING_INTERVAL_SECONDS)