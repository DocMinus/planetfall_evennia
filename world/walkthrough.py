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


def reload_world():
    """Wipe every Planetfall room/object/NPC and reload pristine from JSON.

    The harness must start from a known-good world each run; live probing
    mutates persistent objects (Floyd's flags, door OPENBITs, item locations),
    so a deterministic walkthrough needs a clean rebuild, not just a fresh
    GameState. The dev DB's world is fully regenerable from world/data/*.json.
    """
    from evennia.objects.models import ObjectDB
    from typeclasses.rooms import PlanetfallRoom
    from typeclasses.objects import PlanetfallObject
    from typeclasses.characters import PlanetfallNPC
    paths = {PlanetfallRoom.path, PlanetfallObject.path, PlanetfallNPC.path}
    # Subclasses (Character extends PlanetfallNPC) share the NPC schema; match
    # on any object carrying a zil_id or our tester key, plus the typeclasses.
    for obj in ObjectDB.objects.all():
        if obj.db_typeclass_path in paths or str(obj.db_typeclass_path).endswith("characters.Character"):
            obj.delete()
    from world.loader import load_world
    load_world()


def fresh_game(char_key="WalkthroughTester", reload=True):
    """Reset to a clean game: fresh world + GameState, tester at DECK-NINE.

    Returns (char, gs). With ``reload=True`` (default) the whole world is
    rebuilt from JSON for determinism; pass ``reload=False`` to reuse the
    current DB (faster, but only safe if nothing has been mutated).
    """
    random.seed(SEED)
    from evennia import create_object
    from typeclasses.characters import Character
    from world.scheduler_script import get_game_state_script, get_or_create_game_state_script

    if reload:
        reload_world()

    # Wipe and recreate the game_state singleton for a deterministic start.
    script = get_game_state_script()
    if script is not None:
        script.delete()
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

# Milestone 2: descend through the long hall to the mechanical level and
# activate Floyd in the robot shop (+2, the first scoring event).
DESCEND_AND_FLOYD = [
    Cmd("north", expect=("Plain Hall",)),
    Cmd("north", expect=("Rec Area",)),
    Cmd("east", expect=("Rec Corridor",)),
    Cmd("east", expect=("Mess Corridor",)),
    Cmd("east", expect=("Dorm Corridor",)),
    Cmd("east", expect=("Corridor Junction",), note="long-hall transit"),
    Cmd("south", expect=("Mech Corridor North",)),
    Cmd("south", expect=("Mech Corridor",)),
    Cmd("south", expect=("Mech Corridor South",)),
    Cmd("southeast", expect=("Robot Shop",)),
    Cmd("turn on robot", expect=("Floyd's name is Floyd",),
        check=lambda gs: gs.score == 2, note="Floyd activation awards +2"),
]

def _carries(char, *zil_ids):
    have = {o.db.zil_id for o in char.contents}
    have |= {o.db.zil_id for c in char.contents for o in c.contents}  # one level in
    return all(z in have for z in zil_ids)


# Milestone 3: collect the workshop tools, then the brig/access puzzle —
# fish the key out of the crevice with the magnet, unlock & remove the
# padlock, fetch the ladder, span the rift, and cross to the offices to take
# the upper-elevator and kitchen access cards.
TOOLS_AND_CARDS = [
    # Workshop tools (laser, magnet, pliers, flask) needed downstream.
    Cmd("northwest", expect=("Mech Corridor South",)),
    Cmd("southwest", expect=("Tool Room",)),
    Cmd("take magnet"), Cmd("take pliers"), Cmd("take laser"), Cmd("take flask"),
    # To the crevice and fish out the key with the magnet.
    Cmd("northeast", expect=("Mech Corridor South",)),
    Cmd("north", expect=("Mech Corridor",)),
    Cmd("north", expect=("Mech Corridor North",)),
    Cmd("north", expect=("Corridor Junction",)),
    Cmd("north", expect=("Admin Corridor South",)),
    Cmd("search crevice", expect=("shiny steel key",)),
    Cmd("get key with magnet", expect=("magnet attracts the key",)),
    # Drop the magnet here: a carried magnet scrambles any held access card
    # (I-MAGNET), so it must not travel with the cards. Retrieved later for
    # the relay speck.
    Cmd("drop magnet"),
    # Back to the padlock; unlock, remove, open the storage door, get the ladder.
    Cmd("south", expect=("Corridor Junction",)),
    Cmd("west", expect=("Dorm Corridor",)),
    Cmd("west", expect=("Mess Corridor",)),
    Cmd("unlock padlock with key", expect=("padlock springs open",)),
    Cmd("take padlock", check=lambda gs: gs.flags.get("PADLOCK-REMOVED")),
    Cmd("open storage door", expect=("Opened",)),
    Cmd("north", expect=("Storage West",)),
    Cmd("take ladder"),
    # Carry the ladder to the rift, extend it, span the gap, cross over.
    Cmd("south", expect=("Mess Corridor",)),
    Cmd("east", expect=("Dorm Corridor",)),
    Cmd("east", expect=("Corridor Junction",)),
    Cmd("north", expect=("Admin Corridor South",)),
    Cmd("north", expect=("Admin Corridor",)),
    Cmd("drop ladder"),
    Cmd("open ladder", expect=("extends to a length",)),
    Cmd("put ladder across rift", expect=("spanning the precipice",),
        check=lambda gs: gs.flags.get("LADDER-FLAG")),
    Cmd("north", expect=("Admin Corridor North",), note="cross the rift"),
    # Offices: take the elevator + kitchen cards from the small desk.
    Cmd("west", expect=("Small Office",)),
    Cmd("open small desk", expect=("Opened",)),
    # Space-separated, not "upper-elevator": a hyphenated token matches neither
    # the UPPER nor ELEVATOR adjective, tying with the carried ID-CARD on "card".
    Cmd("take upper elevator card", expect=("Taken",)),
    Cmd("take kitchen card", expect=("Taken",),
        check=lambda gs: _carries(gs.player, "UPPER-ELEVATOR-CARD", "KITCHEN-CARD")),
]

