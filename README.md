# Planetfall — Evennia Port

An unofficial fan port of **Planetfall** (1983, Infocom) to
[Evennia](https://www.evennia.com) 6.0, a Python/Django MUD framework.
The game world (105 rooms, 137 objects, 8 NPCs, 200+ verb patterns) is
derived from the original ZIL source code via a custom extraction pipeline.

> **Status:** Phases 0–14 complete — the game is playable end to end, from the
> Feinstein's destruction (escape-pod sequence) through the planet-side puzzles
> to the winning cryo endgame. The clock/interrupt engine, Floyd, scoring,
> AGAIN/SAVE-RESTORE, and all conditional/transport exits are faithful, and the
> Phase 13 puzzle subsystems are in: a uniform death/JIGS-UP + win path, the
> generic verbs, the brig escape, card-slot access, survival (sleep/food/
> sickness), the shuttle driving sim, the reactor repair (Defense + Course
> Control + Comm chemistry + laser/miniaturization + the strip microbe battle),
> the cryo endgame, teleport booths, the Floyd sacrifice, the lab mutant chase,
> and scenery flavour. Every verb in the syntax resolves to a faithful response.
>
> **Phase 14 (hardening/validation/polish)** adds a committed end-to-end
> regression suite: a scripted walkthrough plays the game with real typed
> commands from the opening pod to a **perfect 80/80 victory** (Galactic
> Overlord, Floyd revived), and `make test` (10 tests) replays it plus
> save/restore round-trips, hyphen-token resolution, `take all`, the timed flood
> drain, and the microbe battle. A one-command `make rebuild` recovers a wiped
> dev DB from scratch. The only remaining work is the single-player front-end
> (Phase 15), tracked in `EVENNIA_PLAN_5.md`.

---

## Prerequisites

- Python 3.12
- [uv](https://github.com/astral-sh/uv) (used for the virtual environment)

---

## Setup

```bash
# 1. Create the virtual environment and install dependencies
uv sync

# 2. Apply database migrations (creates the SQLite DB and schema)
make migrate

# 3. Start the server.
#    On first run: prompts to create an admin account, then loads the game world.
make start
```

To stop the server: `make stop`. To resume later: `make start` again.
Note: `make restart` is currently unreliable — use `make stop` then `make start`.

The server starts two listeners:
- **MUD client** — `localhost:4000` (telnet / any MUD client)
- **Web client** — `http://localhost:4001` (browser, no client needed)

---

## Starting a Game

1. Connect to `localhost:4001` in your browser (or a MUD client at port 4000).
2. Log in with the superuser account you created. The current development DB
   ships with a superuser **`superuser`** / password **`planetfall123`** —
   change it (`@password`) or create your own; this is a throwaway dev credential.
3. Teleport to the starting room:
   ```
   @tel/quiet DECK-NINE
   ```
4. Type `look` to see where you are.

---

## Basic Commands

| Command | Effect |
|---------|--------|
| `look` / `l` | Describe the current room |
| `examine <thing>` | Examine an object or NPC |
| `inventory` / `i` | List what you're carrying |
| `take <thing>` | Pick up an object |
| `drop <thing>` | Drop from inventory |
| `open <thing>` | Open a door or container |
| `close <thing>` | Close it |
| `put <thing> in <container>` | Put an object inside another |
| `read <thing>` | Read an object's text |
| `wear <thing>` | Wear clothing |
| `look in <container>` | Look inside a container |
| `wait` / `z` | Pass a turn |
| `score` | Show your current score |
| `diagnose` | Show your health status |
| `north` / `n`, `south` / `s` … | Move in a direction |
| `go <direction>` / `walk <direction>` | Same as the bare direction |
| `again` / `g` | Repeat the last command |
| `quit` / `q` | Disconnect |

Synonyms from the original game are wired in — `get` works for `take`,
`kill` for `attack`, `i` for `inventory`, etc.

---

## Makefile Targets

```bash
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
movement → clock/interrupt firing → scoring → endgame). The suite (10 tests)
also covers SAVE/RESTORE round-trips (including a live timed demon mid-countdown),
hyphen-token object resolution, `take all`, the timed fungicide flood drain, and
the strip microbe battle. Budget ~35s — the walkthrough drives several thousand
internal moves.

---

## Project Structure

```
planetfall_evennia/
  commands/
    command.py            # PlanetfallCommand base class; clock advance hook
    default_cmdsets.py    # CharacterCmdSet; registers all 99 verb commands
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
  server/conf/
    settings.py           # Evennia settings
```

---

## How the Clock Works

Every command that is not marked `timeless` advances the game clock. Most
actions cost 7 ticks (Planetfall's default), but a **successful move costs 20**
(`DEFAULT-MOVE`); a few verbs and the transit routines (shuttle, long hall,
ladder) set their own cost. The `GameStateScript` Evennia `Script` owns a
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

---

## Credits and Licences

### Planetfall (original game)

**Planetfall** was written by **Steve Meretzky** and published by **Infocom** in 1983.
The intellectual property is currently held by **Activision Blizzard**.

The ZIL (Zork Implementation Language) source code used as reference for this port
was made publicly available as part of the
[Infocom Cabinet](https://github.com/historicalsource) archive hosted by
Jason Scott and the Internet Archive (2019). The copyright to Planetfall and
its source code remains with Activision Blizzard. This project is an
**unofficial, non-commercial fan port** created for educational and preservation
purposes. It makes no claim to the original intellectual property.

If you own the rights and have concerns, please open an issue.

### Evennia

This project is built on [Evennia](https://www.evennia.com) 6.0.0, a Python/Django
framework for online text games.

Evennia is copyright © the Evennia contributors and is released under the
**BSD 3-Clause Licence**.
Source: https://github.com/evennia/evennia

### This Port

The Python code in this repository (loader, scheduler, Floyd logic, command
system) was written for this project. It is released under the **MIT Licence**
— see `LICENSE` if present, otherwise treat it as freely usable for
non-commercial purposes with attribution.
