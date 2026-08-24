"""
pacagotchi.py  -  Tamagotchi-style bot for the WPS packet radio messaging service.

Channel commands (posted to the configured pacagotchi channel):
  /spawn          create a new pet (only when dead or none exists)
  /feed [food]    feed the pet; 'junk' keywords make it happier but risk illness
  /play           play with the pet
  /clean          clean up poop
  /medicate       cure illness
  /sleep          let the pet rest
  /pet            show the current pet and status
  /stats          full stats including top caretakers
  /help           command reference

State is stored in the 'pacagotchi' table in the WPS SQLite database.
The background tick thread updates pet state every TICK_INTERVAL seconds.
"""

import json, time, random, threading
from handlers import db_logger

# ---------------------------------------------------------------------------
# Configuration — loaded from bots.json "pacagotchi" block, with fallbacks
# ---------------------------------------------------------------------------

def _load_config():
    try:
        with open("bots.json") as f:
            return json.load(f).get("pacagotchi", {})
    except Exception:
        return {}

_cfg = _load_config()

def _c(key, default):
    return _cfg.get(key, default)

TICK_INTERVAL            = _c("tick_interval",           300)
AGE_JUVENILE             = _c("age_juvenile",            3600)
AGE_ADULT                = _c("age_adult",               172800)
HUNGER_DROP_PER_TICK     = _c("hunger_drop_per_tick",    1.67)
HAPPINESS_DROP_BORED     = _c("happiness_drop_bored",    2)
HEALTH_DROP_STARVING     = _c("health_drop_starving",    4)
HEALTH_DROP_DIRTY        = _c("health_drop_dirty",       2)
HEALTH_DROP_ILL_LATE     = _c("health_drop_ill_late",    8)
SLEEP_TICK_MULTIPLIER    = _c("sleep_tick_multiplier",   0.25)
AUTO_SLEEP_AFTER         = _c("auto_sleep_after",        10800)
SLEEP_MIN_HOURS          = _c("sleep_min_hours",         6)
SLEEP_MAX_HOURS          = _c("sleep_max_hours",         10)
POOP_RISE_EVERY_N_TICKS  = _c("poop_rise_every_n_ticks", 7)
JUNK_ILLNESS_THRESHOLD   = _c("junk_illness_threshold",  3)
ILL_DEATH_TIMEOUT        = _c("ill_death_timeout",       10800)

MAX_STAT = 100
MIN_STAT = 0

# ---------------------------------------------------------------------------
# Ham radio–themed pet names
# ---------------------------------------------------------------------------

NAMES = [
    "M0RSEY", "G4DGT",  "W1FER",  "K9BIT",  "VK2CW",
    "ZL3SSB", "W0OFY",  "KG4HAM", "G3RIG",  "VE3ANT",
    "K0KEY",  "W2LOG",  "N1DIP",  "KB3RX",  "WB4YGI",
    "VK4SSB", "G0AMP",  "K1XMT",  "N0DPX",  "W3QRP",
]

# ---------------------------------------------------------------------------
# ASCII art
# {E} is replaced by the mood-appropriate eyes at render time.
# ---------------------------------------------------------------------------

_EYES = {
    "happy": "^.^",
    "sad":   "T_T",
    "ill":   "x.x",
    "dead":  "X.X",
}

