'''
WPS traffic harness

Simulates packet-node traffic into two WPS instances to exercise posting and replication.

Each session:
  1. Opens a direct TCP connection to a WPS instance
  2. Sends the callsign line (as the BPQ node does on connect)
  3. Sends N channel posts (type cp, cid 6) 1100ms apart
  4. Waits for a cpr for every post sent
  5. Holds the connection open for a 5 second tail, then disconnects

Tests:
  1) 1 post to WPS 1
  2) 2 posts to WPS 1
  3) 4 posts to WPS 1
  4) 1 post to WPS 1 and 1 post to WPS 2 in parallel
  5) 4 posts to each of WPS 1 and WPS 2 in parallel

In tests 4 and 5 both sessions connect together, but WPS 2 waits a further 2 seconds before
its first post. Pass --suppress-gap to have both start posting at the same time.

Usage:
  python misc/traffic_harness.py --wps1 localhost:63000:M0ABC --wps2 otherhost:63000:M0XYZ --test 1
'''

import argparse
import asyncio
import base64
import json
import sys
import threading
import time
import zlib

CHANNEL_ID = 6
POST_TEXT = "The quick brown fox jumps over the lazy dog 123456"
assert len(POST_TEXT) == 50

POST_INTERVAL = 1.1         # seconds between posts
TAIL = 5.0                  # seconds to hold the connection after the last cpr
CALLSIGN_SETTLE = 0.5       # pause after the callsign so WPS reads it as its own recv()
WPS2_START_GAP = 2.0        # extra wait before WPS 2 starts posting in the parallel tests
COMPRESSION_DELIMITER = chr(192)

T0 = time.monotonic()

def log(label, text):
    print(f"[{time.monotonic() - T0:8.3f}] {label:<12} {text}", flush=True)

# Post timestamps must be unique across both instances - WPS de-dupes posts on cid + ts,
# so two parallel posts landing in the same millisecond would collide once replicated
_ts_lock = threading.Lock()
_last_ts = 0

def unique_ts():
    global _last_ts
    with _ts_lock:
        _last_ts = max(round(time.time() * 1000), _last_ts + 1)
        return _last_ts

def decode_frame(frame):
    '''
    WPS sends either plain JSON or chr(192) + base64(zlib(JSON)) + chr(192)
    '''
    if frame.startswith(COMPRESSION_DELIMITER) and frame.endswith(COMPRESSION_DELIMITER):
        frame = zlib.decompress(base64.b64decode(frame[1:-1])).decode('utf-8')
    return json.loads(frame)

class Endpoint:
    def __init__(self, name, spec):
        try:
            host, port, callsign = spec.rsplit(':', 2)
            self.port = int(port)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{name} must be host:port:callsign, got '{spec}'")
        self.name = name
        self.host = host
        self.callsign = callsign.upper()

    def __str__(self):
        return f"{self.name} {self.host}:{self.port} as {self.callsign}"

