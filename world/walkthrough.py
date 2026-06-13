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
    max_waits: int = 60
    note: str = ""


@dataclass
class RepeatUntil:
    """Issue ``text`` repeatedly until ``predicate(gs)`` is true.

    For ramp-style actions a player keeps trying — e.g. lasering the relay
    speck, where marksmanship improves with every miss.
    """
    text: str
    predicate: Callable
    max_n: int = 40
    note: str = ""


@dataclass
class EatIfHungry:
    """Eat a survival-kit goo when hunger has risen (a no-op otherwise).

    Mirrors how a player reacts to the hunger warnings: the goo handler
    refuses ("you're not hungry") without consuming the goo below the
    threshold, so these can be sprinkled liberally along the route.
    """
    threshold: int = 1
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

    def _eat_if_hungry(self, threshold=1):
        """Eat the next survival-kit goo if hunger has risen (else a no-op)."""
        if self.gs.flags.get("HUNGER-LEVEL", 0) < threshold:
            return
        kit = next((o for o in self.char.contents
                    if o.db.zil_id == "FOOD-KIT"), None)
        if kit is None:
            return
        if not kit.db.is_open:
            self._emit("open survival kit")
        goo = next((o for o in kit.contents), None)
        if goo is not None:
            self._emit(f"eat {goo.key}")

    def run_step(self, step):
        if isinstance(step, RepeatUntil):
            n = 0
            while not step.predicate(self.gs) and n < step.max_n:
                self._emit(step.text)
                if self.gs.dead:
                    break
                n += 1
            if not step.predicate(self.gs):
                raise StepError(f"RepeatUntil '{step.text}' exhausted after "
                                f"{n}: {step.note}")
            return
        if isinstance(step, EatIfHungry):
            if self.gs.flags.get("HUNGER-LEVEL", 0) < step.threshold:
                return
            kit = next((o for o in self.char.contents
                        if o.db.zil_id == "FOOD-KIT"), None)
            if kit is None:
                raise StepError(f"EatIfHungry: no survival kit carried. {step.note}")
            if not kit.db.is_open:
                self._emit("open survival kit")
            goo = next((o for o in kit.contents), None)
            if goo is None:
                return   # out of goo; rely on other food / hope to finish
            self._emit(f"eat {goo.key}")
            return
        if isinstance(step, WaitUntil):
            n = 0
            while not step.predicate(self.gs) and n < step.max_waits:
                self._eat_if_hungry()   # a waiting player still eats when hungry
                if self.gs.dead:
                    raise StepError(f"died while waiting: {step.note}")
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
    char.db.pf_walkthrough = True   # get player-style M-ENTER / room scoring
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
        note="Floyd activation awards +2"),
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
    # Detour into storage east for the good bedistor (the course-control spare).
    Cmd("east", expect=("Storage East",)),
    Cmd("take good bedistor", check=lambda gs: _carries(gs.player, "GOOD-BEDISTOR")),
    Cmd("west", expect=("Mech Corridor North",)),
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
    # The shuttle card (needed far later to drive the maglev) is one room
    # further west, in the large office's desk — grab it now while we're here
    # and the magnet is safely dropped.
    Cmd("west", expect=("Large Office",)),
    Cmd("open large desk", expect=("Opened",)),
    Cmd("take shuttle card", expect=("Taken",),
        check=lambda gs: _carries(gs.player, "SHUTTLE-CARD")),
    Cmd("east", expect=("Small Office",)),
]

def _floyd_with(gs):
    from world.walkthrough import _by_zil
    floyd = _by_zil("FLOYD")
    return bool(floyd and gs.player and floyd.location is gs.player.location)


# From here the hunger clock (first warning ~2000 ticks) bites; EatIfHungry is
# a no-op until then, and the WaitUntil loops also eat as needed.

