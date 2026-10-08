#!/usr/bin/env bash
#
# Installs WPS as a systemd service, so it starts at boot and restarts if it stops.
# Writes the same unit file as docs/installation/INSTALLATION.md#running-wps-as-a-service,
# filled in for the user, directory and Python of this install.
#
#   sudo ./install_service.sh                 install (or update) and start the service
#   sudo ./install_service.sh --uninstall     stop, disable and remove the service
#   ./install_service.sh --dry-run            print the unit file without installing it
#
# Run ./install_service.sh --help for all options.

set -euo pipefail

SERVICE_NAME="wps"
WPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
USER_GIVEN=0
PYTHON=""
ACTION="install"
START=1
ORIG_ARGS="$*"

usage() {
    cat <<EOF
Usage: sudo $0 [options]

Installs WPS from $WPS_DIR as the systemd service '$SERVICE_NAME'.

Options:
  --user USER       User the service runs as (default: the user who ran sudo, here '$RUN_USER')
  --python PATH     Python interpreter to use (default: $WPS_DIR/.venv/bin/python if present, else python3)
  --name NAME       Service name (default: wps)
  --no-start        Install and enable at boot, but don't start or restart it now
  --dry-run         Print the unit file that would be installed and exit
  --uninstall       Stop, disable and remove the service. WPS files and data are left alone
  -h, --help        Show this help
EOF
}

die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARNING: $*" >&2; }
info() { echo "==> $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --user)      [ $# -ge 2 ] || die "--user needs a value"; RUN_USER="$2"; USER_GIVEN=1; shift 2 ;;
        --python)    [ $# -ge 2 ] || die "--python needs a value"; PYTHON="$2"; shift 2 ;;
        --name)      [ $# -ge 2 ] || die "--name needs a value"; SERVICE_NAME="$2"; shift 2 ;;
        --no-start)  START=0; shift ;;
        --dry-run)   ACTION="dry-run"; shift ;;
        --uninstall) ACTION="uninstall"; shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           usage >&2; die "unknown option: $1" ;;
    esac
done

UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Runs a command as the service user, to check things as the service will see them
run_as_user() {
    if [ "$(id -un)" = "$RUN_USER" ]; then
        "$@"
    else
        sudo -n -u "$RUN_USER" -- "$@"
    fi
}

require_systemd() {
    command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] \
        || die "systemd isn't running on this system. This installer needs a systemd-based Linux (e.g. Raspberry Pi OS)"
}

require_root() {
    [ "$(id -u)" -eq 0 ] || die "run with sudo: sudo $0 $ORIG_ARGS"
}

uninstall() {
    require_systemd
    require_root
    if [ ! -f "$UNIT_FILE" ]; then
        info "No $UNIT_FILE - nothing to remove"
        exit 0
    fi
    info "Stopping and disabling $SERVICE_NAME"
    systemctl disable --now "$SERVICE_NAME" || true
    rm -f "$UNIT_FILE"
    systemctl daemon-reload
    systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true
    info "Removed $UNIT_FILE. WPS files and data in $WPS_DIR are unchanged"
}

render_unit() {
    cat <<EOF
[Unit]
Description=WPS - Packet Radio Messaging Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$WPS_DIR
ExecStart="$PYTHON" wps.py
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=90
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
}

if [ "$ACTION" = "uninstall" ]; then
    uninstall
    exit 0
fi

# --- Work out and check the settings -----------------------------------------------

[ -f "$WPS_DIR/wps.py" ] || die "wps.py not found in $WPS_DIR - run this script from the WPS directory"

id "$RUN_USER" >/dev/null 2>&1 || die "user '$RUN_USER' doesn't exist"
if [ "$RUN_USER" = "root" ] && [ "$USER_GIVEN" -eq 0 ]; then
    die "the service would run as root. Run with sudo from your normal account, or pass --user USER (--user root if you really mean it)"
fi

if [ -z "$PYTHON" ]; then
    if [ -x "$WPS_DIR/.venv/bin/python" ]; then
        PYTHON="$WPS_DIR/.venv/bin/python"
    else
        PYTHON="$(command -v python3 || true)"
        [ -n "$PYTHON" ] || die "python3 not found. Install it, or pass --python PATH"
    fi