async def session(endpoint, post_count, cpr_timeout, start_delay=0):
    '''
    Runs one connect / post / wait-for-cpr / tail / disconnect cycle.
    Returns a dict of results for the summary.
    '''
    label = endpoint.name
    result = { "endpoint": endpoint.name, "sent": 0, "acked": 0, "latencies": [], "error": None }

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(endpoint.host, endpoint.port), 10)
    except Exception as e:
        result["error"] = f"connect failed: {e}"
        log(label, result["error"])
        return result

    log(label, f"connected to {endpoint.host}:{endpoint.port}")

    pending = {}                    # ts -> monotonic send time
    all_acked = asyncio.Event()
    sending_done = False

    async def receive():
        buffer = ''
        while True:
            data = await reader.read(4096)
            if not data:
                log(label, "connection closed by WPS")
                return
            buffer += data.decode('utf-8', errors='replace')
            *frames, buffer = buffer.split('\r')
            for frame in frames:
                frame = frame.strip('\n')
                if not frame:
                    continue
                try:
                    obj = decode_frame(frame)
                except Exception:
                    log(label, f"RX (non-JSON) {frame!r}")
                    continue

                if obj.get("t") == "cpr" and obj.get("ts") in pending:
                    rtt = (time.monotonic() - pending.pop(obj["ts"])) * 1000
                    server_ms = obj.get("dts", 0) - obj["ts"]
                    result["acked"] += 1
                    result["latencies"].append(rtt)
                    log(label, f"RX cpr ts={obj['ts']} rtt={rtt:.0f}ms dts-ts={server_ms}ms")
                    if sending_done and not pending:
                        all_acked.set()
                else:
                    log(label, f"RX {obj.get('t')} {json.dumps(obj)[:120]}")

    receiver = asyncio.create_task(receive())

    try:
        # Node behaviour - callsign line first
        writer.write(f"{endpoint.callsign}\r\n".encode())
        await writer.drain()
        log(label, f"TX callsign {endpoint.callsign}")
        await asyncio.sleep(CALLSIGN_SETTLE)

        if start_delay > 0:
            log(label, f"waiting {start_delay:.1f}s before posting")
            await asyncio.sleep(start_delay)

        for i in range(post_count):
            if i > 0:
                await asyncio.sleep(POST_INTERVAL)
            ts = unique_ts()
            post = { "t": "cp", "cid": CHANNEL_ID, "fc": endpoint.callsign, "ts": ts, "p": POST_TEXT }
            pending[ts] = time.monotonic()
            writer.write((json.dumps(post, separators=(',', ':')) + '\r\n').encode())
            await writer.drain()
            result["sent"] += 1
            log(label, f"TX cp {i + 1}/{post_count} ts={ts}")

        sending_done = True
        if pending:
            try:
                await asyncio.wait_for(all_acked.wait(), cpr_timeout)
                log(label, "all cprs received")
            except asyncio.TimeoutError:
                result["error"] = f"{len(pending)} cpr(s) missing after {cpr_timeout}s"
                log(label, result["error"])
        else:
            log(label, "all cprs received")

        log(label, f"tail {TAIL:.0f}s")
        await asyncio.sleep(TAIL)

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log(label, result["error"])

    finally:
        receiver.cancel()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        log(label, "disconnected")

    return result

def build_tests(wps1, wps2, wps2_gap):
    '''
    Each test is a description and a list of (endpoint, post count, start delay) sessions
    '''
    return {
        1: ("1 post to WPS 1",                      [(wps1, 1, 0)]),
        2: ("2 posts to WPS 1",                     [(wps1, 2, 0)]),
        3: ("4 posts to WPS 1",                     [(wps1, 4, 0)]),
        4: ("1 post to each of WPS 1 and WPS 2",    [(wps1, 1, 0), (wps2, 1, wps2_gap)]),
        5: ("4 posts to each of WPS 1 and WPS 2",   [(wps1, 4, 0), (wps2, 4, wps2_gap)]),
    }

async def run(args):
    wps2_gap = 0 if args.suppress_gap else WPS2_START_GAP
    description, sessions = build_tests(args.wps1, args.wps2, wps2_gap)[args.test]

    log("HARNESS", str(args.wps1))
    log("HARNESS", str(args.wps2))
    log("HARNESS", f"=== Test {args.test}: {description} ===")

    started = time.monotonic()
    results = await asyncio.gather(*(session(ep, count, args.cpr_timeout, delay) for ep, count, delay in sessions))
    elapsed = time.monotonic() - started

    passed = all(r["error"] is None and r["acked"] == r["sent"] for r in results)

    print("\nSummary", flush=True)
    print(f"  Test {args.test} {'PASS' if passed else 'FAIL'}  {description}  ({elapsed:.1f}s)")
    for r in results:
        lat = r["latencies"]
        lat_text = f"rtt min/avg/max {min(lat):.0f}/{sum(lat) / len(lat):.0f}/{max(lat):.0f}ms" if lat else "no cprs"
        err = f"  ERROR: {r['error']}" if r["error"] else ""
        print(f"      {r['endpoint']}: {r['acked']}/{r['sent']} acked, {lat_text}{err}")

    return passed

def main():
    parser = argparse.ArgumentParser(description="Simulate node traffic into two WPS instances")
    parser.add_argument("--wps1", required=True, type=lambda s: Endpoint("WPS1", s), help="host:port:callsign")
    parser.add_argument("--wps2", required=True, type=lambda s: Endpoint("WPS2", s), help="host:port:callsign")
    parser.add_argument("--test", required=True, type=int, choices=range(1, 6), help="test to run (1-5)")
    parser.add_argument("--cpr-timeout", type=float, default=60, help="seconds to wait for all cprs after the last post (default 60)")
    parser.add_argument("--suppress-gap", action="store_true", help=f"tests 4 and 5: start WPS 2 posting at the same time as WPS 1, instead of {WPS2_START_GAP:.0f}s later")
    args = parser.parse_args()

    ok = asyncio.run(run(args))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