# 10 pet types, each with keys: "name", 1, 2, 3 (stages)
_ART = {
    1: {
        "name": "Fox",
        1: "  /\\_/\\\n ({E})\n  \\---/",
        2: "  /\\_/\\\n ({E})\n  ( Y )\n  /| |\\",
        3: " ^/\\_/\\^\n  ({E})\n  ( Y  )\n  /| |\\\n  \"   \"",
    },
    2: {
        "name": "Hamster",
        1: " (o  o)\n ({E})\n  \\--/",
        2: " (oo oo)\n<({E})>\n  \\  /\n  (  )",
        3: "(ooo ooo)\n<( {E} )>\n   \\   /\n   (   )\n   //|\\\\",
    },
    3: {
        "name": "Rabbit",
        1: " | |\n({E})\n ( )",
        2: " /| |\\\n({E})\n( Y )\n| | |",
        3: " //| |\\\\\n ( {E} )\n  ( Y )\n //| |\\\\",
    },
    4: {
        "name": "Owl",
        1: "/O O\\\n({E})\n \\  /",
        2: "  /OO\\\n ({E})\n (    )\n  \\  /",
        3: "   /OO\\\n  ({E})\n  (    )\n  /\\  /\\",
    },
    5: {
        "name": "Cat",
        1: "/\\ /\\\n({E})\n \\v/",
        2: " /\\ /\\\n ({E})\n (    )\n  \\v/",
        3: "/\\   /\\\n ({E})\n(     )\n \\   /\n  \\_/",
    },
    6: {
        "name": "Frog",
        1: "oO  Oo\n({E})\n\\___/",
        2: " oO  Oo\n ({E})\n (    )\n  /||\\",
        3: "OoO  OoO\n ({E})\n(      )\n / ||  \\",
    },
    7: {
        "name": "Bear",
        1: "/oo\\\n({E})\n(  )",
        2: " /oo\\\n ({E})\n (    )\n  \\  /",
        3: " /oo\\\n ({E})\n(      )\n( /|\\ )\n  \\_/",
    },
    8: {
        "name": "Penguin",
        1: " /--\\\n({E})\n \\/\\/",
        2: " /----\\\n ({E})\n(      )\n \\----/",
        3: "  /----\\\n ({E})\n (      )\n  \\----/\n  //  \\\\",
    },
    9: {
        "name": "Duck",
        1: "  __\n({E})>\n  \\/",
        2: "   __\n ({E})>\n (    )\n  /\\/\\",
        3: "    __\n  ({E})>\n (      )\n  \\----/\n  /\\/\\/\\",
    },
    10: {
        "name": "Radio Bot",
        1: "[-----]\n| {E} |\n|_____|",
        2: " [-------]\n |  {E}   |\n |  ~~~~  |\n |--------|",
        3: "  [---------]\n  |   {E}   |\n  |  ~~~~~  |\n  |   | |   |\n  |___|_|___|",
    },
}