# Milestone 4: end day 1 with a dorm sleep. The whole deep complex (shuttle +
# reactor + computer + cryo) lies past the shuttle where there is no safe bed,
# so we reset the sleep clock here to get a full fresh budget for day 2. Recross
# the rift back to the near side and bed down in a dorm until morning.
DAY1_SLEEP = [
    EatIfHungry(),
    Cmd("east", expect=("Admin Corridor North",), note="leave the office"),
    Cmd("south", expect=("Admin Corridor",), note="recross the rift"),
    Cmd("south", expect=("Admin Corridor South",)),
    Cmd("south", expect=("Corridor Junction",)),
    Cmd("west", expect=("Dorm Corridor",), note="long-hall transit"),
    Cmd("south", expect=("Dorm C",)),
    # Take Floyd's lower-elevator card here: the magnet is long dropped (so the
    # card won't scramble) and Floyd reliably catches up in the dorm. Search the
    # de-activated robot for it (CARD-STOLEN, +1), then switch him back on.
    WaitUntil(_floyd_with, max_waits=20, note="let Floyd catch up"),
    Cmd("turn off floyd", expect=("betrayal",)),
    Cmd("search floyd", expect=("Loowur Elavaatur",),
        check=lambda gs: gs.flags.get("CARD-STOLEN")),
    Cmd("turn on robot", note="reactivate Floyd"),
    WaitUntil(lambda gs: gs.flags.get("SLEEPY-LEVEL", 0) > 0, max_waits=320,
              note="stay up (eating as needed) until drowsy"),
    Cmd("get in bed", expect=("soft and comfortable",)),
    WaitUntil(lambda gs: gs.day >= 2, note="sleep through to day 2"),
]

