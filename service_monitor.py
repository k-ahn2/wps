import asyncio
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid
import telnetlib3

'''
Script to BPQ Telnet Server and WPS to verify it's working and to show the output
Script will raise a monitoring alert if:
- It cannot connect to the Telnet Server
- It cannot log in successfully
- It does not receive any response to the WPS command
- It receives a response to WPS that does not indicate a successful connection

Written by Copilot
'''

class FatalMonitorError(Exception):
    pass

def ensure_onesignal_runtime():
    """Re-exec with .venv interpreter when OneSignal is needed but unavailable."""
    monitor_cfg = ENV.get("serviceMonitoring", {})
    callsigns = monitor_cfg.get("enabledCallsignsToReceiveServiceNotifications", [])
    push_enabled = bool(monitor_cfg.get("enableServiceMonitoring", False) and len(callsigns) > 0)

    if not push_enabled:
        return

    try:
        importlib.import_module("onesignal")
        return
    except Exception:
        pass

    # Prevent infinite re-exec loops.
    if os.environ.get("WPS_MONITOR_VENV_REEXEC") == "1":
        return

    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
    if venv_python.exists():
        os.environ["WPS_MONITOR_VENV_REEXEC"] = "1"
        os.execv(str(venv_python), [str(venv_python), *sys.argv])

def load_env(env_path="env.json"):
    with open(env_path, "r", encoding="utf-8") as env_file:
        return json.load(env_file)

ENV = load_env()
ensure_onesignal_runtime()

def load_monitor_config():
    monitor_config = ENV.get("serviceMonitoring", {})

    host = monitor_config.get("bpqEndpoint", "127.0.0.1")
    port = int(monitor_config.get("bpqPort", 8010))
    username = monitor_config.get("telnetUsername", "")
    password = monitor_config.get("telnetPassword", "")
    return host, port, username, password

def get_service_monitoring_player_ids():
    monitor_cfg = ENV.get("serviceMonitoring", {})
    callsigns = monitor_cfg.get("enabledCallsignsToReceiveServiceNotifications", [])

    if not monitor_cfg.get("enableServiceMonitoring", False) or not callsigns:
        return []

    db_filename = ENV.get("dbFilename", "")
    if not db_filename:
        print("Error resolving push recipients: missing dbFilename in env.json")
        return []

    placeholders = ",".join(["?" for _ in callsigns])
    query = f"""
    SELECT user
    FROM users
    WHERE UPPER(json_extract(user, '$.callsign')) IN ({placeholders})
    """

    resolved_player_ids = []
    try:
        conn = sqlite3.connect(db_filename)
        cursor = conn.cursor()
        cursor.execute(query, [str(c).upper() for c in callsigns])

        for (user_json,) in cursor.fetchall():
            user = json.loads(user_json)
            print(f"Resolving push recipients, found user: {user.get('callsign', None)}")
            for push_entry in user.get("push", []):
                player_id = push_entry.get("playerId")
                is_enabled = push_entry.get("isPushEnabled")
                is_bad = "isBadPlayerId" in push_entry
                if player_id and is_enabled and not is_bad:
                    resolved_player_ids.append(player_id)
    except Exception as e:
        print(f"Error resolving push recipients from database: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Return unique values while preserving discovery order.
    return list(dict.fromkeys(resolved_player_ids))

def send_push_notification(heading, message, player_id):
    # Mirrors the push behavior used by the main WPS server.
    if not ENV.get("serviceMonitoring", {}).get("enableServiceMonitoring", False):
        return {"result": "success", "data": "Notification triggered but sending disabled"}

    app_id = ENV.get("notificationsProdId", "")
    rest_key = ENV.get("notificationsProdRestKey", "")

    if not app_id:
        return {
            "result": "failure",
            "error": "Missing OneSignal app id. Set notificationsProdId in env.json",
        }

    if not rest_key:
        return {
            "result": "failure",
            "error": "Missing OneSignal REST key. Set notificationsProdRestKey in env.json",
        }
    
    try:
        onesignal = importlib.import_module("onesignal")
        default_api = importlib.import_module("onesignal.api.default_api")
        notification_module = importlib.import_module("onesignal.model.notification")
        Notification = notification_module.Notification
    except Exception as e:
        return {
            "result": "failure",
            "error": f"OneSignal SDK not available in {sys.executable} ({e})",
        }

    configuration = onesignal.Configuration(
        rest_api_key=rest_key
    )

    notification = Notification()
    notification.set_attribute("app_id", app_id)
    notification.set_attribute("external_id", str(uuid.uuid4()))
    notification.set_attribute("headings", {"en": heading})
    notification.set_attribute("contents", {"en": message})
    notification.set_attribute("include_player_ids", [player_id])

    with onesignal.ApiClient(configuration) as api_client:
        api_instance = default_api.DefaultApi(api_client)

    try:
        notification_response = api_instance.create_notification(notification)
        if "errors" in notification_response:
            raise Exception(notification_response.get("errors"))
        return {"result": "success", "data": notification_response}
    except Exception as e:
        return {"result": "failure", "error": e.args[0] if len(e.args) > 0 else str(e)}

def echo_server_output(text):
    if text:
        print(text, end="", flush=True)

def normalize_for_match(data):
    # Normalize line endings/spacing so matching works across CRLF/LF variations.
    return " ".join(data.replace("\r", " ").replace("\n", " ").split()).lower()

async def read_and_echo_until_idle(reader, idle_seconds=0.6, max_wait_seconds=5, skip_leading_line=None):
    deadline = time.time() + max_wait_seconds
    last_data_time = None
    saw_output = False
    captured = []
    leading_buffer = ""
    leading_processed = skip_leading_line is None

    while time.time() < deadline:
        chunk = ""
        remaining = max(0, deadline - time.time())
        read_timeout = min(0.2, remaining)

        try:
            if read_timeout > 0:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=read_timeout)
        except asyncio.TimeoutError:
            chunk = ""

        if chunk:
            if not leading_processed:
                leading_buffer += chunk
                line_end_idx = -1
                for sep in ("\r", "\n"):
                    idx = leading_buffer.find(sep)
                    if idx != -1 and (line_end_idx == -1 or idx < line_end_idx):
                        line_end_idx = idx

                if line_end_idx == -1:
                    # Wait for a full first line before deciding whether to suppress it.
                    continue

                first_line = leading_buffer[:line_end_idx].strip()
                remainder_start = line_end_idx
                while remainder_start < len(leading_buffer) and leading_buffer[remainder_start] in ("\r", "\n"):
                    remainder_start += 1

                if first_line == skip_leading_line:
                    chunk = leading_buffer[remainder_start:]
                else:
                    chunk = leading_buffer

                leading_buffer = ""
                leading_processed = True

            if not chunk:
                continue

            saw_output = True
            last_data_time = time.time()
            captured.append(chunk)
            echo_server_output(chunk)
        elif last_data_time is not None and (time.time() - last_data_time) >= idle_seconds:
            break

        await asyncio.sleep(0.05)

    return saw_output, "".join(captured)

