from env import *
from state import syslog_log
import logging
from logging.handlers import TimedRotatingFileHandler

def apply_log_settings():
    '''
    Applies the log levels and retention from env to loggers already set up, so a warm reload
    after an env.json edit changes them without a restart. wpsLoggingEnabled/dbLoggingEnabled
    need nothing here: they are read from env on every log call.
    '''
    for name, level_key in (("wps", 'minWpsLogLevel'), ("db", 'minDbLogLevel')):
        logger = logging.getLogger(name)
        if not logger.handlers:
            continue  # not created yet - picks the settings up when it is
        logger.setLevel(getattr(logging, env.get(level_key, 'INFO').upper(), logging.INFO))
        for handler in logger.handlers:
            if isinstance(handler, TimedRotatingFileHandler):
                handler.backupCount = env['daysToRetainLogFiles']

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

# Errors are always mirrored to syslog, even when file logging is disabled, so they are visible
# when running headless
SYSLOG_LEVELS = ("ERROR", "CRITICAL")

def wps_logger(function_name, callsign, log, log_entry_level="INFO"):

    if log_entry_level.upper() in SYSLOG_LEVELS:
        syslog_log(f"{callsign} {function_name} {log}", log_entry_level)

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

    if log_entry_level.upper() in SYSLOG_LEVELS:
        syslog_log(f"DB {function_name} {log}", log_entry_level)

    if not env.get('dbLoggingEnabled', True):
        return

    logger = get_db_logger()

    extra = {
        "function": function_name
    }

    level = getattr(logging, log_entry_level.upper(), logging.INFO)
    logger.log(level, log, extra=extra)