# Milestone 4: ride the upper elevator from the lobby to the tower core.
# Call the car (blue button) and wait for it to arrive, board it, enable it
# with the upper card, push Up, ride to the tower, and step out to COMM-ROOM
# (the first of the three reactor systems is up here).
UPPER_ELEVATOR = [
    Cmd("east", expect=("Admin Corridor North",), note="leave the office"),
    Cmd("south", expect=("Admin Corridor",), note="recross the rift"),
    Cmd("south", expect=("Admin Corridor South",)),
    Cmd("south", expect=("Corridor Junction",)),
    Cmd("east", expect=("Elevator Lobby",)),
    Cmd("press blue button", expect=("whirring",), note="call the upper car"),
    WaitUntil(lambda gs: _door_open("UPPER-ELEVATOR-DOOR"),
              note="wait for the upper car to arrive"),
    Cmd("north", expect=("Upper Elevator",), note="board the car"),
    Cmd("slide upper elevator card through slot", expect=("Elevator enabled",),
        check=lambda gs: gs.flags.get("UPPER-ELEVATOR-ON")),
    Cmd("push up button", expect=("sensation of vertical movement",),
        check=lambda gs: gs.flags.get("ELEVATOR-IN-TRANSIT")),
    WaitUntil(lambda gs: not gs.flags.get("ELEVATOR-IN-TRANSIT"),
              note="ride up to the tower core"),
    Cmd("out", expect=("Tower Core",),
        check=lambda gs: gs.flags.get("UPPER-ELEVATOR-UP")),
    Cmd("northeast", expect=("Comm Room",), note="reach the first reactor system"),
]

def _floyd_with(gs):
    from world.walkthrough import _by_zil
    floyd = _by_zil("FLOYD")
    return bool(floyd and gs.player and floyd.location is gs.player.location)


# Milestone 5: get the lower-elevator card and ride down to the waiting area.
# The deterministic route is to take the card from a de-activated Floyd
# (search him while off), avoiding the day-2 friendly-reveal RNG. Then ride
# the upper car back to the lobby and the lower car down to the waiting area.
# (Each push is preceded by re-sliding that car's card, which refreshes the
# timed turn-off so the trip can't be stranded by the demon.)
LOWER_ELEVATOR = [
    Cmd("turn off floyd", expect=("betrayal",)),
    Cmd("search floyd", expect=("Loowur Elavaatur",),
        check=lambda gs: gs.flags.get("CARD-STOLEN")),
    Cmd("turn on robot", note="reactivate Floyd"),
    # Ride the upper car back down to the lobby.
    Cmd("southwest", expect=("Tower Core",)),
    Cmd("north", expect=("Upper Elevator",)),
    Cmd("slide upper elevator card through slot", expect=("Elevator enabled",)),
    Cmd("push down button", expect=("vertical movement",)),
    WaitUntil(lambda gs: not gs.flags.get("ELEVATOR-IN-TRANSIT")),
    Cmd("out", expect=("Elevator Lobby",)),
    # Call, board, enable, and ride the lower car down to the waiting area.
    Cmd("press red button", expect=("vibrating",)),
    WaitUntil(lambda gs: _door_open("LOWER-ELEVATOR-DOOR"),
              note="wait for the lower car to arrive"),
    Cmd("south", expect=("Lower Elevator",)),
    Cmd("slide lower elevator card through slot", expect=("Elevator enabled",),
        check=lambda gs: gs.flags.get("LOWER-ELEVATOR-ON")),
    Cmd("push down button", expect=("vertical movement",)),
    WaitUntil(lambda gs: not gs.flags.get("ELEVATOR-IN-TRANSIT")),
    Cmd("out", expect=("Waiting Area",), check=_floyd_with,
        note="Floyd rides down with you"),
]

WALKTHROUGH: list = (list(ESCAPE_POD) + list(DESCEND_AND_FLOYD)
                     + list(TOOLS_AND_CARDS) + list(UPPER_ELEVATOR)
                     + list(LOWER_ELEVATOR))


def dev_run(steps=None, verbose=True):
    """Run the walkthrough against the live dev DB (discovery / smoke)."""
    char, gs = fresh_game()
    drv = Driver(char, gs, verbose=verbose)
    drv.run(steps if steps is not None else WALKTHROUGH)
    print(f"\n=== reached: day={gs.day} score={gs.score} "
          f"moves={gs.internal_moves} room={getattr(char.location,'db',None) and char.location.db.zil_id} "
          f"dead={gs.dead} ===")
    return drv