# Milestone 5: day 2 — descend to the deep complex. Sleeping drops all non-worn
# gear on the floor (faithful: ZIL WAKING-UP moves it to HERE), so first scoop
# the essentials back up, then head to the elevator lobby and ride the lower car
# down to the waiting area, Floyd in tow.
DAY2_DESCENT = [
    Cmd("take survival kit", note="food first — eating needs it back"),
    Cmd("take laser"),
    Cmd("take shuttle card"),
    Cmd("take good bedistor"),
    Cmd("take pliers"),
    Cmd("take lower elevator card",
        check=lambda gs: _carries(gs.player, "LOWER-ELEVATOR-CARD", "SHUTTLE-CARD",
                                  "LASER", "FOOD-KIT", "GOOD-BEDISTOR", "PLIERS")),
    EatIfHungry(),
    Cmd("north", expect=("Dorm Corridor",)),
    Cmd("east", expect=("Corridor Junction",), note="long-hall transit"),
    Cmd("east", expect=("Elevator Lobby",)),
    Cmd("press red button", expect=("vibrating",), note="call the lower car"),
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

# Milestone 6: drive the maglev shuttle across the gorge to Lawanda. Board
# ALFIE, enter a control cabin, power it with the shuttle card, then run the
# speed puzzle: accelerate to velocity 20, coast, and decelerate so the car
# arrives at velocity 0 on counter 24 (vel<20 crashes the car, vel>=20 kills
# you). Then climb out to the Fork, the deep-complex hub.
SHUTTLE = [
    EatIfHungry(),
    Cmd("east", expect=("Kalamontee Platform",)),
    Cmd("south", expect=("Shuttle Car Alfie",), note="board ALFIE"),
    Cmd("east", expect=("Alfie Control East",), note="into the control cabin"),
    Cmd("slide shuttle card through slot", expect=("activated",),
        check=lambda gs: gs.flags.get("SHUTTLE-ON")),
    Cmd("push up lever", expect=("upper position",), note="accelerate"),
    Cmd("wait"), Cmd("wait"), Cmd("wait"),   # +5 velocity/turn -> 20
    Cmd("pull down lever", expect=("central position",), note="coast at 20"),
    WaitUntil(lambda gs: gs.flags.get("SHUTTLE-COUNTER", 0) >= 20,
              note="coast until nearly there"),
    Cmd("pull down lever", expect=("lower position",), note="decelerate"),
    WaitUntil(lambda gs: not gs.flags.get("SHUTTLE-MOVING"),
              note="glide to a stop"),
    Cmd("west", expect=("Shuttle Car Alfie",),
        check=lambda gs: not gs.flags.get("ALFIE-BROKEN"), note="clean arrival"),
    Cmd("north", expect=("Lawanda Platform",), note="across the gorge"),
    Cmd("up", expect=("Escalator",)),
    Cmd("up", expect=("Fork",), check=_floyd_with, note="deep-complex hub"),
]

# Milestone 7: repair planetary course control. In the course-control room,
# open the cube, pull its fused bedistor with the pliers, and drop in the good
# one — averting the orbital decay (+6). (Note "fused", not "bad": that's the
# bedistor's actual adjective; "bad" would tie with the carried good one.)
COURSE_CONTROL = [
    EatIfHungry(),
    Cmd("northeast", expect=("Systems Corridor West",)),
    Cmd("east", expect=("Systems Corridor",)),
    Cmd("east", expect=("Systems Corridor East",)),
    Cmd("north", expect=("Course Control",)),
    Cmd("open cube", expect=("swings open",)),
    Cmd("remove fused bedistor with pliers", expect=("remove the fused bedistor",)),
    Cmd("put good bedistor in cube", expect=("warning lights go out",),
        check=lambda gs: gs.flags.get("COURSE-CONTROL-FIXED")),
]

# Milestone 8: Floyd's bio-lock foray for the mini-booth card. Show Floyd the
# broken computer (he frets, COMPUTER-FLAG), then take him to the bio-lock,
# where he offers to brave the mutant lab for the card. Play the door dance —
# open (he rushes in), close (growls), wait (he knocks), open (he stumbles out
# with the card), close (he succumbs) — then take the mini-card he died for.
BIO_FORAY = [
    EatIfHungry(),
    Cmd("south", expect=("Systems Corridor East",)),
    Cmd("south", expect=("Library Lobby",)),
    Cmd("south", expect=("Project Corridor East",)),
    Cmd("south", expect=("Computer Room",)),
    WaitUntil(lambda gs: gs.flags.get("COMPUTER-FLAG"),
              note="Floyd recognises the broken computer"),
    Cmd("northeast", expect=("Main Lab",)),
    Cmd("open west door", expect=("Opened",)),
    Cmd("southeast", expect=("Bio Lock West",)),
    Cmd("east", expect=("Bio Lock East",)),
    WaitUntil(lambda gs: gs.flags.get("FLOYD-WAITING"),
              note="Floyd peers in and offers to fetch the card"),
    Cmd("open door", expect=("plunges into the Bio Lab",),
        check=lambda gs: gs.flags.get("FORAY-COUNTER") == 1),
    Cmd("close door", check=lambda gs: gs.flags.get("FORAY-COUNTER") == 2),
    Cmd("wait", check=lambda gs: gs.flags.get("FORAY-COUNTER") == 3, note="the knock"),
    Cmd("open door", expect=("stumbles out",),
        check=lambda gs: gs.flags.get("FORAY-COUNTER") == 4),
    Cmd("close door", check=lambda gs: gs.flags.get("FORAY-COUNTER") == 5,
        note="Floyd's sacrifice (+2)"),
    Cmd("take mini card", expect=("Taken",),
        check=lambda gs: _carries(gs.player, "MINI-CARD")),
]

# Milestone 9: fix the computer. Swap the laser's spent battery for the fresh
# one (the old one can't outlast the marksmanship ramp), shrink into the
# computer via the mini-booth, walk the strip to the relay, and laser the
# speck until it's destroyed — COMPUTER-FIXED (+8), which opens the cryo route.
COMPUTER_FIX = [
    EatIfHungry(),
    Cmd("west", expect=("Bio Lock West",)),
    Cmd("west", expect=("Main Lab",)),
    Cmd("south", expect=("Lab Storage",)),
    Cmd("take new battery"),
    Cmd("remove old battery from laser", expect=("Taken",)),
    Cmd("put new battery in laser", expect=("clicks into",)),
    Cmd("north", expect=("Main Lab",)),
    Cmd("southwest", expect=("Computer Room",)),
    Cmd("south", expect=("Miniaturization Booth",)),
    Cmd("slide mini card through slot", expect=("activated",)),
    Cmd("type 384", expect=("Station 384",), note="shrink into the computer"),
    Cmd("east", expect=("Strip Near Station",)),
    Cmd("north", expect=("Middle of Strip",)),
    Cmd("north", expect=("Strip Near Relay",)),
    Cmd("set dial to 1", expect=("set to 1",), note="red = the fine setting"),
    RepeatUntil("shoot speck with laser",
                lambda gs: gs.flags.get("COMPUTER-FIXED"), max_n=40,
                note="two precise hits fix the computer"),
]

# Milestone 10: the cryo endgame. Un-shrink (re-enter Station 384 → auxiliary
# booth), gear up in the lab office (gas mask) and flood the bio-lab, dash
# across it to the cryo elevator, slam the button on the pursuing mutants
# (CRYO-SCORE-FLAG, +5), descend, and step into the anteroom — where the cure
# is found and the game is won.
ESCAPE_AND_CRYO = [
    Cmd("south", expect=("Middle of Strip",)),
    Cmd("south", expect=("Strip Near Station",)),
    Cmd("west", expect=("Auxiliary Booth",), note="re-grow via the aux booth"),
    Cmd("north", expect=("Lab Office",)),
    Cmd("open lab desk", expect=("Opened",)),
    Cmd("take gas mask"),
    Cmd("wear gas mask", expect=("put on",)),
    Cmd("press fungicide button", expect=("hissing",),
        check=lambda gs: gs.flags.get("LAB-FLOODED")),
    Cmd("open office door"),
    Cmd("west", expect=("Bio Lab",), note="cross the (flooded) lab, masked"),
    Cmd("open east door"),
    Cmd("west", expect=("Bio Lock East",)),
    Cmd("west", expect=("Bio Lock West",)),
    Cmd("west", expect=("Main Lab",)),
    Cmd("southwest", expect=("Computer Room",)),
    Cmd("west", expect=("ProjCon Office",)),
    Cmd("south", expect=("Cryo-Elevator",)),
    Cmd("push button", expect=("monsters reach it",),
        check=lambda gs: gs.flags.get("CRYO-SCORE-FLAG")),
    WaitUntil(lambda gs: _door_open("CRYO-ELEVATOR-DOOR"),
              note="ride down to the anteroom"),
    Cmd("north", expect=("You have won",),
        check=lambda gs: gs.dead and gs.death_reason is None,
        note="the cryo anteroom resolves the game — victory"),
]

WALKTHROUGH: list = (list(ESCAPE_POD) + list(DESCEND_AND_FLOYD)
                     + list(TOOLS_AND_CARDS) + list(DAY1_SLEEP)
                     + list(DAY2_DESCENT) + list(SHUTTLE) + list(COURSE_CONTROL)
                     + list(BIO_FORAY) + list(COMPUTER_FIX) + list(ESCAPE_AND_CRYO))


def dev_run(steps=None, verbose=True):
    """Run the walkthrough against the live dev DB (discovery / smoke)."""
    char, gs = fresh_game()
    drv = Driver(char, gs, verbose=verbose)
    drv.run(steps if steps is not None else WALKTHROUGH)
    print(f"\n=== reached: day={gs.day} score={gs.score} "
          f"moves={gs.internal_moves} room={getattr(char.location,'db',None) and char.location.db.zil_id} "
          f"dead={gs.dead} ===")
    return drv
