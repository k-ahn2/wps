from env import *
import datetime

# Plan to incrementally migrate functions from wps.py to handlers.py, to enable warm re-loading of code without disconnecting TCP connections

# Logging
MIN_LOG_LEVEL = env['minWpsLogLevel']
WPS_LOGFILE = open("wps.log", "a")

def wps_logger(function_name, callsign, log, log_entry_level = 'INFO'):
    if MIN_LOG_LEVEL == 'ERROR' and log_entry_level == 'INFO':
        return
    WPS_LOGFILE.write(f"{datetime.datetime.now().isoformat()} {log_entry_level} {callsign} {function_name} {str(log)}\n") 
    WPS_LOGFILE.flush()