async def read_until_contains(reader, expected_text, timeout=5):
    deadline = time.time() + timeout
    buffer = []
    normalized_expected = normalize_for_match(expected_text)

    while time.time() < deadline:
        chunk = ""
        remaining = max(0, deadline - time.time())
        read_timeout = min(0.2, remaining)

        try:
            if read_timeout > 0:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=read_timeout)
        except asyncio.TimeoutError:
            chunk = ""

        if chunk:
            buffer.append(chunk)
            echo_server_output(chunk)

            if normalized_expected in normalize_for_match("".join(buffer)):
                return "".join(buffer)

        await asyncio.sleep(0.05)

    notify_error("Timeout", "waiting for: " + expected_text)
    raise TimeoutError("Timed out waiting for: " + expected_text)

async def login(reader, writer, username, password):
    print("\n--- Login exchange ---")
    await read_until_contains(reader, "user:", timeout=5)
    writer.write(f"{username}\r\n")
    await writer.drain()

    await read_until_contains(reader, "password:", timeout=5)
    writer.write(f"{password}\r\n")
    await writer.drain()

    _, captured = await read_and_echo_until_idle(reader)
    return captured

async def wait_for_command_prompt(reader, expected_prompt, timeout=10, initial_data=""):
    normalized_expected = normalize_for_match(expected_prompt)
    normalized_initial = normalize_for_match(initial_data)

    if normalized_expected in normalized_initial:
        return

    deadline = time.time() + timeout
    buffer = []

    while time.time() < deadline:
        chunk = ""
        remaining = max(0, deadline - time.time())
        read_timeout = min(0.2, remaining)

        try:
            if read_timeout > 0:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=read_timeout)
        except asyncio.TimeoutError:
            chunk = ""

        if chunk:
            buffer.append(chunk)
            echo_server_output(chunk)
            if normalized_expected in normalize_for_match("".join(buffer)):
                return

        await asyncio.sleep(0.05)

    notify_error("Timeout", "Timed out waiting for: " + expected_prompt)

async def read_response(reader, writer, command, settle_seconds=0.6, max_wait_seconds=5):
    writer.write(f"{command}\r\n")
    await writer.drain()
    print(f"\n--- Response for '{command}' ---")
    saw_output, response = await read_and_echo_until_idle(
        reader,
        idle_seconds=settle_seconds,
        max_wait_seconds=max_wait_seconds,
        skip_leading_line=command,
    )

    if command == "WPS" and saw_output and "*** Connected" not in response:
        notify_error(command, "Unable to connect to WPS")

    if not saw_output:
        print("<no data>")
        notify_error(command, "No response received")

def notify_error(source, error):
    print(f"Error in {source}: {error}")

    player_ids = get_service_monitoring_player_ids()
    for player_id in player_ids:
        push_response = send_push_notification(
            heading="WPS Monitor Alert",
            message=f"{source}: {error}",
            player_id=player_id,
        )
        if push_response.get("result") == "failure":
            print(f"Error sending push notification: {push_response.get('error')}")

    raise FatalMonitorError(error)

async def main():
    host, port, username, password = load_monitor_config()
    commands = ["WPS", "monitor"]

    print(f"Connecting via telnet to {host}:{port}...")
    try:
        reader, writer = await telnetlib3.open_connection(host=host, port=port)
    except (OSError, asyncio.TimeoutError) as e:
        notify_error("Connection", f"Unable to reach {host}:{port} ({e})")
        return
    except Exception as e:
        notify_error("Connection", f"Unexpected connection error: {e}")
        return

    try:
        post_login_data = await login(reader, writer, username, password)
        await wait_for_command_prompt(
            reader,
            expected_prompt="Connected to Telnet Server",
            initial_data=post_login_data,
        )
        await read_response(reader, writer, "?")

        for command in commands:
            await read_response(reader, writer, command)
    finally:
        writer.close()
        await writer.wait_closed()
        print("\nDisconnected.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except FatalMonitorError:
        pass