fi
case "$PYTHON" in
    /*) ;;
    *)  PYTHON="$(command -v "$PYTHON" || true)"; [ -n "$PYTHON" ] || die "--python must be an absolute path or on PATH" ;;
esac
[ -x "$PYTHON" ] || die "$PYTHON isn't executable"

if [ "$ACTION" = "dry-run" ]; then
    echo "# Would be written to $UNIT_FILE"
    render_unit
    exit 0
fi

require_systemd
require_root

info "Checking $RUN_USER can run WPS from $WPS_DIR with $PYTHON"

# WPS writes env.json, channels.json, the database and logs to its working directory
run_as_user test -w "$WPS_DIR" \
    || die "$RUN_USER can't write to $WPS_DIR. Fix with: sudo chown -R $RUN_USER $WPS_DIR"
for f in env.json channels.json wps.db wps.log db.log; do
    if [ -e "$WPS_DIR/$f" ] && ! run_as_user test -w "$WPS_DIR/$f"; then
        die "$RUN_USER can't write to $WPS_DIR/$f. Fix with: sudo chown -R $RUN_USER $WPS_DIR"
    fi
done

if ! run_as_user "$PYTHON" -c "import requests" >/dev/null 2>&1; then
    if [ "$PYTHON" = "/usr/bin/python3" ]; then
        die "the 'requests' package isn't available to $PYTHON. Install it with: sudo apt install python3-requests"
    fi
    die "the 'requests' package isn't available to $PYTHON. Install it with: $PYTHON -m pip install -r $WPS_DIR/requirements.txt"
fi

if [ ! -f "$WPS_DIR/env.json" ]; then
    warn "env.json doesn't exist yet. WPS will create a default one on first start - running 'python3 wps.py' once from the terminal first is recommended, to check it starts cleanly"
fi

# A copy of WPS started from a terminal would hold the TCP port and make the service fail to start
SERVICE_PID="$(systemctl show -p MainPID --value "$SERVICE_NAME" 2>/dev/null || echo 0)"
for pid in $(pgrep -f "python[0-9.]* .*wps\.py" 2>/dev/null || true); do
    [ "$pid" = "$SERVICE_PID" ] && continue
    if [ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" = "$WPS_DIR" ]; then
        warn "WPS is already running outside the service (pid $pid). Stop it (Ctrl+C in its terminal) or the service won't be able to open its TCP port"
    fi
done

# --- Install ----------------------------------------------------------------------

NEW_UNIT="$(render_unit)"
if [ -f "$UNIT_FILE" ]; then
    if [ "$(cat "$UNIT_FILE")" = "$NEW_UNIT" ]; then
        info "$UNIT_FILE is already up to date"
    else
        cp "$UNIT_FILE" "$UNIT_FILE.bak"
        info "Updating $UNIT_FILE (previous version saved as $UNIT_FILE.bak)"
        printf '%s\n' "$NEW_UNIT" > "$UNIT_FILE"
    fi
else
    info "Writing $UNIT_FILE"
    printf '%s\n' "$NEW_UNIT" > "$UNIT_FILE"
fi
chmod 644 "$UNIT_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
info "Enabled $SERVICE_NAME to start at boot"

if [ "$START" -eq 0 ]; then
    info "Not starting now (--no-start). Start with: sudo systemctl start $SERVICE_NAME"
    exit 0
fi

if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Restarting $SERVICE_NAME (this disconnects connected users)"
    systemctl restart "$SERVICE_NAME"
else
    info "Starting $SERVICE_NAME"
    systemctl start "$SERVICE_NAME"
fi

# Give it a moment, so a failure on startup (bad env.json, port in use) shows up here
sleep 3
if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "$SERVICE_NAME is running"
else
    systemctl status "$SERVICE_NAME" --no-pager || true
    die "$SERVICE_NAME didn't stay running. See: journalctl -u $SERVICE_NAME -n 50 and journalctl -t WPS -n 50"
fi

cat <<EOF

WPS is installed as the '$SERVICE_NAME' service.

  Follow the logs:       journalctl -t WPS -f
  Status:                sudo systemctl status $SERVICE_NAME
  Warm-reload code:      sudo systemctl reload $SERVICE_NAME
  Restart:               sudo systemctl restart $SERVICE_NAME
  Remove the service:    sudo $0 --uninstall
EOF
