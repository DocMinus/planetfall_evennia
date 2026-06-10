"""
Standalone interrupt routines registered at game start.

ZIL source reference (misc.zil:76-81 — initial QUEUE calls):
  ENABLE <QUEUE I-BLATHER -1>
  ENABLE <QUEUE I-AMBASSADOR -1>
  ENABLE <QUEUE I-RANDOM-INTERRUPTS 1>
  ENABLE <QUEUE I-SLEEP-WARNINGS 3600>
  ENABLE <QUEUE I-HUNGER-WARNINGS 2000>
  ENABLE <QUEUE I-SICKNESS-WARNINGS 1000>

Routines with more than a couple of state variables should become their own
class (like FloydNPC). The ones here are thin enough to stay as functions
that close over GameState.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.state import GameState


def register_all(gs: GameState) -> None:
    """Register the initial interrupt set. Call once at game start."""
    s = gs.scheduler
    s.enable(s.queue(make_i_blather(gs),              -1))
    s.enable(s.queue(make_i_ambassador(gs),            -1))
    s.enable(s.queue(make_i_random_interrupts(gs),      1))
    s.enable(s.queue(make_i_sleep_warnings(gs),      3600))
    s.enable(s.queue(make_i_hunger_warnings(gs),     2000))
    s.enable(s.queue(make_i_sickness_warnings(gs),   1000))


# ---------------------------------------------------------------------------
# I-BLATHER  (globals.zil:680)
# Every-turn ensign harassment / brig drag. Stub until room/actor model exists.
# ---------------------------------------------------------------------------

def make_i_blather(gs: GameState):
    def i_blather() -> bool:
        # TODO: implement ensign harassment logic
        return False
    i_blather.__name__ = "i_blather"
    return i_blather


# ---------------------------------------------------------------------------
# I-AMBASSADOR  (globals.zil:802)
# Every-turn alien NPC interactions.
# ---------------------------------------------------------------------------

def make_i_ambassador(gs: GameState):
    def i_ambassador() -> bool:
        # TODO: implement ambassador interaction logic
        return False
    i_ambassador.__name__ = "i_ambassador"
    return i_ambassador


# ---------------------------------------------------------------------------
# I-RANDOM-INTERRUPTS  (misc.zil:109)
# One-shot: queues ship explosion and comm puzzle after 1 tick.
# ---------------------------------------------------------------------------

def make_i_random_interrupts(gs: GameState):
    def i_random_interrupts() -> bool:
        # Queue the ship explosion countdown (5 stages, globals.zil:1162)
        gs.scheduler.enable(gs.scheduler.queue(make_i_blowup_feinstein(gs), 100))
        # TODO: queue comm puzzle interrupt
        return False
    i_random_interrupts.__name__ = "i_random_interrupts"
    return i_random_interrupts


# ---------------------------------------------------------------------------
# I-BLOWUP-FEINSTEIN  (globals.zil:1162)
# 5-stage ship destruction countdown. Carries its own stage counter.
# ---------------------------------------------------------------------------

def make_i_blowup_feinstein(gs: GameState):
    state = {"stage": 0}

    MESSAGES = [
        "A distant rumble shakes the ship.",
        "The lights flicker. Something structural is giving way.",
        "Fire and smoke pour from a nearby corridor.",
        "The hull groans as explosive decompression begins.",
        None,  # stage 5: game over
    ]

    def i_blowup_feinstein() -> bool:
        stage = state["stage"]
        if stage < 4:
            msg = MESSAGES[stage]
            if msg:
                # TODO: wire up to output system
                pass
            state["stage"] += 1
            gs.scheduler.enable(gs.scheduler.queue(i_blowup_feinstein, 150))
        else:
            # TODO: trigger game-over / fatal condition
            return True
        return False

    i_blowup_feinstein.__name__ = "i_blowup_feinstein"
    return i_blowup_feinstein


# ---------------------------------------------------------------------------
# I-SLEEP-WARNINGS  (globals.zil:2026)
# 5-level fatigue escalation. Fires at 3600 ticks, re-queues each stage.
# ---------------------------------------------------------------------------

def make_i_sleep_warnings(gs: GameState):
    state = {"level": 0}

    MESSAGES = [
        "You feel a little drowsy.",
        "You are quite tired.",
        "You can barely keep your eyes open.",
        "You collapse from exhaustion.",
        None,  # fatal
    ]

    def i_sleep_warnings() -> bool:
        level = state["level"]
        if level < len(MESSAGES):
            msg = MESSAGES[level]
            if msg:
                pass  # TODO: wire up to output system
            state["level"] += 1
            if level < len(MESSAGES) - 1:
                gs.scheduler.enable(gs.scheduler.queue(i_sleep_warnings, 600))
            else:
                return True  # fatal
        return False

    i_sleep_warnings.__name__ = "i_sleep_warnings"
    return i_sleep_warnings


# ---------------------------------------------------------------------------
# I-HUNGER-WARNINGS  (globals.zil:2295)
# ---------------------------------------------------------------------------

def make_i_hunger_warnings(gs: GameState):
    state = {"level": 0}

    MESSAGES = [
        "Your stomach growls.",
        "You are getting quite hungry.",
        "You are ravenously hungry.",
        "You faint from hunger.",
        None,
    ]

    def i_hunger_warnings() -> bool:
        level = state["level"]
        if level < len(MESSAGES):
            msg = MESSAGES[level]
            if msg:
                pass  # TODO: wire up to output system
            state["level"] += 1
            if level < len(MESSAGES) - 1:
                gs.scheduler.enable(gs.scheduler.queue(i_hunger_warnings, 500))
            else:
                return True
        return False

    i_hunger_warnings.__name__ = "i_hunger_warnings"
    return i_hunger_warnings


# ---------------------------------------------------------------------------
# I-SICKNESS-WARNINGS  (globals.zil:2330)
# ---------------------------------------------------------------------------

def make_i_sickness_warnings(gs: GameState):
    state = {"level": 0}

    MESSAGES = [
        "You feel slightly nauseous.",
        "The radiation sickness is getting worse.",
        "You are very ill.",
        "You die from radiation poisoning.",
        None,
    ]

    def i_sickness_warnings() -> bool:
        level = state["level"]
        if level < len(MESSAGES):
            msg = MESSAGES[level]
            if msg:
                pass  # TODO: wire up to output system
            state["level"] += 1
            if level < len(MESSAGES) - 1:
                gs.scheduler.enable(gs.scheduler.queue(i_sickness_warnings, 400))
            else:
                return True
        return False

    i_sickness_warnings.__name__ = "i_sickness_warnings"
    return i_sickness_warnings
