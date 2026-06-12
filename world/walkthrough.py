"""Canonical end-to-end walkthrough harness (Phase 14a.1).

Drives a fresh game from DECK-NINE to the winning ending using **only real
typed commands**, exercising the actual path: pattern matcher, ``resolve_obj``
in real rooms, PRSO/PRSI dispatch, exit wiring / ``at_traverse``, the per-turn
clock + M-END demon, and interrupts firing during play.

This module is the single source of truth for the walkthrough. It is consumed
two ways:

  * **Dev / discovery** — against the live dev DB::

        .venv/bin/evennia shell -c "from world.walkthrough import dev_run; dev_run()"

  * **Regression test** (Phase 14c.2) — an ``EvenniaTest`` that loads the world
    and replays :data:`WALKTHROUGH`, asserting the milestones.

The game's opening (and a few puzzles) use ``RANDOM``; :func:`run` seeds Python's
``random`` so a fixed command list is reproducible. Steps that wait on a player
reaction to a timed event (the Feinstein explosion) are modelled as
:class:`WaitUntil` — still only typed ``wait`` commands, but repeated until the
world reacts, exactly as a human plays.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

# Deterministic seed so RANDOM-driven timing (blowup delay, laser shot counts,
# the safe combination) is reproducible across runs.
SEED = 1983


# ---------------------------------------------------------------------------
# Step types
# ---------------------------------------------------------------------------

@dataclass
class Cmd:
    """A single typed command plus optional assertions on its output / state."""
    text: str
    # substrings that must ALL appear in the captured output of this command
    expect: tuple[str, ...] = ()
    # substrings that must NOT appear (e.g. "isn't implemented", "can't see")
    forbid: tuple[str, ...] = ()
    # optional predicate(gs) -> bool checked after the command
    check: Callable | None = None
    note: str = ""


@dataclass
class WaitUntil:
    """Issue ``wait`` repeatedly until ``predicate(gs)`` is true (player reaction)."""
    predicate: Callable
    max_waits: int = 40
    note: str = ""


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class StepError(AssertionError):
    pass


class Driver:
    """Executes a step list against a Character, capturing per-command output."""

    def __init__(self, char, gs, verbose=True):
        self.char = char
        self.gs = gs
        self.verbose = verbose
        self.log: list[str] = []
        self._buf: list[str] = []
        # Capture everything sent to the character.
        orig_msg = char.msg

        def _capture(text=None, **kwargs):
            if text is not None:
                s = text[0] if isinstance(text, tuple) else text
                self._buf.append(str(s))
            return orig_msg(text, **kwargs) if False else None
        char.msg = _capture

    def _emit(self, cmd_text):
        self._buf = []
        self.char.execute_cmd(cmd_text)
        out = "\n".join(self._buf)
        self.log.append(f"> {cmd_text}\n{out}")
        if self.verbose:
            print(f"\n> {cmd_text}\n{out}")
        return out

    def run_step(self, step):
        if isinstance(step, WaitUntil):
            n = 0
            while not step.predicate(self.gs) and n < step.max_waits:
                self._emit("wait")
                n += 1
            if not step.predicate(self.gs):
                raise StepError(f"WaitUntil exhausted after {n} waits: {step.note}")
            return
        out = self._emit(step.text)
        low = out.lower()
        for sub in step.expect:
            if sub.lower() not in low:
                raise StepError(
                    f"'{step.text}': expected {sub!r} in output.\n--- got ---\n{out}"
                )
        for sub in step.forbid:
            if sub.lower() in low:
                raise StepError(
                    f"'{step.text}': forbidden {sub!r} in output.\n--- got ---\n{out}"
                )
        if step.check and not step.check(self.gs):
            raise StepError(f"'{step.text}': post-condition check failed. {step.note}")

    def run(self, steps):
        for i, step in enumerate(steps):
            try:
                self.run_step(step)
            except StepError as e:
                print(f"\n*** STEP {i} FAILED ***\n{e}")
                raise


# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

def _by_zil(zil_id):
    from evennia.utils.search import search_object_attribute
    res = search_object_attribute(key="zil_id", value=zil_id)
    return res[0] if res else None


def fresh_game(char_key="WalkthroughTester"):
    """Reset to a clean game: fresh GameState, test Character at DECK-NINE.

    Returns (char, gs). Reuses an existing tester character if present.
    """
    random.seed(SEED)
    from evennia import create_object
    from typeclasses.characters import Character
    from world.scheduler_script import get_game_state_script

    # Wipe and recreate the game_state singleton for a deterministic start.
    script = get_game_state_script()
    if script is not None:
        script.delete()
    from world.scheduler_script import get_or_create_game_state_script
    script = get_or_create_game_state_script()
    gs = script.get_gs()

    deck_nine = _by_zil("DECK-NINE")
    if deck_nine is None:
        raise RuntimeError("World not loaded: DECK-NINE missing. Run load_world().")

    # Find or create the throwaway tester.
    from evennia.utils.search import search_object
    existing = [o for o in search_object(char_key) if o.is_typeclass(Character, exact=False)]
    char = existing[0] if existing else create_object(Character, key=char_key)
    # Clear any leftover inventory from a prior run.
    for item in list(char.contents):
        item.delete()
    char.location = deck_nine
    char.db.starter_items_given = False
    char._give_starter_items()
    char.db.starter_items_given = True

    gs.player = char
    return char, gs


# ---------------------------------------------------------------------------
# The walkthrough (built up incrementally as 14a.1 progresses)
# ---------------------------------------------------------------------------

def _door_open(zil_id):
    d = _by_zil(zil_id)
    return bool(d and d.db.is_open)


# Milestone 1: escape the doomed Feinstein in the pod.
ESCAPE_POD = [
    Cmd("look", expect=("Deck Nine",), note="confirm starting room"),
    WaitUntil(lambda gs: _door_open("POD-DOOR"),
              note="wait for the explosion to blow the port pod door open"),
    Cmd("west", expect=("Escape Pod",), note="into the pod"),
    Cmd("sit in web", note="strap into the safety web"),
    WaitUntil(lambda gs: gs.flags.get("BLOWUP-COUNTER", 0) >= 5,
              note="ride out the ejection"),
    WaitUntil(lambda gs: gs.flags.get("TRIP-COUNTER", 0) > 14,
              note="ride the pod down until it lands"),
    Cmd("take towel", note="grab survival provisions"),
    Cmd("take survival kit"),
    Cmd("stand", expect=("water rising",), note="standing starts the pod sinking"),
    Cmd("open pod door"),
    Cmd("out", expect=("Underwater",), note="swim out before it sinks"),
    Cmd("up", expect=("Crag",), note="surface onto the crag"),
    Cmd("up", expect=("Balcony",)),
    Cmd("up", expect=("Winding Stair",)),
    Cmd("up", expect=("Courtyard",), note="arrive at the deserted complex"),
]

WALKTHROUGH: list = list(ESCAPE_POD)


def dev_run(steps=None, verbose=True):
    """Run the walkthrough against the live dev DB (discovery / smoke)."""
    char, gs = fresh_game()
    drv = Driver(char, gs, verbose=verbose)
    drv.run(steps if steps is not None else WALKTHROUGH)
    print(f"\n=== reached: day={gs.day} score={gs.score} "
          f"moves={gs.internal_moves} room={getattr(char.location,'db',None) and char.location.db.zil_id} "
          f"dead={gs.dead} ===")
    return drv
