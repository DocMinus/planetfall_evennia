# Planetfall — Evennia Port

An unofficial fan port of **Planetfall** (1983, Infocom) to
[Evennia](https://www.evennia.com) 6.0, a Python/Django MUD framework.
The game world (105 rooms, 137 objects, 8 NPCs, 200+ verb patterns) is
derived from the original ZIL source code via a custom extraction pipeline.

> **Single-player only.** Evennia is a multiplayer framework, but this port
> runs as a local single-player game: one persistent player in one shared
> world. It does not support concurrent multiplayer.

---

## Quick Start

Requires Python 3.12 and [uv](https://github.com/astral-sh/uv).

```bash
uv sync         # install dependencies
make rebuild    # build the game database
make play       # start the server and open the game in your browser
```

The game opens at `http://localhost:4001/` on the PLANETFALL title screen —
press **RETURN** to begin. Progress persists between sessions. To stop the
server: `make stop`.

See [INSTALL.md](INSTALL.md) for full setup, Makefile targets, tests, and
development notes.

---

## Resources: Maps and Walkthroughs

Maps and walkthroughs collected from around the web are in [planetfall_resources/](planetfall_resources/), including a walkthrough adapted for this port: [WALKTHROUGH_this_port.txt](planetfall_resources/WALKTHROUGH_this_port.txt).

---

## Playing

| Command                          | Effect                            |
| -------------------------------- | --------------------------------- |
| `look` / `l`                     | Describe the current room         |
| `examine <thing>`                | Examine an object or NPC          |
| `inventory` / `i`                | List what you're carrying         |
| `take <thing>` / `drop <thing>`  | Pick up / drop an object          |
| `open <thing>` / `close <thing>` | Open or close a door or container |
| `put <thing> in <container>`     | Put an object inside another      |
| `read <thing>`                   | Read an object's text             |
| `wait` / `z`                     | Pass a turn                       |
| `score` / `diagnose`             | Show your score / health          |
| `north` / `n`, `south` / `s` …   | Move in a direction               |
| `again` / `g`                    | Repeat the last command           |
| `save` / `restore` / `restart`   | As in the original game           |

Synonyms from the original game work, as do multi-object forms
(`drop all except kit`) and orders to Floyd (`floyd, go north`).

Stuck? [WALKTHROUGH_this_port.txt](planetfall_resources/WALKTHROUGH_this_port.txt)
is a compact command-by-command solution (adapted from Paul D. Smith's
classic walkthrough).

---

## Credits and Licences

**Planetfall** was written by **Steve Meretzky** and published by **Infocom** in
1983; the intellectual property is held by **Activision Blizzard**. The ZIL
source used as reference was made public in the
[Infocom Cabinet](https://github.com/historicalsource) archive (Jason Scott and
the Internet Archive, 2019). This project is an **unofficial, non-commercial fan
port** for educational and preservation purposes and makes no claim to the
original intellectual property. If you own the rights and have concerns, please
open an issue.

Built on [Evennia](https://github.com/evennia/evennia) 6.0.0, © the Evennia
contributors, **BSD 3-Clause Licence**.

The Python code in this repository is released under the **MIT Licence** — see
`LICENSE` if present, otherwise treat it as freely usable for non-commercial
purposes with attribution.
