import json
import os
import subprocess
import time

# wps_status.py: the status file through which a running WPS tells the web dashboard - a separate
# process, see dashboard.py - about itself. Standard library only.
#
# WPS (wps.py) writes wps.status.json when it starts and after every warm reload:
#   pid          its process id, so the dashboard can send it SIGHUP to warm-reload
#   started_at   epoch ms; a new value tells the dashboard WPS has restarted
#   startup_env  env.json as WPS started with it, so the dashboard can tell which saved settings
#                aren't in effect yet (env.STARTUP_ENV in the WPS process)
#   last_reload  {"at": epoch ms, "errors": [...]} for the latest warm reload, however triggered
#
# It holds a copy of env.json's secrets, so it's written readable by its owner only, like env.json
# should be.

STATUS_PATH = os.path.abspath("wps.status.json")


def now_ms():
    return round(time.time() * 1000)


def write(status):
    tmp = STATUS_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(status, f)
    os.replace(tmp, STATUS_PATH)


def read():
    try:
        with open(STATUS_PATH) as f:
            status = json.load(f)
        return status if isinstance(status, dict) else None
    except (OSError, ValueError):
        return None


def is_wps_process(pid):
    '''True if pid is a running wps.py - not just any process that has since reused the pid.'''
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.path.isdir("/proc/self"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            return False
    else:  # no /proc, e.g. macOS
        try:
            cmdline = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return False
    return "wps.py" in cmdline


def running():
    '''The status of the WPS that wrote the file, if it is still running; otherwise None.'''
    status = read()
    return status if status and is_wps_process(status.get("pid")) else None
