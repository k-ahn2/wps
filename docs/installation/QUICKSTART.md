# Quick Start - DAPPS, WPS and Replication

The minimum steps to get two or more packet nodes each running WPS, linked as a **tree**, with messages, posts and user names replicating between them over DAPPS.

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
5. [Step 4 - Check It Works](#step-4---check-it-works)
6. [Common Problems](#common-problems)

## Before You Start

**Repeat Steps 1 to 3 on every node** that will run a replicated WPS instance. Work through the [checklist](#prerequisite-checklist) below first.

**Agree callsigns with the other sysops first.** Every node needs three, and every node needs to know its neighbours':

| Name | Example (node A) | Example (node B) | Used for |
| - | - | - | - |
| Node callsign | `T3EST-1` | `T4EST-1` | Your existing BPQ or Xrouter node |
| DAPPS callsign | `T3EST-7` | `T4EST-7` | DAPPS only. **Must be different** from the node and WPS callsigns |
| Origin callsign | `T3EST` | `T4EST` | Names this WPS instance in replication, and shows on posts that came from it |

The examples below use node A's values. Swap in your own.

> [!IMPORTANT]
> This guide links the nodes as a **tree**. Each node lists only its **neighbours** (the nodes it links to directly) as peers, and every node has `relay` on, so content is passed along the tree to the nodes beyond. Agree who neighbours whom before you start. See [3.2](#32-the-tree-who-neighbours-whom). For a full mesh instead, see [Replication - How It Works](/docs/replication/REPLICATION.md).

### Prerequisite checklist

Tick all of these before Step 1. Most problems later come from a callsign that doesn't match or a link that never worked.

**This node**
- [ ] A working BPQ or Xrouter node on a `systemd`-based Linux (e.g. Raspberry Pi OS)
- [ ] `sudo` access on it
- [ ] `git` and Python 3 installed (`git --version`, `python3 --version`)
- [ ] These TCP ports free, or you know which you'll use instead: `5000` (DAPPS dashboard), `8000` (AGW), `63001` (WPS), `8095` (WPS dashboard)
- [ ] Two unused `APPLICATION` numbers in `bpq32.cfg`, one for WPS and one for DAPPS (this guide uses `1` and `2`)

**The node(s) you'll replicate to**
- [ ] You know this node's neighbours in the tree: the nodes it will link to directly and list as peers (see [3.2](#32-the-tree-who-neighbours-whom))
- [ ] You have a packet link (RF, AXIP or AXUDP) to each neighbour, and have confirmed it by connecting from your node to theirs
- [ ] Each neighbour's sysop is setting up WPS and DAPPS too, and you've agreed when you'll test together
- [ ] Joining a tree that already has content? Agree a `bootstrapFromTs` starting point with your neighbour's sysop (see [3.3](#33-joining-an-existing-tree-set-bootstrapfromts))

**Callsigns**
- [ ] You've chosen your node, DAPPS and origin callsigns, and the DAPPS callsign is different from your node and WPS callsigns and not used by anything else on your node
- [ ] You've sent your DAPPS and origin callsigns to each neighbour's sysop
- [ ] You have each neighbour's **DAPPS callsign** and **origin callsign**, SSIDs included, checked character by character

**Agreed with the other sysops**
- [ ] The shape of the tree: who neighbours whom, with no loops, and `relay` on at every node
- [ ] Everyone will run the same WPS version
- [ ] One shared `channels.json`, and who supplies it (see [3.4](#34-match-channelsjson))
- [ ] If bots are on, which single node runs them (see [3.5](#35-things-to-agree-with-the-other-sysops))

It helps to write your neighbours down as you'll enter them in Step 1.4 and Step 3.1:

| Neighbour | Node callsign | DAPPS callsign | Origin callsign | Node port that reaches it |
| - | - | - | - | - |
| Node B | `T4EST-1` | `T4EST-7` | `T4EST` | e.g. `2` |

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

### 1.4 Add each neighbour

In the DAPPS dashboard's **Neighbours** panel, add each neighbour's **DAPPS callsign** (e.g. `T4EST-7`) and the node port that reaches it.

> [!NOTE]
> If the link is AXIP/AXUDP with per-callsign `MAP` entries, add a `MAP` for the neighbour's DAPPS callsign as well as its node callsign.

### 1.5 Prove the link

Once each neighbour has also finished Step 1, use the dashboard's **Send a test message** form to send to the neighbour's DAPPS callsign, and confirm it appears on the neighbour's `/Inbound` page. **Don't move on to replication until this works in both directions.**

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

### 2.3 Run WPS as a service

First stop the copy of WPS you started in 2.1 (`Ctrl+C`). If it's still running, it holds the TCP port and the service can't start.

Then, from the WPS directory, as your normal user (not root):

```
cd ~/wps
sudo ./install_service.sh
```

The script:
1. Checks your user can write to the WPS directory, and that Python can import `requests`
2. Installs the `wps` service, running as you, using the virtual environment's Python if the WPS directory has one (`.venv`, `venv` or `env`), otherwise `python3`
3. Installs the `wps-dashboard` service for the [web dashboard](/docs/installation/INSTALLATION.md#web-dashboard), plus a sudoers rule that lets the dashboard start, stop and restart WPS (and nothing else)
4. Enables both to start at boot, starts them, and checks they stay running

It should finish with `wps is running` and `wps-dashboard is running`. If WPS doesn't stay running, the script says so and shows which logs to check.

> [!TIP]
> To see the files it will write before installing anything, run `./install_service.sh --dry-run` (no `sudo` needed). Run `./install_service.sh --help` for all the options.

WPS now runs in the background and starts at boot. Replication is still off until Step 3.

Now browse to the dashboard at `http://<node-ip>:8095/` and **set its password straight away**. There's no default: the first person to open it chooses the password, and the dashboard can change every WPS setting. You'll use it to configure replication in Step 3.

> [!WARNING]
> The dashboard shows message and post content and can edit `env.json`, and it uses plain HTTP. Keep port 8095 off the internet. See [Web Dashboard](/docs/installation/INSTALLATION.md#web-dashboard).

Run the script again after moving the WPS directory or changing Python environment: it updates the services and restarts WPS. To remove the services, run `sudo ./install_service.sh --uninstall` (your WPS files and data are left alone).

See [Running WPS as a Service](/docs/installation/INSTALLATION.md#running-wps-as-a-service) for the options, and for setting the services up by hand instead.

### 2.4 Check and manage WPS from the dashboard

Log in to the dashboard at `http://<node-ip>:8095/` and choose **Service** at the top left.

**Check it's running.** The **WPS** card should show a green **running** label and `systemd service wps`, with:
- **Running since**: when WPS last started
- **Connected users**: who's connected now
- **Automatic restarts**: how many times systemd has restarted WPS after it stopped. If this keeps going up, WPS is crashing: check the logs

If it shows **stopped** or **failed**, check the logs (below), fix the problem, then click **Start**.

**Start, stop and restart.** The buttons on the WPS card:

| Button | Use it to | Disconnects users |
| - | - | - |
| **Start** | Start WPS when it's stopped | - |
| **Stop** | Stop WPS. Nobody can connect until you start it again | Yes |
| **Restart** | Apply settings the Config page marks **restart**, such as the replication settings in Step 3 | Yes, while it restarts |
| **Reload** | Re-read `env.json` and warm-reload WPS's code, e.g. after a `git pull` | No |

**Stop** and **Restart** ask you to confirm first. The **Dashboard** card's **Restart dashboard** button restarts only the dashboard. You don't usually need it, since the dashboard restarts by itself whenever WPS does.

**Read the logs.** The **Logs** card at the bottom shows the last 200 lines by default. Choose which log with the first drop-down:
- **journalctl: WPS service**: startup messages and errors. Start here if WPS won't start or keeps restarting
- **wps.log**: what WPS is doing, including `REPLICATION` lines once replication is on
- **db.log**: database activity
- **journalctl: dashboard service**: the dashboard's own messages

Type in the filter box to show only matching lines (e.g. `Replication`), and tick **Follow (5s)** to watch new lines arrive.

From a terminal, the same things are `sudo systemctl status wps`, `sudo systemctl start|stop|restart|reload wps` and `journalctl -u wps`.

## Step 3 - Configure Replication

### 3.1 Set replication on the Config page

Log in to the dashboard at `http://<node-ip>:8095/`. It opens on the **Config** page. Scroll down to the **Replication** section and set these six settings. Leave everything else at its default.

| Setting | Set to | Example (node A) |
| - | - | - |
| `enabled` | Ticked | ☑ |
| `dappsCallsign` | **This** node's DAPPS callsign, exactly as entered in Step 1.2, SSID included | `T3EST-7` |
| `originCallsign` | **This** node's origin callsign | `T3EST` |
| `peers` | One entry per **neighbour**. Click **+ Add peer** for each, and fill in its `originCallsign` and `dappsCallsign`. Only the nodes this one links to directly, never nodes further along the tree | `T4EST` / `T4EST-7` |
| `relay` | Ticked, on every node, including the nodes at the ends of the tree | ☑ |
| `appSlug` | Leave as `wps-repl`. Must be the same on every node | `wps-repl` |

The changed settings are listed in the bar at the bottom of the page. Click **Save and apply**. Each of these settings is labelled **restart**, so the page marks them *restart pending*: they take effect when you restart WPS in [3.6](#36-restart-wps).

Each neighbour lists you in return. In the [example tree](#32-the-tree-who-neighbours-whom), node B sits between A and C, so it has two entries under `peers`:

| `originCallsign` | `dappsCallsign` |
| - | - |
| `T3EST` | `T3EST-7` |
| `T5EST` | `T5EST-7` |

### 3.2 The tree: who neighbours whom

Each node lists only its neighbours in `peers`, and passes everything it receives on to its other neighbours. A node only needs a packet link and a DAPPS route to its neighbours, not to every node.

For example, a tree of three nodes with B in the middle:

```
T3EST (A) ─── T4EST (B) ─── T5EST (C)
```

| Node | `peers` | `relay` |
| - | - | - |
| A | B | `true` |
| B | A, C | `true` |
| C | B | `true` |

A post made on C reaches A through B, and the other way round. With just two nodes, each lists the other, and `relay` is still `true` so a third node can join later.

- **No loops.** The links must form a tree. If A, B and C all list each other, it still works, but every post crosses some links twice.
- **`relay` on everywhere.** A node with `relay` off rejects content relayed to it, and doesn't pass anything on.
- **A link down cuts off everything beyond it.** If the A–B link fails in the example, A and C don't see each other's posts until it's back. They then catch up automatically.

See [Relaying - tree topology](/docs/replication/REPLICATION.md#8-relaying---tree-topology) for how it works.

### 3.3 Joining an existing tree? Set `bootstrapFromTs`

If your node is joining nodes that are already replicating, set `bootstrapFromTs` **on your new node only**. Without it, your node asks its neighbours for their full history, and every post and message ever made is sent again over packet radio. With it, your node only receives content from that time onwards.

Set it to the current time in epoch milliseconds. Get the value with:

```
python3 -c "import time; print(int(time.time() * 1000))"
```

Enter the number in the `bootstrapFromTs` field in the Config page's **Replication** section (e.g. `1790000000000`), together with the settings in 3.1, then **Save and apply**.

> [!WARNING]
> **Set and check `bootstrapFromTs` before you restart WPS in [3.6](#36-restart-wps).** That restart switches replication on, and `bootstrapFromTs` is only read then. If it's empty or wrong at that moment, your node asks its neighbours for their full history, and setting it afterwards does nothing. After saving, reload the Config page and check the value is there, and that it's 13 digits (milliseconds, not seconds).

- **Set it before your first start with replication enabled.** It only takes effect then. Afterwards it is ignored, so it's safe to leave set.
- **Neighbour first.** The existing node you will link to must add your node to its `peers` and restart WPS **before** you restart yours in 3.6. They must also run a WPS version that supports `bootstrapFromTs`.
- **Older content stays behind.** Posts and messages from before the cutoff never reach your node. They remain on the nodes that already had them.
- **Your neighbour sets the limit.** Content from nodes further away comes to you through your neighbour, which can only pass on what it has logged since it joined or turned on `relay`. If your cutoff is earlier than that, you get their content from that point instead.
- **Leave it unset** on existing nodes, and when you're setting up a brand-new tree where every node starts empty, because then there's no history to replay.

See [Bringing up a new instance](/docs/replication/REPLICATION.md#bringing-up-a-new-instance) for how it works and the other options.

### 3.4 Match `channels.json`

`channels.json` is **not** replicated. Copy the same file to every node so channel ids (`cid`) mean the same channel everywhere.

### 3.5 Things to agree with the other sysops

- **Same WPS version on every node.** Update all nodes together with `git pull`.
- **One agreed tree.** Agree who neighbours whom, with no loops, and keep `relay` set to `true` everywhere.
- **Bots on one node only.** If `botsEnabled` is `true`, run each bot on just one instance or its posts will double up.

### 3.6 Restart WPS

> [!WARNING]
> Joining an existing tree? Check `bootstrapFromTs` is set on the Config page **before** you restart (see [3.3](#33-joining-an-existing-tree-set-bootstrapfromts)). This restart switches replication on, and it's too late to set it afterwards.

WPS reads the replication settings at startup. Go to the dashboard's **Service** page (top left), click **Restart**, then **Restart WPS** to confirm. Connected users are disconnected while WPS restarts, and their clients reconnect afterwards.

The dashboard restarts along with WPS. Reload the page: the *restart pending* marks on the Config page are gone, and a **Replication** page now appears at the top left.

If the dashboard isn't reachable, restart from a terminal instead: `sudo systemctl restart wps`.

## Step 4 - Check It Works

### 4.1 Startup message

On the dashboard's **Service** page, check WPS shows **running**, and that **Automatic restarts** isn't going up. In the **Logs** card, choose **journalctl: WPS service** and type `Replication` in the filter box. You should see:

```
Replication started: origin=... app=wps-repl peers=... relay=... dapps=...
```

From a terminal, the same lines are in `journalctl -t WPS -n 50`.

### 4.2 Replication dashboard

On the dashboard, choose **Replication** at the top left. Once the nodes are up, each neighbour should show as healthy on the **Overview** tab within a few minutes. Origins that reach this node through a neighbour are listed as **relayed via** that neighbour.

### 4.3 End to end

Connect a client (e.g. [Frames](http://frames.oarc.uk)) to node A and post in a channel. Connect a client to node B and check the post arrives. Then try the other direction. Also check a post reaches a node that isn't a direct neighbour, such as A to C in the example.

Delivery takes seconds to minutes depending on the link. The **Activity log** tab on each dashboard shows the event being sent and received.

## Common Problems

| Startup message or symptom | Fix |
| - | - |
| `Replication disabled ...`, or no **Replication** page on the dashboard | `enabled` isn't ticked in the Config page's Replication section, or WPS hasn't been restarted since |
| `Replication enabled but replication.dappsCallsign/peers are not configured ...` | On the Config page, set `dappsCallsign` and add at least one peer, then restart WPS |
| DAPPS test message never arrives | Node not accepting the DAPPS callsign (on BPQ, `APPLICATION` line missing the DAPPS callsign), node not restarted, AGW not reachable, neighbour not added, or AXIP `MAP` missing for the DAPPS callsign. Fix this before looking at WPS |
| Dashboard shows `rejected` items | The sender isn't in `peers`, or a callsign doesn't match exactly. Check both callsigns for each neighbour, SSIDs included, and that `relay` is `true` on this node |
| Posts reach neighbours but not nodes further away | `relay` is `false` on the node in the middle |
| Posts land in the wrong channel or don't show | `channels.json` differs between nodes |
| Anything else | Set `minWpsLogLevel` to `INFO` on the dashboard's Config page (it applies straight away), then on the Service page choose **wps.log** in the Logs card and filter for `REPLICATION`. See [Monitoring and Operations](/docs/replication/REPLICATION.md#monitoring-and-operations) |

Replication's identity and topology settings (`enabled`, callsigns, `peers`, `relay`, `appSlug`, `bootstrapFromTs`) are only read at startup: after changing them on the Config page, restart WPS from the Service page. The Config page marks every setting that needs a restart and applies the rest when you save.
