from env import *
import datetime

# Logging
MIN_LOG_LEVEL = env['minWpsLogLevel']
WPS_LOGFILE = open("wps.log", "a")

def wps_logger(function_name, callsign, log, log_entry_level = 'INFO'):
    if MIN_LOG_LEVEL == 'ERROR' and log_entry_level == 'INFO':
        return
    WPS_LOGFILE.write(f"{datetime.datetime.now().isoformat()} {log_entry_level} {callsign} {function_name} {str(log)}\n") 
    WPS_LOGFILE.flush()

