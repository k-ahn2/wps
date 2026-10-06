# Quick Start - DAPPS, WPS and Replication

The minimum steps to get two (or more) packet nodes each running WPS, with messages, posts and user names replicating between them over DAPPS.

This guide is deliberately short. For the detail behind each step see:
- [Installation](/docs/installation/INSTALLATION.md) - full WPS install, `env.json` reference, running as a service
- [Replication - How It Works](/docs/replication/REPLICATION.md) - every replication setting, monitoring and troubleshooting
- [DAPPS documentation](https://packet-net.github.io/dapps/)

[Return to README](/README.md)

> [!NOTE]
> WPS and DAPPS can run on either a **BPQ** or an **Xrouter** node. Most steps are the same for both. The node configuration steps (1.3 and 2.2) give BPQ configuration only: running WPS and DAPPS together on Xrouter hasn't been tested yet, so Xrouter configuration isn't included.

## Table of Contents

1. [Before You Start](#before-you-start)
2. [Step 1 - Install DAPPS](#step-1---install-dapps)
3. [Step 2 - Install WPS](#step-2---install-wps)
4. [Step 3 - Configure Replication](#step-3---configure-replication)
5. [Step 4 - Run WPS as a Service](#step-4---run-wps-as-a-service)
6. [Step 5 - Check It Works](#step-5---check-it-works)
7. [Common Problems](#common-problems)

## Before You Start

**Repeat Steps 1 to 4 on every node** that will run a replicated WPS instance. Each node needs:
- A working BPQ or Xrouter node on a `systemd`-based Linux (e.g. Raspberry Pi OS)
- `git` and Python 3
- A packet link (RF, AXIP or AXUDP) to each of the other nodes

**Agree callsigns with the other sysops first.** Every node needs three, and every node needs to know the others':

| Name | Example (node A) | Example (node B) | Used for |
| - | - | - | - |
| Node callsign | `T3EST-1` | `T4EST-1` | Your existing BPQ or Xrouter node |
| DAPPS callsign | `T3EST-7` | `T4EST-7` | DAPPS only. **Must be different** from the node and WPS callsigns |
| Origin callsign | `T3EST` | `T4EST` | Names this WPS instance in replication, and shows on posts that came from it |

The examples below use node A's values. Swap in your own.

> [!IMPORTANT]
> Replication is **full mesh**: every instance must list every other instance as a peer. With three nodes, each node has two peers.

## Step 1 - Install DAPPS

### 1.1 Install

On Debian, Ubuntu or Raspberry Pi OS, install from the packet-net apt repository (recommended):

```
curl -fsSL https://packet-net.github.io/apt/pubkey.asc | sudo gpg --dearmor -o /usr/share/keyrings/packet-net.gpg
echo "deb [signed-by=/usr/share/keyrings/packet-net.gpg] https://packet-net.github.io/apt ./" | sudo tee /etc/apt/sources.list.d/packet-net.list
sudo apt update
sudo apt install dapps
```

Later updates then come with `sudo apt update && sudo apt upgrade`.

On other `systemd`-based Linux, use the one-line installer instead:

```
curl -sSL https://packet-net.github.io/dapps/install.sh | sudo bash
```

> [!WARNING]
> Use one method or the other, never both. See the [DAPPS Linux install page](https://packet-net.github.io/dapps/install/linux/) for details.

### 1.2 First-run setup

Browse to `http://<node-ip>:5000/` and:
1. Set an admin password
2. Enter the **DAPPS callsign** (e.g. `T3EST-7`)
3. Choose the **AGW** bearer, then click **Detect packet node** (it probes `localhost:8000`)

### 1.3 Add DAPPS to your node

DAPPS connects to the node over AGW (TCP port `8000`), and the node must accept connections for the DAPPS callsign.

#### BPQ

In `bpq32.cfg`:

1. Make sure AGW is enabled in the main (top) section:
    ```
    AGWPORT=8000
    ```
2. Add an `APPLICATION` line using the DAPPS callsign. Use an application number not already in use - this guide uses `2` because WPS will take `1` in Step 2:
    ```
    APPLICATION 2,DAPPS,,T3EST-7,DAPPS,0
    ```

Restart BPQ, then check AGW is reachable:

```
nc -vz localhost 8000
```

#### Xrouter

Not yet tested with WPS and DAPPS, so no configuration is given here. See the Xrouter documentation for adding an application callsign and enabling AGW.

### 1.4 Add each peer as a neighbour

In the DAPPS dashboard's **Neighbours** panel, add each peer's **DAPPS callsign** (e.g. `T4EST-7`) and the node port that reaches it.

> [!NOTE]
> If the link is AXIP/AXUDP with per-callsign `MAP` entries, add a `MAP` for the peer's DAPPS callsign as well as its node callsign.

### 1.5 Prove the link

Once the peer has also finished Step 1, use the dashboard's **Send a test message** form to send to the peer's DAPPS callsign, and confirm it appears on the peer's `/Inbound` page. **Don't move on to replication until this works in both directions.**

## Step 2 - Install WPS

### 2.1 Download and first run

```
cd ~
git clone https://github.com/k-ahn2/wps
cd wps
sudo apt install python3-requests
python3 wps.py
```

The first run creates `wps.db`, `env.json`, `channels.json` and the log files. Check the console for errors and note the TCP port it reports (default `63001`). Press `Ctrl+C` to stop it.

### 2.2 Add WPS to your node

The node must pass connections for the WPS application to WPS's TCP port (default `63001`).

#### BPQ

In `bpq32.cfg`, inside your Telnet port's `CONFIG` section, add (or extend) these entries:

```
PORT
   PORTNUM=8
   DRIVER=TELNET
   CONFIG
   DisconnectOnClose=1
   CMDPORT 63001
   MAXSESSIONS=25
   ....
END PORT
```

Then add the WPS application, where `8` is that Telnet port's `PORTNUM` and `HOST 0` is the first `CMDPORT` entry (`63001`):

```
APPLICATION 1,WPS,C 8 HOST 0 TRANS
```

Restart BPQ. See [Node Integration](/docs/installation/INSTALLATION.md#node-integration---interfacing-with-bpq-or-xrouter) for the version with an application callsign and NET/ROM alias.

#### Xrouter

Not yet tested with WPS and DAPPS, so no configuration is given here. See the Xrouter documentation for adding an application that connects to a TCP port.

## Step 3 - Configure Replication

### 3.1 Edit `env.json`

In the WPS directory, find the `replication` block in `env.json` and set these five values. Leave everything else at its default.

```json
"replication": {
    "enabled": true,
    "dappsCallsign": "T3EST-7",
    "originCallsign": "T3EST",
    "peers": [
        {"originCallsign": "T4EST", "dappsCallsign": "T4EST-7"}
    ],
    "appSlug": "wps-repl",
    ...
}
```

| Setting | Set to |
| - | - |
| `enabled` | `true` |
| `dappsCallsign` | **This** node's DAPPS callsign, exactly as entered in Step 1.2, SSID included |
| `originCallsign` | **This** node's origin callsign |
| `peers` | One entry per **other** node: its origin callsign and its DAPPS callsign |
| `appSlug` | Leave as `wps-repl`. Must be the same on every node |

The peer's config is the mirror image of yours. For node B in the example:

```json
"dappsCallsign": "T4EST-7",
"originCallsign": "T4EST",
"peers": [
    {"originCallsign": "T3EST", "dappsCallsign": "T3EST-7"}
]
```

### 3.2 Joining an existing mesh? Set `bootstrapFromTs`

If your node is joining nodes that are already replicating, set `bootstrapFromTs` **on your new node only**. Without it, your node asks every peer for its full history, and every post and message ever made is sent again over packet radio. With it, your node only receives content from that time onwards.

Set it to the current time in epoch milliseconds. Get the value with:

```
python3 -c "import time; print(int(time.time() * 1000))"
```

Add it to the `replication` block:

```json
"replication": {
    "enabled": true,
    "dappsCallsign": "T3EST-7",
    "originCallsign": "T3EST",
    "peers": [
        {"originCallsign": "T4EST", "dappsCallsign": "T4EST-7"}
    ],
    "bootstrapFromTs": 1790000000000,
    ...
}
```

- **Set it before your first start with replication enabled.** It only takes effect then. Afterwards it is ignored, so it's safe to leave in `env.json`.
- **Peers first.** Each existing peer must add your node to its `peers` list and restart **before** you start your node. They must also run a WPS version that supports `bootstrapFromTs`.
- **Older content stays behind.** Posts and messages from before the cutoff never reach your node. They remain on the nodes that already had them.
- **Leave it unset** on existing nodes, and when you're setting up a brand-new mesh where every node starts empty, because then there's no history to replay.

See [Bringing up a new instance](/docs/replication/REPLICATION.md#bringing-up-a-new-instance) for how it works and the other options.

### 3.3 Match `channels.json`

`channels.json` is **not** replicated. Copy the same file to every node so channel ids (`cid`) mean the same channel everywhere.

### 3.4 Things to agree with the other sysops

- **Same WPS version on every node.** Update all nodes together with `git pull`.
- **Bots on one node only.** If `botsEnabled` is `true`, run each bot on just one instance or its posts will double up.

## Step 4 - Run WPS as a Service

Create `/etc/systemd/system/wps.service`, changing `User` and both paths if you aren't the `pi` user in `/home/pi/wps`:

```ini
[Unit]
Description=WPS - Packet Radio Messaging Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/wps
ExecStart=/usr/bin/python3 wps.py
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=90
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```
sudo systemctl daemon-reload
sudo systemctl enable --now wps
```

See [Running WPS as a Service](/docs/installation/INSTALLATION.md#running-wps-as-a-service) for what each setting does.

## Step 5 - Check It Works

### 5.1 Startup message

```
journalctl -t WPS -n 50
```

You should see:

```
Replication started: origin=... app=wps-repl peers=... dapps=...
Replication dashboard on http://0.0.0.0:8095/
```

### 5.2 Replication dashboard

Browse to `http://<node-ip>:8095/`. Once both nodes are up, each peer should show as healthy on the **Overview** tab within a few minutes.

> [!WARNING]
> The dashboard shows message and post content and has no login by default. Keep port 8095 off the internet, or set `replication.dashboard.password` in `env.json` and restart WPS.

### 5.3 End to end

Connect a client (e.g. [Frames](http://frames.oarc.uk)) to node A and post in a channel. Connect a client to node B and check the post arrives. Then try the other direction.

Delivery takes seconds to minutes depending on the link. The **Activity log** tab on each dashboard shows the event being sent and received.

## Common Problems

| Startup message or symptom | Fix |
| - | - |
| `Replication disabled ...` | `replication.enabled` is not `true` in `env.json` |
| `Replication enabled but replication.dappsCallsign/peers are not configured ...` | Set `dappsCallsign` and add at least one entry to `peers` |
| DAPPS test message never arrives | Node not accepting the DAPPS callsign (on BPQ, `APPLICATION` line missing the DAPPS callsign), node not restarted, AGW not reachable, neighbour not added, or AXIP `MAP` missing for the DAPPS callsign. Fix this before looking at WPS |
| Dashboard shows `rejected` items | The sender isn't in `peers`, or a callsign doesn't match exactly. Check both callsigns for each peer, SSIDs included |
| Posts land in the wrong channel or don't show | `channels.json` differs between nodes |
| Anything else | Set `minWpsLogLevel` to `INFO` in `env.json`, restart WPS, and look for `REPLICATION` lines in `wps.log`. See [Monitoring and Operations](/docs/replication/REPLICATION.md#monitoring-and-operations) |

After changing `env.json`, always run `sudo systemctl restart wps` - replication settings are only read at startup.
