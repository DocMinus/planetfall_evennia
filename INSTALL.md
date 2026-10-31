# Installation and Development

Full setup, tooling, and development notes for the Planetfall Evennia port.
For the quick version, see [README.md](README.md).

---

## Prerequisites

- Python 3.12
- [uv](https://github.com/astral-sh/uv) (used for the virtual environment)

---

## Setup

```bash
# 1. Create the virtual environment and install dependencies
uv sync

# 2. Build a fresh game database (migrate + initial setup + load the world)
make rebuild

# 3. Play: boots the server and opens the game in your browser.
make play
```

`make play` starts the server, waits for it to come online, and opens
`http://localhost:4001/` — you land directly on the PLANETFALL title screen.
Press **RETURN** to begin. To stop the server: `make stop`.

The server also accepts raw MUD clients at `localhost:4000` (telnet), though the
single-player front-end (auto-login, splash, amber theme) is the web client.

There is no login screen — the browser session auto-connects as the single
persistent player, and progress persists across sessions (close the tab and
`make play` again to resume exactly where you left off). This is a local
single-player configuration; it deliberately does **not** support concurrent
public multiplayer.

> **Dev/admin note.** The underlying account is the superuser
> **`superuser`** / **`planetfall123`** (a throwaway dev credential). Admin
> access is intentionally kept *out* of the game prompt; use the Django admin
> site at `http://localhost:4001/admin/` or `make shell` instead.

---

## Makefile Targets

```bash
make play       # Single-player launch: boot server, wait, open the game in a browser
make start      # Start the Evennia server
make stop       # Stop the server
make restart    # Restart (currently unreliable — prefer stop + start)
make shell      # Drop into an interactive Django shell
make migrate    # Apply database migrations
make load       # Load world data manually (auto-runs on first server start)
make superuser  # Create an additional admin account
make reset-game # Reset the running game to a pristine Deck Nine start
make rebuild    # Rebuild a fresh/wiped DB end to end (migrate + superuser + world)
make test       # Run the full regression suite (world/tests.py)
make test-walkthrough  # Run only the end-to-end walkthrough regression
```

`make reset-game` wipes the world + GameState and re-places your character at a
clean start (use it between playthroughs). `make rebuild` goes further: starting
from no database at all, it migrates, non-interactively creates superuser #1
(from the `DEV_USER`/`DEV_PASSWORD` make vars), runs Evennia's initial setup, and
loads the world — recovering a corrupt or deleted dev DB in one command. It backs
up any existing `server/evennia.db3` to `evennia.db3.bak` first.

---

## Tests

The regression suite lives in `world/tests.py` and runs against a fresh,
JSON-loaded world inside an in-memory test database:

```bash
make test
```

The headline test (`WalkthroughRegressionTest`) replays the canonical
`world/walkthrough.py` — a full DECK-NINE → victory playthrough driven by real
typed commands — and asserts a **perfect 80/80 win**. It is the regression net
for the entire critical path (parser → object resolution → verb dispatch →
movement → clock/interrupt firing → scoring → endgame). The suite
also covers SAVE/RESTORE round-trips (including a live timed demon mid-countdown),
hyphen-token object resolution, `take all`, the timed fungicide flood drain, and
the strip microbe battle. `WalkthroughDocTest` (`world/test_walkthrough_doc.py`)
types in the player-facing [WALKTHROUGH.txt](WALKTHROUGH.txt) verbatim and
requires it to win. Budget ~2–3 minutes — each walkthrough drives several
thousand internal moves.

---

## Project Structure

```
planetfall_evennia/
  commands/
    command.py            # PlanetfallCommand base class; clock advance hook
    default_cmdsets.py    # registers the Planetfall verbs; strips Evennia's non-game cmds
  typeclasses/
    rooms.py              # PlanetfallRoom — ZIL room fields (flags, pseudo, action)
    objects.py            # PlanetfallObject — ZIL object fields (TAKEBIT, CONTBIT, …)
    characters.py         # PlanetfallNPC + Character; starter item handout
    exits.py              # PlanetfallExit — blocked/conditional/per exit types
  world/
    data/                 # JSON world data (rooms, objects, npcs, syntax, synonyms)
    loader.py             # One-time DB loader: JSON → Evennia objects
    state.py              # GameState + Scheduler (pure Python clock engine)
    interrupts.py         # Interrupt closures: sleep/hunger warnings, blather, etc.
    floyd.py              # FloydNPC — autonomous robot companion logic
    scheduler_script.py   # Evennia Script wrapping GameState; survives server reloads
    verb_commands.py      # 99 Command classes generated from syntax.json; verb handlers
    action_handlers.py    # ROOM/OBJ action dispatch (puzzle logic per ZIL ACTION routines)
    exit_handlers.py      # PER transport routines (elevators, shuttle, pod, ladder…)
    walkthrough.py        # Scripted real-command playthrough → 80/80 victory
    tests.py              # Regression suite (replays the walkthrough; save/restore; …)
    devtools.py           # Dev helpers behind `make reset-game` / `make rebuild`
  scripts/
    reset_game.py         # `make reset-game` entry point (reset to a pristine start)
    rebuild_db.py         # `make rebuild` entry point (bootstrap a fresh DB)
    play.py               # `make play` helper: wait for the web server, open the browser
  web/                    # single-player front-end
    utils/middleware.py   # auto-authenticate the browser session as the one player
    website/urls.py       # route `/` straight to the game client (no website page)
    templates/webclient/webclient.html  # PLANETFALL title splash + panes
  server/conf/
    settings.py           # Evennia settings (single-player session config, auto-login)
    connection_screens.py # Planetfall connect banner (fallback for manual connect)
  server/.static/webclient/css/custom.css  # amber-phosphor terminal theme
```

---

## How the Clock Works

Every command that is not marked `timeless` advances the game clock. Most
actions cost 7 ticks (Planetfall's default), but a **successful move costs 20**
(`DEFAULT-MOVE`), `wait` costs 40, `examine` 32, `look` 9; every turn in the
falling escape pod costs 54, and the transit routines (shuttle, long hall,
ladder) set their own cost. Waking up resets the clock to morning. The `GameStateScript` Evennia `Script` owns a
`GameState` + `Scheduler` instance. After each command the scheduler fires any
queued interrupt routines whose countdown has reached zero — sleep warnings,
hunger messages, Floyd's idle chatter, etc.

State (clock, interrupt queue, Floyd's position) is persisted to the Evennia
database after every advance and rebuilt on server restart.

---

## Reloading / Development

```bash
make restart          # Hot-reload; picks up Python changes without resetting the DB
make shell            # Interactive shell with full Django ORM access

# Inspect the game state from the shell:
from world.scheduler_script import get_game_state_script
gs = get_game_state_script().get_gs()
print(gs.day, gs.internal_moves)

# Re-run the world loader after wiping the DB:
from world.loader import load_world
load_world()
```
