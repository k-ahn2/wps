from env import *
import logging
from logging.handlers import TimedRotatingFileHandler

# Plan to incrementally migrate functions from wps.py to handlers.py, to enable warm re-loading of code without disconnecting TCP connections

def get_wps_logger():
    logger = logging.getLogger("wps")

    if logger.handlers:
        return logger

    numeric_level = getattr(logging, env.get('minWpsLogLevel', 'INFO').upper(), logging.INFO)
    logger.setLevel(numeric_level)

    handler = TimedRotatingFileHandler(
        "wps.log",
        when="midnight",
        interval=1,
        backupCount=env['daysToRetainLogFiles'],
        encoding="utf-8"
    )

    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(callsign)s %(function)s %(message)s"
    ))
    logger.addHandler(handler)

    return logger

def wps_logger(function_name, callsign, log, log_entry_level="INFO"):
    
    if not env.get('wpsLoggingEnabled', True):
        return
    
    logger = get_wps_logger()

    extra = {
        "callsign": callsign,
        "function": function_name
    }

    level = getattr(logging, log_entry_level.upper(), logging.INFO)
    logger.log(level, log, extra=extra)

def get_db_logger():
    logger = logging.getLogger("db")

    if logger.handlers:
        return logger

    numeric_level = getattr(logging, env.get('minDbLogLevel', 'INFO').upper(), logging.INFO)
    logger.setLevel(numeric_level)

    handler = TimedRotatingFileHandler(
        "db.log",
        when="midnight",
        interval=1,
        backupCount=env['daysToRetainLogFiles'],
        encoding="utf-8"
    )

    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(function)s %(message)s"
    ))
    logger.addHandler(handler)

    return logger

def db_logger(function_name, log, log_entry_level="INFO"):

    if not env.get('dbLoggingEnabled', True):
        return

    logger = get_db_logger()

    extra = {
        "function": function_name
    }

    level = getattr(logging, log_entry_level.upper(), logging.INFO)
    logger.log(level, log, extra=extra)