def _art(pet_type, stage, mood):
    template = _ART[pet_type][stage]
    return template.replace("{E}", _EYES[mood])

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _init_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pacagotchi (
            id INTEGER PRIMARY KEY,
            state TEXT
        )
    """)
    cursor.connection.commit()

def _load(cursor):
    cursor.execute("SELECT state FROM pacagotchi WHERE id=1")
    row = cursor.fetchone()
    return json.loads(row[0]) if row else None

def _save(cursor, state):
    cursor.execute(
        "INSERT OR REPLACE INTO pacagotchi (id, state) VALUES (1, ?)",
        (json.dumps(state),)
    )
    cursor.connection.commit()

# ---------------------------------------------------------------------------
# Game helpers
# ---------------------------------------------------------------------------

def _mood(state):
    if not state.get("is_alive"):
        return "dead"
    if state.get("ill_since") is not None:
        return "ill"
    if (state.get("happiness", 100) < 30
            or state.get("hunger", 100) < 20
            or state.get("poop_level", 0) >= 3):
        return "sad"
    return "happy"

def _bar(value, width=10):
    filled = max(0, min(width, round(value / 100 * width)))
    return "[" + "#" * filled + "." * (width - filled) + "]"

def _poop_str(level):
    if level == 0:
        return "none"
    return "[P]" * level

def _render(state):
    mood      = _mood(state)
    stage     = state.get("stage", 1)
    pet_type  = state.get("pet_type", 1)
    name      = state.get("name", "???")
    type_name = _ART[pet_type]["name"]

    art       = _art(pet_type, stage, mood)
    age_s     = int(time.time()) - state.get("born_at", int(time.time()))
    age_m     = age_s // 60
    stage_n   = {1: "Baby", 2: "Juvenile", 3: "Adult"}.get(stage, "?")

    hp  = max(0, state.get("health",    100))
    hu  = max(0, state.get("hunger",    100))
    hap = max(0, state.get("happiness", 100))
    pp  = state.get("poop_level", 0)

    lines = [
        f"=== {name} the {type_name} ===",
        art,
        f"Stage: {stage_n} | Mood: {mood.upper()}",
        f"Health:    {_bar(hp)} {hp}%",
        f"Hunger:    {_bar(hu)} {hu}%",
        f"Happiness: {_bar(hap)} {hap}%",
        f"Poop: {_poop_str(pp)}",
    ]
    if not state.get("is_alive"):
        lines.append("*** R.I.P. ***")
    elif mood == "ill":
        lines.append("*** ILL - use /medicate ***")
    elif mood == "sad":
        if hu < 20:
            lines.append("*** HUNGRY - use /feed ***")
        if pp >= 3:
            lines.append("*** DIRTY - use /clean ***")
    return "```\n" + "\n".join(lines) + "\n```"

def _bump_caretaker(state, callsign):
    ct = state.get("caretakers", {})
    ct[callsign] = ct.get(callsign, 0) + 1
    state["caretakers"] = ct

# ---------------------------------------------------------------------------
# Background tick
# ---------------------------------------------------------------------------

def tick(cursor, broadcast_fn, channel_id, fallback_fc="PACBOT"):
    """
    Called by the background thread every TICK_INTERVAL seconds.
    Updates pet state and optionally posts a status update to the channel.
    broadcast_fn(cursor, cid, text, fc) — provided by wps.py
    """
    state = _load(cursor)
    if state is None or not state.get("is_alive"):
        return

    now = int(time.time())
    state["last_tick"] = now

    sleep_until = state.get("sleep_until", 0)

    # Auto-sleep after inactivity (if not already sleeping)
    last_active = state.get("last_active", state.get("born_at", now))
    if sleep_until <= now and (now - last_active) > AUTO_SLEEP_AFTER:
        sleep_until = now + random.randint(SLEEP_MIN_HOURS * 3600, SLEEP_MAX_HOURS * 3600)
        state["sleep_until"] = sleep_until

    # Wake up automatically when sleep_until has passed
    if sleep_until > now:
        sleeping = True
    else:
        sleeping = False
        if state.get("sleep_until", 0) > 0 and "sleep_until" in state:
            # Just woke up — clear the field so we don't re-trigger next tick
            state["sleep_until"] = 0

    m = SLEEP_TICK_MULTIPLIER if sleeping else 1.0  # multiplier for negative deltas

    # Stage promotion
    age_s = now - state["born_at"]
    if age_s >= AGE_ADULT and state["stage"] < 3:
        state["stage"] = 3
    elif age_s >= AGE_JUVENILE and state["stage"] < 2:
        state["stage"] = 2

    # Hunger (halved while asleep — metabolism slows)
    state["hunger"] = max(MIN_STAT, state["hunger"] - round(HUNGER_DROP_PER_TICK * m))

    # Poop accumulation (every N ticks; slower asleep)
    tick_ctr = state.get("poop_tick_ctr", 0) + 1
    effective_poop_ticks = round(POOP_RISE_EVERY_N_TICKS / m)  # longer cycle asleep
    if tick_ctr >= effective_poop_ticks:
        state["poop_tick_ctr"] = 0
        state["poop_level"] = min(5, state["poop_level"] + 1)
    else:
        state["poop_tick_ctr"] = tick_ctr

    # Poop illness trigger
    if state["poop_level"] >= 4 and state.get("ill_since") is None:
        state["ill_since"] = now

    # Health impacts
    if state["hunger"] < 20:
        state["health"] = max(MIN_STAT, state["health"] - round(HEALTH_DROP_STARVING * m))
    if state["poop_level"] >= 4:
        state["health"] = max(MIN_STAT, state["health"] - round(HEALTH_DROP_DIRTY * m))
    if state.get("ill_since") and (now - state["ill_since"]) > ILL_DEATH_TIMEOUT:
        state["health"] = max(MIN_STAT, state["health"] - round(HEALTH_DROP_ILL_LATE * m))

    # Boredom — not applied while sleeping
    if not sleeping and (now - state.get("last_played", now)) > 1800:
        state["happiness"] = max(MIN_STAT, state["happiness"] - HAPPINESS_DROP_BORED)

    # Death check
    if state["health"] <= 0:
        state["is_alive"] = False
        state["health"]   = 0
        _save(cursor, state)
        msg = _render(state) + "\nYour pet has died! Use /spawn to get a new one."
        broadcast_fn(cursor, channel_id, msg, state.get("name", fallback_fc))
        return

    current_mood = _mood(state)
    _save(cursor, state)

    # Post a status nudge if things are going wrong — but stay quiet while sleeping
    if not sleeping and (current_mood in ("sad", "ill") or state["hunger"] < 30):
        msg = _render(state)
        broadcast_fn(cursor, channel_id, msg, state.get("name", fallback_fc))

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_command(cursor, post_text, from_callsign):
    """
    Parse a slash command from a channel post.
    Returns {"text": str, "fc": str} or None if the post is not a command.
    """
    stripped = post_text.strip()
    if not stripped.startswith("/"):
        return None

    parts = stripped.split()
    cmd   = parts[0].lower()
    args  = parts[1:]

    state = _load(cursor)

    if cmd == "/spawn":
        if state and state.get("is_alive"):
            return {"text": f"Your pet {state['name']} is still alive! No need to spawn yet.", "fc": state["name"]}
        return _cmd_spawn(cursor)

    if state is None:
        return {"text": "No pet yet! Use /spawn to create one.", "fc": "PACBOT"}

    fc = state.get("name", "PACBOT")

    if not state.get("is_alive"):
        return {"text": f"{fc} has died. Use /spawn to get a new one.", "fc": fc}

    # Commands that wake the pet (any interactive care action)
    WAKING_CMDS = {"/feed", "/play", "/clean", "/medicate"}
    if cmd in WAKING_CMDS:
        woke = _wake_if_sleeping(state, from_callsign)
    else:
        # Non-waking commands still update last_active so auto-sleep resets
        state["last_active"] = int(time.time())

    handlers = {
        "/feed":     lambda: _cmd_feed(cursor, state, args, from_callsign),
        "/play":     lambda: _cmd_play(cursor, state, from_callsign),
        "/clean":    lambda: _cmd_clean(cursor, state, from_callsign),
        "/medicate": lambda: _cmd_medicate(cursor, state, from_callsign),
        "/sleep":    lambda: _cmd_sleep(cursor, state, from_callsign),
        "/stats":    lambda: _cmd_stats(state),
        "/pet":      lambda: {"text": _render(state), "fc": fc},
        "/show":     lambda: {"text": _render(state), "fc": fc},
        "/status":   lambda: {"text": _render(state), "fc": fc},
        "/help":     lambda: _cmd_help(fc),
    }

    handler = handlers.get(cmd)
    if handler:
        result = handler()
        if cmd in WAKING_CMDS and woke:
            result["text"] = f"({from_callsign} woke {fc} up!)\n" + result["text"]
        return result
    return {"text": f"Unknown command '{cmd}'. Try /help", "fc": fc}


def _cmd_spawn(cursor):
    name     = random.choice(NAMES)
    pet_type = random.randint(1, 10)
    now      = int(time.time())
    state = {
        "name":           name,
        "pet_type":       pet_type,
        "stage":          1,
        "health":         100,
        "hunger":         100,
        "happiness":      80,
        "poop_level":     0,
        "poop_tick_ctr":  0,
        "born_at":        now,
        "last_tick":      now,
        "last_played":    0,
        "ill_since":      None,
        "junk_count":     0,
        "is_alive":       True,
        "caretakers":     {},
    }
    _save(cursor, state)
    type_name = _ART[pet_type]["name"]
    art = _art(pet_type, 1, "happy")
    msg = (
        f"=== A new pet has arrived! ===\n"
        f"{art}\n"
        f"Meet {name} the {type_name}!\n"
        f"Look after it well, 73!\n"
        f"Commands: /feed /play /clean /medicate /sleep /stats /pet /help"
    )
    return {"text": msg, "fc": name}


def _cmd_feed(cursor, state, args, from_callsign):
    fc = state["name"]
    junk_words = {"junk", "burger", "pizza", "chips", "sweets", "cake", "donut", "crisps", "biscuit"}
    food = args[0].lower() if args else "food"
    is_junk = food in junk_words

    if is_junk:
        state["hunger"]    = min(MAX_STAT, state["hunger"]    + 40)
        state["happiness"] = min(MAX_STAT, state["happiness"] + 15)
        state["junk_count"] = state.get("junk_count", 0) + 1
        if state["junk_count"] >= JUNK_ILLNESS_THRESHOLD and state.get("ill_since") is None:
            state["ill_since"]  = int(time.time())
            state["junk_count"] = 0
            extra = f"\n*** Too much junk! {fc} feels ill. Use /medicate ***"
        else:
            extra = f"\n{fc} wolfed down the {food}! (junk count: {state['junk_count']}/{JUNK_ILLNESS_THRESHOLD})"
    else:
        state["hunger"]    = min(MAX_STAT, state["hunger"]    + 30)
        state["happiness"] = min(MAX_STAT, state["happiness"] + 5)
        state["junk_count"] = max(0, state.get("junk_count", 0) - 1)
        if state.get("ill_since") is not None:
            state["health"] = min(MAX_STAT, state["health"] + 5)
        extra = f"\n{fc} ate some healthy {food}. Good work, {from_callsign}!"

    _bump_caretaker(state, from_callsign)
    _save(cursor, state)
    return {"text": _render(state) + extra, "fc": fc}


def _cmd_play(cursor, state, from_callsign):
    fc = state["name"]
    state["happiness"] = min(MAX_STAT, state["happiness"] + 20)
    state["hunger"]    = max(MIN_STAT, state["hunger"]    - 10)
    state["health"]    = min(MAX_STAT, state["health"]    + 5)
    state["last_played"] = int(time.time())
    _bump_caretaker(state, from_callsign)
    _save(cursor, state)
    extra = f"\n{fc} had a great time with {from_callsign}! (Playing makes it hungry)"
    return {"text": _render(state) + extra, "fc": fc}


def _cmd_clean(cursor, state, from_callsign):
    fc = state["name"]
    level = state["poop_level"]
    if level == 0:
        return {"text": f"{fc} is already spotless — nothing to clean!", "fc": fc}
    state["poop_level"] = 0
    state["happiness"]  = min(MAX_STAT, state["happiness"] + 15)
    # Heavy poop that was making it ill: cleaning helps recovery
    if level >= 4 and state.get("ill_since") is not None:
        state["health"] = min(MAX_STAT, state["health"] + 10)
    _bump_caretaker(state, from_callsign)
    _save(cursor, state)
    extra = f"\n{from_callsign} cleaned up {level} poop(s). {fc} is much happier!"
    return {"text": _render(state) + extra, "fc": fc}


def _cmd_medicate(cursor, state, from_callsign):
    fc = state["name"]
    if state.get("ill_since") is None:
        return {"text": f"{fc} is not ill — save the medicine!", "fc": fc}
    if state["health"] < 20:
        return {"text": f"{fc} is too weak. Clean up and feed first, then try /medicate.", "fc": fc}
    state["ill_since"]  = None
    state["health"]     = min(MAX_STAT, state["health"]    + 20)
    state["happiness"]  = max(MIN_STAT, state["happiness"] - 20)
    state["junk_count"] = 0
    _bump_caretaker(state, from_callsign)
    _save(cursor, state)
    extra = f"\n{fc} has been treated by {from_callsign}. Medicine works but tastes awful!"
    return {"text": _render(state) + extra, "fc": fc}


def _cmd_sleep(cursor, state, from_callsign):
    fc = state["name"]
    now = int(time.time())
    if state.get("sleep_until", 0) > now:
        wake_in = (state["sleep_until"] - now) // 3600
        return {"text": f"{fc} is already asleep (wakes in ~{wake_in}h). Shh! Use /feed, /play or /clean to wake.", "fc": fc}
    duration = random.randint(SLEEP_MIN_HOURS * 3600, SLEEP_MAX_HOURS * 3600)
    state["sleep_until"] = now + duration
    state["health"] = min(MAX_STAT, state["health"] + 10)
    state["happiness"] = max(MIN_STAT, state["happiness"] - 5)
    _save(cursor, state)
    hours = duration // 3600
    extra = f"\n{fc} is now sleeping for ~{hours}h. Stats drop 4x slower. Use /feed, /play or /clean to wake early. Zzz..."
    return {"text": _render(state) + extra, "fc": fc}


def _wake_if_sleeping(state, from_callsign):
    """Wake the pet if sleeping and record activity. Called by any interactive care command."""
    now = int(time.time())
    state["last_active"] = now
    if state.get("sleep_until", 0) > now:
        state["sleep_until"] = 0
        return True
    return False


def _cmd_stats(state):
    fc        = state["name"]
    pet_type  = state["pet_type"]
    type_name = _ART[pet_type]["name"]
    mood      = _mood(state)
    stage     = state.get("stage", 1)
    stage_n   = {1: "Baby", 2: "Juvenile", 3: "Adult"}.get(stage, "?")

    now   = int(time.time())
    age_s = now - state["born_at"]
    age_h = age_s // 3600
    age_m = (age_s % 3600) // 60

    caretakers = state.get("caretakers", {})
    sorted_ct  = sorted(caretakers.items(), key=lambda x: x[1], reverse=True)

    lines = [
        _render(state),
        f"Age: {age_h}h {age_m}m",
        "--- Top Caretakers ---",
    ]
    if sorted_ct:
        for i, (cs, count) in enumerate(sorted_ct[:5], 1):
            lines.append(f" {i}. {cs}: {count} action(s)")
    else:
        lines.append("  No caretakers yet — step up!")
    return {"text": "\n".join(lines), "fc": fc}


def _cmd_help(fc):
    lines = [
        "=== Pacagotchi Commands ===",
        "/spawn        - Create a new pet",
        "/feed [food]  - Feed it (add junk food name for junk)",
        "/play         - Play with pet (increases happiness)",
        "/clean        - Clean up poop",
        "/medicate     - Cure illness",
        "/sleep        - Put it to sleep (4x slower stat drops, auto after 3h idle)",
        "/pet          - Show pet status",
        "/stats        - Full stats + top caretakers",
        "/help         - This help text",
        "Tip: too much junk food = illness!",
    ]
    return {"text": "\n".join(lines), "fc": fc}

# ---------------------------------------------------------------------------
# Init and background thread startup
# ---------------------------------------------------------------------------

def init(db_connection):
    """Initialise the pacagotchi DB table. Call once at WPS startup."""
    cursor = db_connection.cursor()
    _init_table(cursor)


def start_tick_thread(db_connection, broadcast_fn, channel_id):
    """
    Start the background state-update thread.
    broadcast_fn(cursor, cid, text, fc) — provided by wps.py.
    """
    def _loop():
        while True:
            time.sleep(TICK_INTERVAL)
            try:
                cursor = db_connection.cursor()
                state  = _load(cursor)
                fc     = state.get("name", "PACBOT") if state else "PACBOT"
                tick(cursor, broadcast_fn, channel_id, fc)
            except Exception as exc:
                db_logger("PACAGOTCHI TICK", f"Tick error: {exc}", "ERROR")

    t = threading.Thread(target=_loop, daemon=True, name="pacagotchi_tick")
    t.start()
    return t
