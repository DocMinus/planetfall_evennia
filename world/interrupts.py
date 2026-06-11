"""
Standalone interrupt routines registered at game start.

ZIL source reference (misc.zil:76-81 — initial QUEUE calls):
  ENABLE <QUEUE I-BLATHER -1>
  ENABLE <QUEUE I-AMBASSADOR -1>
  ENABLE <QUEUE I-RANDOM-INTERRUPTS 1>
  ENABLE <QUEUE I-SLEEP-WARNINGS 3600>
  ENABLE <QUEUE I-HUNGER-WARNINGS 2000>
  ENABLE <QUEUE I-SICKNESS-WARNINGS 1000>

Each maker function accepts an optional `state` dict so the GameStateScript
can restore interrupt-local state after a server reload.  The returned
closure carries a `_state` attribute pointing at that dict so the script
can serialise it back to db.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from world.state import GameState


def _send(gs, msg: str) -> None:
    """Send a message to the player if one is connected."""
    if gs.player:
        gs.player.msg(msg)


def register_all(gs: "GameState") -> None:
    """Register the initial interrupt set. Call once on a fresh game."""
    s = gs.scheduler
    s.enable(s.queue(make_i_blather(gs),              -1))
    s.enable(s.queue(make_i_ambassador(gs),            -1))
    s.enable(s.queue(make_i_random_interrupts(gs),      1))
    s.enable(s.queue(make_i_sleep_warnings(gs),      3600))
    s.enable(s.queue(make_i_hunger_warnings(gs),     2000))
    s.enable(s.queue(make_i_sickness_warnings(gs),   1000))


# ---------------------------------------------------------------------------
# I-BLATHER  (globals.zil:680)
# Every-turn ensign harassment / brig drag.
# ---------------------------------------------------------------------------

def make_i_blather(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {}

    def i_blather() -> bool:
        # TODO: implement ensign harassment logic
        return False

    i_blather.__name__ = "i_blather"
    i_blather._state = state
    return i_blather


# ---------------------------------------------------------------------------
# I-AMBASSADOR  (globals.zil:802)
# Every-turn alien NPC interactions.
# ---------------------------------------------------------------------------

def make_i_ambassador(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {}

    def i_ambassador() -> bool:
        # TODO: implement ambassador interaction logic
        return False

    i_ambassador.__name__ = "i_ambassador"
    i_ambassador._state = state
    return i_ambassador


# ---------------------------------------------------------------------------
# I-RANDOM-INTERRUPTS  (misc.zil:109)
# One-shot: queues ship explosion and comm puzzle after 1 tick.
# ---------------------------------------------------------------------------

def make_i_random_interrupts(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {}

    def i_random_interrupts() -> bool:
        gs.scheduler.enable(gs.scheduler.queue(make_i_blowup_feinstein(gs), 100))
        # TODO: queue comm puzzle interrupt
        return False

    i_random_interrupts.__name__ = "i_random_interrupts"
    i_random_interrupts._state = state
    return i_random_interrupts


# ---------------------------------------------------------------------------
# I-BLOWUP-FEINSTEIN  (globals.zil:1162)
# 5-stage ship destruction countdown. Carries its own stage counter.
# ---------------------------------------------------------------------------

_BLOWUP_MESSAGES = [
    "A distant rumble shakes the ship.",
    "The lights flicker. Something structural is giving way.",
    "Fire and smoke pour from a nearby corridor.",
    "The hull groans as explosive decompression begins.",
    None,  # stage 4: game over
]


def make_i_blowup_feinstein(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {"stage": 0}

    def i_blowup_feinstein() -> bool:
        stage = state["stage"]
        if stage < len(_BLOWUP_MESSAGES) - 1:
            msg = _BLOWUP_MESSAGES[stage]
            if msg:
                _send(gs, msg)
            state["stage"] += 1
            gs.scheduler.enable(gs.scheduler.queue(i_blowup_feinstein, 150))
        else:
            _send(gs, "The Feinstein tears apart around you.  Everything goes dark.")
            return True
        return False

    i_blowup_feinstein.__name__ = "i_blowup_feinstein"
    i_blowup_feinstein._state = state
    return i_blowup_feinstein


# ---------------------------------------------------------------------------
# I-SLEEP-WARNINGS  (globals.zil:2026)
# 5-level fatigue escalation.
# ---------------------------------------------------------------------------

_SLEEP_MESSAGES = [
    "You feel a little drowsy.",
    "You are quite tired.",
    "You can barely keep your eyes open.",
    "You collapse from exhaustion.",
    None,  # fatal
]


def make_i_sleep_warnings(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {"level": 0}

    def i_sleep_warnings() -> bool:
        level = state["level"]
        if level < len(_SLEEP_MESSAGES):
            msg = _SLEEP_MESSAGES[level]
            if msg:
                _send(gs, msg)
            state["level"] += 1
            if level < len(_SLEEP_MESSAGES) - 1:
                gs.scheduler.enable(gs.scheduler.queue(i_sleep_warnings, 600))
            else:
                return True
        return False

    i_sleep_warnings.__name__ = "i_sleep_warnings"
    i_sleep_warnings._state = state
    return i_sleep_warnings


# ---------------------------------------------------------------------------
# I-HUNGER-WARNINGS  (globals.zil:2295)
# ---------------------------------------------------------------------------

_HUNGER_MESSAGES = [
    "Your stomach growls.",
    "You are getting quite hungry.",
    "You are ravenously hungry.",
    "You faint from hunger.",
    None,
]


def make_i_hunger_warnings(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {"level": 0}

    def i_hunger_warnings() -> bool:
        level = state["level"]
        if level < len(_HUNGER_MESSAGES):
            msg = _HUNGER_MESSAGES[level]
            if msg:
                _send(gs, msg)
            state["level"] += 1
            if level < len(_HUNGER_MESSAGES) - 1:
                gs.scheduler.enable(gs.scheduler.queue(i_hunger_warnings, 500))
            else:
                return True
        return False

    i_hunger_warnings.__name__ = "i_hunger_warnings"
    i_hunger_warnings._state = state
    return i_hunger_warnings


# ---------------------------------------------------------------------------
# I-SICKNESS-WARNINGS  (globals.zil:2330)
# ---------------------------------------------------------------------------

_SICKNESS_MESSAGES = [
    "You feel slightly nauseous.",
    "The radiation sickness is getting worse.",
    "You are very ill.",
    "You die from radiation poisoning.",
    None,
]


def make_i_sickness_warnings(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {"level": 0}

    def i_sickness_warnings() -> bool:
        level = state["level"]
        if level < len(_SICKNESS_MESSAGES):
            msg = _SICKNESS_MESSAGES[level]
            if msg:
                _send(gs, msg)
            state["level"] += 1
            if level < len(_SICKNESS_MESSAGES) - 1:
                gs.scheduler.enable(gs.scheduler.queue(i_sickness_warnings, 400))
            else:
                return True
        return False

    i_sickness_warnings.__name__ = "i_sickness_warnings"
    i_sickness_warnings._state = state
    return i_sickness_warnings


# ---------------------------------------------------------------------------
# Elevator-arrival demons (compone.zil:2570 / 2580)
# Queued on demand by the lobby call-buttons; one-shot — the scheduler drops
# them after they fire. Each opens its shaft door and flips the car-position
# flag the PER routines read.
# ---------------------------------------------------------------------------

def _by_zil_id(zil_id: str):
    from evennia.utils.search import search_object_attribute
    results = search_object_attribute(key="zil_id", strvalue=zil_id)
    return results[0] if results else None


def _player_in(gs, room_zil_id: str) -> bool:
    p = gs.player
    return bool(p and p.location and p.location.db.zil_id == room_zil_id)


def make_i_upper_elevator_arrive(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {}

    def i_upper_elevator_arrive() -> bool:
        door = _by_zil_id("UPPER-ELEVATOR-DOOR")
        if door:
            door.db.is_open = True
        gs.flags["UPPER-ELEVATOR-UP"] = False
        if _player_in(gs, "ELEVATOR-LOBBY"):
            _send(gs, "\nThe door at the north end of the room slides open.")
        return False

    i_upper_elevator_arrive.__name__ = "i_upper_elevator_arrive"
    i_upper_elevator_arrive._state = state
    return i_upper_elevator_arrive


def make_i_lower_elevator_arrive(gs: "GameState", state: dict | None = None):
    state = state if state is not None else {}

    def i_lower_elevator_arrive() -> bool:
        door = _by_zil_id("LOWER-ELEVATOR-DOOR")
        if door:
            door.db.is_open = True
        gs.flags["LOWER-ELEVATOR-UP"] = True
        if _player_in(gs, "ELEVATOR-LOBBY"):
            _send(gs, "\nThe door at the south end of the room slides open.")
        return False

    i_lower_elevator_arrive.__name__ = "i_lower_elevator_arrive"
    i_lower_elevator_arrive._state = state
    return i_lower_elevator_arrive
