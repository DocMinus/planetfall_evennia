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

_BLATHER_SWAGGER = (
    "\nEnsign First Class Blather swaggers in. He studies your work with "
    "half-closed eyes. \"You call this polishing, Ensign Seventh Class?\" he "
    "sneers. \"We have a position for an Ensign Ninth Class in the "
    "toilet-scrubbing division, you know. Thirty demerits."
)


def _drag_to_brig(gs) -> None:
    """ZIL: GOTO BRIG / ROB ADVENTURER CRAG / MOVE PADLOCK HERE (globals.zil:688)."""
    player = gs.player
    brig = _by_zil_id("BRIG")
    crag = _by_zil_id("CRAG")
    padlock = _by_zil_id("PADLOCK")
    if player and crag:
        for item in list(player.contents):
            item.move_to(crag, quiet=True, move_type="drop")
    if player and brig:
        player.move_to(brig, quiet=True, move_type="teleport")
    if padlock and brig:
        padlock.move_to(brig, quiet=True, move_type="move")
        padlock.db.obj_flags = [
            f for f in (padlock.db.obj_flags or []) if f != "TAKEBIT"
        ]


def make_i_blather(gs: "GameState", state: dict | None = None):
    """I-BLATHER (globals.zil:680) — Ensign Blather harasses the wayward ensign."""
    state = state if state is not None else {"brigs_up": 0, "blather_leave": 0}

    def i_blather() -> bool:
        here = _here_id(gs)
        if here is None:
            return False
        bcount = gs.flags.get("BLOWUP-COUNTER", 0)

        if here in ("DECK-EIGHT", "REACTOR-LOBBY"):
            if _in_room("BLATHER", here):
                state["brigs_up"] = state.get("brigs_up", 0) + 1
                if state["brigs_up"] > 3:
                    _send(gs,
                        "\nBlather loses his last vestige of patience and drags "
                        "you to the Feinstein's brig. He throws you in, and the "
                        "door clangs shut behind you.\n")
                    _move_npc("BLATHER", None)
                    _drag_to_brig(gs)
                else:
                    _send(gs,
                        "\n\"I said to return to your post, Ensign Seventh "
                        "Class!\" bellows Blather, turning a deepening shade of "
                        "crimson.")
            elif bcount == 0:
                _move_npc("BLATHER", here)
                _send(gs,
                    "\nEnsign Blather, his uniform immaculate, enters and "
                    "notices you are away from your post. \"Twenty demerits, "
                    "Ensign Seventh Class!\" bellows Blather. \"Forty if you're "
                    "not back on Deck Nine in five seconds!\" He curls his face "
                    "into a hideous mask of disgust at your unbelievable "
                    "negligence.")

        elif here == "DECK-NINE":
            if state.get("blather_leave", 0) == 3 and _in_room("BLATHER", "DECK-NINE"):
                state["blather_leave"] = 0
                _move_npc("BLATHER", None)
                _send(gs,
                    "\nBlather, adding fifty more demerits for good measure, "
                    "moves off in search of more young ensigns to terrorize.")
            elif _in_room("BLATHER", "DECK-NINE"):
                state["blather_leave"] = state.get("blather_leave", 0) + 1
                return False
            elif (not _in_room("AMBASSADOR", here) and bcount == 0 and _prob(5)):
                _move_npc("BLATHER", here)
                uniform = _by_zil_id("PATROL-UNIFORM")
                worn = uniform and "WORNBIT" in (uniform.db.obj_flags or [])
                msg = _BLATHER_SWAGGER
                if not worn:
                    msg += " And another sixty for improper dress!"
                msg += "\" He glares at you, his arms crossed."
                _send(gs, msg)
        return False

    i_blather.__name__ = "i_blather"
    i_blather._state = state
    return i_blather


# ---------------------------------------------------------------------------
# I-AMBASSADOR  (globals.zil:802)
# Every-turn alien NPC interactions.
# ---------------------------------------------------------------------------

_AMBASSADOR_QUOTES = (
    "introduces himself as Br'gun-te'elkner-ipg'nun.",
    "asks if you are performing some sort of religious ceremony.",
    "inquires whether you are interested in a game of Bocci.",
    "recites a plea for coexistence between your races.",
    "asks where Admiral Smithers can be found.",
    "remarks that all humans look alike to him.",
    "offers you a bit of celery.",
)


def make_i_ambassador(gs: "GameState", state: dict | None = None):
    """I-AMBASSADOR (globals.zil:802) — the slime-trailing alien ambassador."""
    state = state if state is not None else {"ambassador_leave": 0}

    def i_ambassador() -> bool:
        import random
        here = _here_id(gs)
        if here is None:
            return False
        bcount = gs.flags.get("BLOWUP-COUNTER", 0)

        if state.get("ambassador_leave", 0) > 2 and _in_room("AMBASSADOR", here):
            _move_npc("AMBASSADOR", None)
            _move_npc("CELERY", None)
            if here == "DECK-NINE":
                _send(gs,
                    "\nThe ambassador grunts a polite farewell, and disappears "
                    "up the gangway, leaving a trail of dripping slime.")
            _disable_self(gs, "i_ambassador")
        elif _in_room("AMBASSADOR", "DECK-NINE"):
            state["ambassador_leave"] = state.get("ambassador_leave", 0) + 1
            if here == "DECK-NINE":
                _send(gs, "\nThe ambassador " + random.choice(_AMBASSADOR_QUOTES))
            return False
        elif here == "DECK-NINE":
            if (not _in_room("AMBASSADOR", here)
                    and not _in_room("BLATHER", here)
                    and bcount == 0
                    and _prob(15)):
                _move_npc("AMBASSADOR", "DECK-NINE")
                _move_npc("CELERY", "DECK-NINE")
                brochure = _by_zil_id("BROCHURE")
                if brochure and gs.player:
                    brochure.move_to(gs.player, quiet=True, move_type="get")
                _send(gs,
                    "\nThe alien ambassador from the planet Blow'k-bibben-Gordo "
                    "ambles toward you from down the corridor. He is munching on "
                    "something resembling an enormous stalk of celery, and he "
                    "leaves a trail of green slime on the deck. He stops nearby, "
                    "and you wince as a pool of slime begins forming beneath him "
                    "on your newly-polished deck. The ambassador wheezes loudly "
                    "and hands you a brochure outlining his planet's major "
                    "exports.")
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
        # ZIL: <ENABLE <QUEUE I-BLOWUP-FEINSTEIN <+ <RANDOM 90> 240>>> — the
        # Feinstein is doomed 240–329 ticks in; I-BLOWUP-FEINSTEIN converts
        # itself to an every-turn demon on its first fire.
        import random
        gs.scheduler.enable(
            gs.scheduler.queue(make_i_blowup_feinstein(gs), random.randint(0, 89) + 240)
        )
        return False

    i_random_interrupts.__name__ = "i_random_interrupts"
    i_random_interrupts._state = state
    return i_random_interrupts


# ---------------------------------------------------------------------------
# I-BLOWUP-FEINSTEIN  (globals.zil:1162)
# The opening: the Feinstein is destroyed over five turns (BLOWUP-COUNTER 1→5).
# The player must reach the ESCAPE-POD and get in the SAFETY-WEB before then.
# Door state, NPC panic, and whether you die or eject all key off where HERE is.
# ---------------------------------------------------------------------------

def _set_door(zil_id: str, *, is_open=None, visible=None) -> None:
    """Mutate a door object's OPENBIT / INVISIBLE, by zil_id."""
    door = _by_zil_id(zil_id)
    if door is None:
        return
    if is_open is not None:
        door.db.is_open = is_open
    if visible is not None:
        flags = [f for f in (door.db.obj_flags or []) if f != "INVISIBLE"]
        if not visible:
            flags.append("INVISIBLE")
        door.db.obj_flags = flags


def make_i_blowup_feinstein(gs: "GameState", state: dict | None = None):
    """I-BLOWUP-FEINSTEIN (globals.zil:1162) — the five-turn ship destruction."""
    state = state if state is not None else {}

    def i_blowup_feinstein() -> bool:
        gs.flags["BLOWUP-COUNTER"] = gs.flags.get("BLOWUP-COUNTER", 0) + 1
        n = gs.flags["BLOWUP-COUNTER"]
        here = _here_id(gs)

        if n == 1:
            gs.flags["BRIGS-UP"] = 0
            _set_door("POD-DOOR", is_open=True)
            msg = ("\nA massive explosion rocks the ship. Echoes from the "
                   "explosion resound deafeningly down the halls. ")
            if here == "DECK-NINE":
                msg += "The door to port slides open. "
                if _in_room("AMBASSADOR", here):
                    _move_npc("AMBASSADOR", None)
                    _move_npc("CELERY", None)
                    msg += ("\nThe ambassador squawks frantically, evacuates a "
                            "massive load of gooey slime, and rushes away.")
                elif _in_room("BLATHER", here):
                    _move_npc("BLATHER", None)
                    msg += ("\nBlather, confused by this non-routine occurrence, "
                            "orders you to continue scrubbing the floor, and "
                            "then dashes off.")
            elif here not in ("ESCAPE-POD", "GANGWAY", "BRIG"):
                msg += ("\nBlather, looking slightly disoriented, barks at you "
                        "to resume your assigned duties.")
            _send(gs, msg)
            # The opening QUEUE fired this once after a delay; convert it to an
            # every-turn demon (ZIL `<ENABLE <QUEUE I-BLOWUP-FEINSTEIN -1>>`).
            # Only here at n==1 — the -1 demon persists, so re-queuing each turn
            # would stack duplicates.
            gs.scheduler.enable(gs.scheduler.queue(i_blowup_feinstein, -1))

        elif n == 2:
            _set_door("CORRIDOR-DOOR", is_open=False, visible=True)
            _set_door("GANGWAY-DOOR", is_open=False, visible=True)
            if here == "DECK-NINE":
                _send(gs, "\nMore distant explosions! A narrow emergency "
                      "bulkhead at the base of the gangway and a wider one "
                      "along the corridor to starboard both crash shut!")
            elif here in ("ESCAPE-POD", "BRIG"):
                _send(gs, "\nThe ship shakes again. You hear, from close by, "
                      "the sounds of emergency bulkheads closing.")
            elif here == "GANGWAY":
                _send(gs, "\nAnother explosion. A narrow bulkhead at the base "
                      "of the gangway slams shut!")
            else:
                _send(gs, "\nYou are deafened by more explosions and by the "
                      "sound of emergency bulkheads slamming closed.")

        elif n == 3:
            _set_door("POD-DOOR", is_open=False)
            if here == "DECK-NINE":
                _send(gs, "\nMore powerful explosions buffet the ship. The "
                      "lights flicker madly, and the escape-pod bulkhead "
                      "clangs shut.")
            elif here == "ESCAPE-POD":
                _send(gs, "\nThe pod door clangs shut as heavy explosions "
                      "continue to buffet the Feinstein.")
            else:
                return gs.die(
                    "The ship rocks from the force of multiple explosions. The "
                    "lights go out, and you feel a sudden drop in pressure "
                    "accompanied by a loud hissing. Too bad you weren't in the "
                    "escape pod...")

        elif n == 4:
            _disable_self(gs, "i_blather")
            _disable_self(gs, "i_ambassador")
            if here == "DECK-NINE":
                _send(gs, "\nExplosions continue to rock the ship.")
            else:
                _send(gs, "\nYou feel the pod begin to slide down its ejection "
                      "tube as explosions shake the mother ship.")

        else:  # n == 5
            if here == "DECK-NINE":
                return gs.die(
                    "An enormous explosion tears the walls of the ship apart. "
                    "If only you had made it to an escape pod...")
            _send(gs, "\nThrough the viewport of the pod you see the Feinstein "
                  "dwindle as you head away. Bursts of light dot its hull. "
                  "Suddenly, a huge explosion blows the Feinstein into tiny "
                  "pieces, sending the escape pod tumbling away!")
            gs.scheduler.enable(gs.scheduler.queue(make_i_pod_trip(gs), -1))
            _disable_self(gs, "i_blowup_feinstein")
            if not gs.flags.get("IN-WEB"):
                if _prob(20):
                    return gs.die(
                        "You are thrown against the bulkhead, head first. It "
                        "seems that getting in the safety webbing would have "
                        "been a good idea.")
                _send(gs, "\nYou are thrown against the bulkhead, bruising a "
                      "few limbs. The safety webbing might have offered a bit "
                      "more protection.")
        return False

    i_blowup_feinstein.__name__ = "i_blowup_feinstein"
    i_blowup_feinstein._state = state
    return i_blowup_feinstein


# ---------------------------------------------------------------------------
# I-POD-TRIP  (globals.zil:1272)
# The escape pod's descent to Resida (TRIP-COUNTER 1→11). Lands safely only if
# you rode it in the SAFETY-WEB.
# ---------------------------------------------------------------------------

_POD_TRIP_MESSAGES = {
    1: ("\nAs the escape pod tumbles away from the former location of the "
        "Feinstein, its gyroscopes whine. The pod slowly stops tumbling. "
        "Lights on the control panel blink furiously as the autopilot "
        "searches for a reasonable destination."),
    2: ("\nThe auxiliary rockets fire briefly, and a nearby planet swings into "
        "view through the port. It appears to be almost entirely ocean, with "
        "just a few visible islands and an unusually small polar ice cap. A "
        "moment later, the system's sun swings into view, and the viewport "
        "polarizes into a featureless black rectangle."),
    3: ("\nThe main thrusters fire a long, gentle burst. A monotonic voice "
        "issues from the control panel. \"Approaching planet...human-"
        "habitable.\""),
    7: "\nThe pod is buffeted as it enters the planet's atmosphere.",
    8: ("\nYou feel the temperature begin to rise, and the pod's climate "
        "control system roars as it labors to compensate."),
    9: ("\nThe viewport suddenly becomes transparent again, giving you a view "
        "of endless ocean below. The lights on the control panel flash madly "
        "as the pod's computer searches for a suitable landing site. The "
        "thrusters fire long and hard, slowing the pod's descent."),
    10: ("\nThe pod is now approaching the closer of a pair of islands. It "
         "appears to be surrounded by sheer cliffs rising from the water, and "
         "is topped by a wide plateau. The plateau seems to be covered by a "
         "sprawling complex of buildings."),
}


def make_i_pod_trip(gs: "GameState", state: dict | None = None):
    """I-POD-TRIP (globals.zil:1272) — the pod's descent to the planet."""
    state = state if state is not None else {}

    def i_pod_trip() -> bool:
        gs.flags["TRIP-COUNTER"] = gs.flags.get("TRIP-COUNTER", 0) + 1
        n = gs.flags["TRIP-COUNTER"]
        if n in _POD_TRIP_MESSAGES:
            _send(gs, _POD_TRIP_MESSAGES[n])
        elif n == 11:
            if gs.flags.get("IN-WEB"):
                here = _by_zil_id("ESCAPE-POD")
                for zid in ("FOOD-KIT", "TOWEL"):
                    obj = _by_zil_id(zid)
                    if obj and here:
                        obj.move_to(here, quiet=True, move_type="move")
                _send(gs, "\nThe pod lands with a thud. Through the viewport "
                      "you can see a rocky cleft and some water below. The pod "
                      "rocks gently back and forth as if it was precariously "
                      "balanced. A previously unseen panel slides open, "
                      "revealing some emergency provisions, including a "
                      "survival kit and a towel.")
                gs.flags["TRIP-COUNTER"] = 15
                _disable_self(gs, "i_pod_trip")
            else:
                return gs.die(
                    "The pod, whose automated controls were unfortunately "
                    "designed by computer scientists, lands with a good deal of "
                    "force. Your body sails across the pod until it is stopped "
                    "by one of the sharper corners of the control panel.")
        return False

    i_pod_trip.__name__ = "i_pod_trip"
    i_pod_trip._state = state
    return i_pod_trip


# ---------------------------------------------------------------------------
# I-SINK-POD  (globals.zil:1325)
# Once the pod has landed and you stand out of the web, it sinks. You must
# open the pod door and swim out before SINK-COUNTER 5.
# ---------------------------------------------------------------------------

def make_i_sink_pod(gs: "GameState", state: dict | None = None):
    """I-SINK-POD (globals.zil:1325) — the landed pod fills with water and sinks."""
    state = state if state is not None else {}

    def i_sink_pod() -> bool:
        gs.flags["SINK-COUNTER"] = gs.flags.get("SINK-COUNTER", 0) + 1
        n = gs.flags["SINK-COUNTER"]
        in_pod = _here_id(gs) == "ESCAPE-POD"
        pod_open = _door_is_open("POD-DOOR")
        if n == 3 and in_pod:
            _send(gs, "\nThe pod is now completely submerged, and you feel it "
                  "smash against underwater rocks. Bubbles streaming upward "
                  "past the window indicate that the pod is continuing to sink.")
        elif n == 4 and in_pod and not pod_open:
            _send(gs, "\nThe pod creaks ominously from the increasing pressure.")
        elif n == 5 and in_pod:
            if pod_open:
                return gs.die(
                    "Between the swirling waters and the increasing pressure, "
                    "it's curtains for you. Perhaps you should have left the "
                    "pod a bit sooner.")
            return gs.die("The pod splits open, and water pours in.")
        return False

    i_sink_pod.__name__ = "i_sink_pod"
    i_sink_pod._state = state
    return i_sink_pod


def _door_is_open(zil_id: str) -> bool:
    door = _by_zil_id(zil_id)
    return bool(door and door.db.is_open)


# ---------------------------------------------------------------------------
# I-SLEEP-WARNINGS  (globals.zil:2026) + the fall-asleep / dream / wake cycle.
# SLEEPY-LEVEL escalates; getting into a bed converts it to a restful sleep
# that advances the day, otherwise level 5 forces a (sometimes fatal) collapse.
# ---------------------------------------------------------------------------

_SLEEP_MESSAGES = {
    1: ("\nYou begin to feel weary. It might be time to think about finding "
        "a nice safe place to sleep.", 400),
    2: ("\nYou're really tired now. You'd better find a place to sleep real "
        "soon.", 135),
    3: ("\nIf you don't get some sleep soon you'll probably drop.", 60),
    4: ("\nYou can barely keep your eyes open.", 50),
}


def make_i_sleep_warnings(gs: "GameState", state: dict | None = None):
    """I-SLEEP-WARNINGS (globals.zil:2026)."""
    state = state if state is not None else {}

    def i_sleep_warnings() -> bool:
        gs.flags["SLEEPY-LEVEL"] = gs.flags.get("SLEEPY-LEVEL", 0) + 1
        lvl = gs.flags["SLEEPY-LEVEL"]
        if gs.flags.get("IN-BED"):
            _send(gs, "\nYou suddenly realize how tired you were and how "
                  "comfortable the bed is. You should be asleep in no time.")
            _disable_self(gs, "i_sleep_warnings")
            gs.scheduler.enable(gs.scheduler.queue(make_i_fall_asleep(gs), 16))
            return False
        if lvl in _SLEEP_MESSAGES:
            msg, delay = _SLEEP_MESSAGES[lvl]
            _send(gs, msg)
            gs.scheduler.enable(gs.scheduler.queue(i_sleep_warnings, delay))
            return False
        # Level 5: you can't stay awake any longer.
        return _sleep_collapse(gs)

    i_sleep_warnings.__name__ = "i_sleep_warnings"
    i_sleep_warnings._state = state
    return i_sleep_warnings


def _sleep_collapse(gs) -> bool:
    here = _here_id(gs)
    if here in ("DORM-A", "DORM-B", "DORM-C", "DORM-D"):
        _send(gs, "\nYou climb into one of the bunk beds and immediately fall "
              "asleep.")
        gs.flags["IN-BED"] = True
        return _dream_and_wake(gs)
    _send(gs, "\nYou can't stay awake a moment longer. You drop to the ground "
          "and fall into a deep but fitful sleep.")
    day = gs.day
    # The castle floods as the days pass; sleeping in the wrong spot drowns you.
    drown = ((day == 1 and here == "CRAG") or
             (day == 3 and here == "BALCONY") or
             (day == 5 and here == "WINDING-STAIR"))
    if drown:
        return gs.die(
            "\n\n\nSuddenly, in the middle of the night, a wave of water washes "
            "over you. Before you can quite get your bearings, you drown.")
    if _prob(30):
        return gs.die(
            "\n\n\nSuddenly, in the middle of the night, you awake as several "
            "ferocious beasts (could they be grues?) surround and attack you. "
            "Perhaps you should have found a slightly safer place to sleep.")
    return _dream_and_wake(gs)


def make_i_fall_asleep(gs: "GameState", state: dict | None = None):
    """I-FALL-ASLEEP (globals.zil:2140) — the moment you actually drop off."""
    state = state if state is not None else {}

    def i_fall_asleep() -> bool:
        _send(gs, "\nYou slowly sink into a deep and restful sleep.")
        _disable_self(gs, "i_fall_asleep")
        return _dream_and_wake(gs)

    i_fall_asleep.__name__ = "i_fall_asleep"
    i_fall_asleep._state = state
    return i_fall_asleep


_DREAMS = (
    "\n...You find yourself on the bridge of the Feinstein. Ensign Blather is "
    "here, as well as Admiral Smithers. You are diligently scrubbing the "
    "control panel. Blather keeps yelling at you to scrub harder. Suddenly you "
    "hit the ship's self-destruct switch! Smithers and Blather howl at you as "
    "the ship begins exploding! You try to run, but your feet seem to be fused "
    "to the deck...",
    "\n...You gulp down the last of your Ramosian Fire Nectar and ask the "
    "andro-waiter for another pitcher...",
)


def _dream_and_wake(gs) -> bool:
    """DREAMING + WAKING-UP (globals.zil:2145, 2197)."""
    import random
    if _prob(60):
        _send(gs, random.choice(_DREAMS))
    return _waking_up(gs)


def _waking_up(gs) -> bool:
    """WAKING-UP (globals.zil:2197) — a new day dawns; reset fatigue, drop kit."""
    gs.day += 1
    gs.flags["SICKNESS-WARNING-FLAG"] = True
    gs.flags["SLEEPY-LEVEL"] = 0
    in_bed = gs.flags.get("IN-BED")

    # Day 9 with no end in sight: you don't survive the night (RESET-TIME).
    if gs.day >= 9:
        return gs.die("Unfortunately, you don't seem to have survived the "
                      "night.")

    # Non-worn inventory ends up on the floor (ZIL drops it to HERE).
    player = gs.player
    if player and player.location:
        for item in list(player.contents):
            if "WORNBIT" not in (item.db.obj_flags or []):
                item.move_to(player.location, quiet=True, move_type="drop")

    banner = f"\n***** SEPTEM {gs.day + 4}, 11344 *****\n"
    sick = gs.flags.get("SICKNESS-LEVEL", 0)
    if not in_bed:
        banner += ("You wake and slowly stand up, feeling stiff from your "
                   "night on the floor.")
    elif sick < 3:
        banner += ("You wake up feeling refreshed and ready to face the "
                   "challenges of this mysterious world.")
    elif sick < 6:
        banner += ("You wake after sleeping restlessly. You feel weak and "
                   "listless.")
    else:
        banner += ("You wake feeling weak and worn-out. It will be an effort "
                   "just to stand up.")
    gs.flags["IN-BED"] = False

    if gs.flags.get("HUNGER-LEVEL", 0) > 0:
        gs.flags["HUNGER-LEVEL"] = 4
        gs.scheduler.enable(gs.scheduler.queue(make_i_hunger_warnings(gs), 100))
        banner += " You are also incredibly famished. Better get some breakfast!"
    else:
        gs.scheduler.enable(gs.scheduler.queue(make_i_hunger_warnings(gs), 400))

    _send(gs, banner)
    # Re-arm the next night's sleep clock (RESET-TIME, simplified).
    _SLEEP_RESET_DELAYS = {2: 5800, 3: 5550, 4: 5200, 5: 4800,
                           6: 4300, 7: 3700, 8: 3000}
    delay = _SLEEP_RESET_DELAYS.get(gs.day, 3000)
    gs.scheduler.enable(gs.scheduler.queue(make_i_sleep_warnings(gs), delay))
    return False


# ---------------------------------------------------------------------------
# I-HUNGER-WARNINGS  (globals.zil:2295)
# ---------------------------------------------------------------------------

_HUNGER_MESSAGES = {
    1: ("\nA growl from your stomach warns that you're getting pretty hungry "
        "and thirsty.", 450),
    2: ("\nYou're now really ravenous and your lips are quite parched.", 150),
    3: ("\nYou're starting to feel faint from lack of food and liquid.", 100),
    4: ("\nIf you don't eat or drink something in a few millichrons, you'll "
        "probably pass out.", 50),
}


def make_i_hunger_warnings(gs: "GameState", state: dict | None = None):
    """I-HUNGER-WARNINGS (globals.zil:2295)."""
    state = state if state is not None else {}

    def i_hunger_warnings() -> bool:
        gs.flags["HUNGER-LEVEL"] = gs.flags.get("HUNGER-LEVEL", 0) + 1
        lvl = gs.flags["HUNGER-LEVEL"]
        if lvl in _HUNGER_MESSAGES:
            msg, delay = _HUNGER_MESSAGES[lvl]
            _send(gs, msg)
            gs.scheduler.enable(gs.scheduler.queue(i_hunger_warnings, delay))
            return False
        return gs.die("\nYou collapse from extreme thirst and hunger.")

    i_hunger_warnings.__name__ = "i_hunger_warnings"
    i_hunger_warnings._state = state
    return i_hunger_warnings


# ---------------------------------------------------------------------------
# I-SICKNESS-WARNINGS  (globals.zil:2330)
# ---------------------------------------------------------------------------

_SICKNESS_MESSAGES = {
    1: "\nYou notice that you feel a bit weak and slightly flushed, but you're "
       "not sure why.",
    2: "\nYou notice that you feel unusually weak, and you suspect that you "
       "have a fever.",
    3: "\nYou are now feeling quite under the weather, not unlike a bad flu.",
    4: "\nYour fever seems to have gotten worse, and you're developing a bad "
       "headache.",
    5: "\nYour health has deteriorated further. You feel hot and weak, and "
       "your head is throbbing.",
    6: "\nYou feel very, very sick, and have almost no strength left.",
    7: "\nYou feel like you're on fire, burning up from the fever. You're "
       "almost too weak to move, and your brain is reeling from the pounding "
       "headache.",
    8: "\nYou're no longer sure of where you are and what you're doing. You "
       "stumble about, your pain subsiding into a dull numbness.",
}


def make_i_sickness_warnings(gs: "GameState", state: dict | None = None):
    """I-SICKNESS-WARNINGS (globals.zil:2330) — only progresses on sick days.

    SICKNESS-WARNING-FLAG is set each night (WAKING-UP); the disease only
    advances while it is set, so a healthy player's sickness clock idles.
    """
    state = state if state is not None else {}

    def i_sickness_warnings() -> bool:
        gs.scheduler.enable(gs.scheduler.queue(i_sickness_warnings, 700))
        if not gs.flags.get("SICKNESS-WARNING-FLAG"):
            return False
        gs.flags["SICKNESS-WARNING-FLAG"] = False
        gs.flags["SICKNESS-LEVEL"] = gs.flags.get("SICKNESS-LEVEL", 0) + 1
        lvl = gs.flags["SICKNESS-LEVEL"]
        if lvl in _SICKNESS_MESSAGES:
            _send(gs, _SICKNESS_MESSAGES[lvl])
            return False
        if lvl >= 9:
            return gs.die("\nYou finally succumb to the ravages of your illness "
                          "and collapse.")
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
    results = search_object_attribute(key="zil_id", value=zil_id)
    return results[0] if results else None


def _player_in(gs, room_zil_id: str) -> bool:
    p = gs.player
    return bool(p and p.location and p.location.db.zil_id == room_zil_id)


# --- NPC movement helpers (Blather / Ambassador, Phase 12) ----------------

def _here_id(gs):
    """ZIL HERE — the player's current room zil_id, or None."""
    p = gs.player
    return p.location.db.zil_id if (p and p.location) else None


def _in_room(obj_zil_id: str, room_zil_id: str) -> bool:
    """ZIL <IN? obj room> by zil_id."""
    obj = _by_zil_id(obj_zil_id)
    return bool(obj and obj.location and obj.location.db.zil_id == room_zil_id)


def _move_npc(obj_zil_id: str, room_zil_id):
    """ZIL <MOVE obj room> / <REMOVE obj> (room_zil_id=None removes off-stage)."""
    obj = _by_zil_id(obj_zil_id)
    if obj is None:
        return
    if room_zil_id is None:
        obj.location = None
    else:
        dest = _by_zil_id(room_zil_id)
        if dest is not None:
            obj.move_to(dest, quiet=True, move_type="move")


def _prob(percent: int) -> bool:
    """ZIL <PROB N> — true N percent of the time."""
    import random
    return random.randint(1, 100) <= percent


def _disable_self(gs, name: str) -> None:
    """ZIL <DISABLE <INT routine>> — stop our own every-turn interrupt firing."""
    for i in gs.scheduler._interrupts:
        if getattr(i.routine, "__name__", None) == name:
            gs.scheduler.disable(i)


# ---------------------------------------------------------------------------
# Card-access turnoff / close demons (globals.zil / compone.zil)
# Each clears the enable flag a card set, after its delay, unless the relevant
# car is mid-transit (the elevators re-queue themselves in that case).
# ---------------------------------------------------------------------------

def _turnoff(gs, flag: str, room_zil: str, message: str,
             name: str, requeue_if_transit: bool = False):
    """Factory body shared by the elevator/teleport/mini turnoff demons."""
    def routine() -> bool:
        if requeue_if_transit and gs.flags.get("ELEVATOR-IN-TRANSIT"):
            gs.scheduler.enable(gs.scheduler.queue(routine, 120))
            return False
        gs.flags[flag] = False
        if _here_id(gs) == room_zil or (room_zil == "*"):
            _send(gs, message)
        return False
    routine.__name__ = name
    routine._state = {}
    return routine


def make_i_turnoff_upper_elevator(gs: "GameState", state: dict | None = None):
    return _turnoff(gs, "UPPER-ELEVATOR-ON", "UPPER-ELEVATOR",
                    "\nA recording says \"Elevator no longer enabled.\"",
                    "i_turnoff_upper_elevator", requeue_if_transit=True)


def make_i_turnoff_lower_elevator(gs: "GameState", state: dict | None = None):
    return _turnoff(gs, "LOWER-ELEVATOR-ON", "LOWER-ELEVATOR",
                    "\nA recording says \"Elevator no longer enabled.\"",
                    "i_turnoff_lower_elevator", requeue_if_transit=True)


def make_i_upper_elevator_trip(gs: "GameState", state: dict | None = None):
    """I-UPPER-ELEVATOR-TRIP (compone.zil:2674) — the car finishes its run.

    Toggles UPPER-ELEVATOR-UP, clears ELEVATOR-IN-TRANSIT, and re-opens the
    door. Queued by the inside ELEVATOR-BUTTON when you push Up/Down.
    """
    def i_upper_elevator_trip() -> bool:
        gs.flags["UPPER-ELEVATOR-UP"] = not gs.flags.get("UPPER-ELEVATOR-UP")
        gs.flags["ELEVATOR-IN-TRANSIT"] = False
        door = _by_zil_id("UPPER-ELEVATOR-DOOR")
        if door:
            door.db.is_open = True
        if _here_id(gs) == "UPPER-ELEVATOR":
            _send(gs, "\nThe elevator door slides open.")
        return False
    i_upper_elevator_trip.__name__ = "i_upper_elevator_trip"
    i_upper_elevator_trip._state = {}
    return i_upper_elevator_trip


def make_i_lower_elevator_trip(gs: "GameState", state: dict | None = None):
    """I-LOWER-ELEVATOR-TRIP (compone.zil:2689) — the car finishes its run."""
    def i_lower_elevator_trip() -> bool:
        gs.flags["LOWER-ELEVATOR-UP"] = not gs.flags.get("LOWER-ELEVATOR-UP")
        gs.flags["ELEVATOR-IN-TRANSIT"] = False
        door = _by_zil_id("LOWER-ELEVATOR-DOOR")
        if door:
            door.db.is_open = True
        if _here_id(gs) == "LOWER-ELEVATOR":
            _send(gs, "\nThe elevator door slides open.")
        return False
    i_lower_elevator_trip.__name__ = "i_lower_elevator_trip"
    i_lower_elevator_trip._state = {}
    return i_lower_elevator_trip


def make_i_turnoff_teleportation(gs: "GameState", state: dict | None = None):
    def i_turnoff_teleportation() -> bool:
        gs.flags["TELEPORTATION-ON"] = False
        if _here_id(gs) in ("BOOTH-1", "BOOTH-2", "BOOTH-3"):
            _send(gs, "\nThe ready light goes dark.")
        return False
    i_turnoff_teleportation.__name__ = "i_turnoff_teleportation"
    i_turnoff_teleportation._state = {}
    return i_turnoff_teleportation


def make_i_turnoff_mini(gs: "GameState", state: dict | None = None):
    def i_turnoff_mini() -> bool:
        gs.flags["MINI-ACTIVATED"] = False
        if _here_id(gs) == "MINI-BOOTH":
            _send(gs, "\nThe hum of the booth's machinery dies away.")
        return False
    i_turnoff_mini.__name__ = "i_turnoff_mini"
    i_turnoff_mini._state = {}
    return i_turnoff_mini


_MAGNET_CARDS = ("KITCHEN-CARD", "SHUTTLE-CARD", "TELEPORTATION-CARD",
                 "UPPER-ELEVATOR-CARD", "LOWER-ELEVATOR-CARD", "MINI-CARD",
                 "ID-CARD")


def make_i_warmth(gs: "GameState", state: dict | None = None):
    """I-WARMTH (comptwo.zil:2789) — the laser heats up when fired, then cools."""
    def i_warmth() -> bool:
        f = gs.flags
        if f.get("LASER-JUST-SHOT"):
            f["LASER-JUST-SHOT"] = False
            f["WARMTH-FLAG"] = f.get("WARMTH-FLAG", 0) + 1
            feels = {3: "slightly warm now", 6: "somewhat warm now",
                     9: "very warm now", 12: "quite hot"}.get(f["WARMTH-FLAG"])
            if feels:
                _send(gs, f"\nThe laser feels {feels}, but that doesn't seem to "
                      "affect its performance at all.")
        else:
            if f.get("WARMTH-FLAG", 0) == 0:
                _disable_self(gs, "i_warmth")
            else:
                f["WARMTH-FLAG"] -= 1
                cools = {12: "quite hot", 9: "very warm", 6: "somewhat warm",
                         3: "slightly warm"}.get(f["WARMTH-FLAG"])
                if cools:
                    _send(gs, f"\nThe laser has cooled, but it still feels "
                          f"{cools}.")
        return False
    i_warmth.__name__ = "i_warmth"
    i_warmth._state = {}
    return i_warmth


def make_i_fry(gs: "GameState", state: dict | None = None):
    """I-FRY (comptwo.zil:2880) — Sector 384 powers up; leave the strip or die."""
    def i_fry() -> bool:
        if _here_id(gs) in ("MIDDLE-OF-STRIP", "STRIP-NEAR-STATION",
                            "STRIP-NEAR-RELAY"):
            return gs.die(
                "\nWith a furious storm of electrical mayhem, Sector 384 comes "
                "to life. A few micro-volts course through the silicon strip on "
                "which you stand. Unfortunately, at your current size, this is "
                "enough to barbecue you.")
        return False
    i_fry.__name__ = "i_fry"
    i_fry._state = {}
    return i_fry


def make_i_announcement(gs: "GameState", state: dict | None = None):
    """I-ANNOUNCEMENT (comptwo.zil:2424) — cryo access now open."""
    def i_announcement() -> bool:
        _send(gs, "\nA recorded announcement blares from the public address "
              "system. \"Revival procedure beginning. Cryo-chamber access from "
              "Project Control Office now open.\"")
        return False
    i_announcement.__name__ = "i_announcement"
    i_announcement._state = {}
    return i_announcement


_MONSTER_IDS = ("RAT-ANT", "TRIFFID", "TROLL", "GRUE")
_MONSTER_ENTRANCES = (
    "One of them trips over an experiment and crashes to the floor.",
    "They snarl and drool as they catch sight of you.",
    "They scrabble over each other in their eagerness to reach you.",
)


def make_i_chase_scene(gs: "GameState", state: dict | None = None):
    """I-CHASE-SCENE (comptwo.zil:2040) — the mutants pursue you from the lab."""
    state = state if state is not None else {"last": None}

    def i_chase_scene() -> bool:
        import random
        here = _here_id(gs)
        if gs.flags.get("LAB-FLOODED"):
            return False   # the fungicide mist has stopped them
        # Caught: a mutant is already in the room with you.
        if _in_room("RAT-ANT", here):
            return gs.die("\nDozens of hungry eyes fix on you as the mutations "
                          "surround you and begin feasting.")
        # Otherwise they storm into your room, right on your heels.
        for mid in _MONSTER_IDS:
            _move_npc(mid, here)
        _send(gs, "\nThe mutants burst into the room right on your heels! "
              + random.choice(_MONSTER_ENTRANCES))
        return False

    i_chase_scene.__name__ = "i_chase_scene"
    i_chase_scene._state = state
    return i_chase_scene


def make_i_magnet(gs: "GameState", state: dict | None = None):
    """I-MAGNET (compone.zil:1601) — a carried magnet scrambles held cards."""
    def i_magnet() -> bool:
        magnet = _by_zil_id("MAGNET")
        player = gs.player
        if not (magnet and player and magnet.location is player):
            _disable_self(gs, "i_magnet")
            return False
        for cid in _MAGNET_CARDS:
            card = _by_zil_id(cid)
            if card and card.location is player:
                flags = card.db.obj_flags or []
                if "SCRAMBLEDBIT" not in flags:
                    card.db.obj_flags = list(flags) + ["SCRAMBLEDBIT"]
                break
        return False
    i_magnet.__name__ = "i_magnet"
    i_magnet._state = {}
    return i_magnet


def make_i_turnoff_shuttle(gs: "GameState", state: dict | None = None):
    def i_turnoff_shuttle() -> bool:
        gs.flags["SHUTTLE-ON"] = False
        return False
    i_turnoff_shuttle.__name__ = "i_turnoff_shuttle"
    i_turnoff_shuttle._state = {}
    return i_turnoff_shuttle


def make_i_kitchen_door_closes(gs: "GameState", state: dict | None = None):
    """I-KITCHEN-DOOR-CLOSES (globals.zil:1470) — auto-shuts unless you're in it."""
    state = state if state is not None else {"every_turn": False}

    def i_kitchen_door_closes() -> bool:
        if _here_id(gs) == "KITCHEN":
            # Don't shut on the player; convert to an every-turn watcher once.
            if not state["every_turn"]:
                state["every_turn"] = True
                gs.scheduler.enable(gs.scheduler.queue(i_kitchen_door_closes, -1))
            return False
        door = _by_zil_id("KITCHEN-DOOR")
        if door:
            door.db.is_open = False
        if _here_id(gs) == "MESS-HALL":
            _send(gs, "\nThe kitchen door slides quietly closed.")
        _disable_self(gs, "i_kitchen_door_closes")
        return False

    i_kitchen_door_closes.__name__ = "i_kitchen_door_closes"
    i_kitchen_door_closes._state = state
    return i_kitchen_door_closes


# ---------------------------------------------------------------------------
# I-SHUTTLE  (globals.zil:1880) — the maglev driving simulation.
# Each turn the car's velocity changes by the lever setting (+5 up / -5 down);
# the trip counter advances; at counter 24 the car arrives (velocity 0 = clean,
# <20 = crash + car broken, else fatal) at the opposite station.
# ---------------------------------------------------------------------------

_SHUTTLE_SIGNS = {
    2: "\nYou pass a sign which says \"Limit 45.\"",
    12: "\nThe tunnel levels out and begins to slope upward. A sign flashes by "
        "which reads \"Hafwaa Mark -- Beegin Deeseluraashun.\"",
    20: "\nYou pass a sign, surrounded by blinking red lights, which says "
        "\"15.\"",
    21: "\nYou pass a sign, surrounded by blinking red lights, which says "
        "\"10.\"",
    22: "\nYou pass a sign, surrounded by blinking red lights, which says "
        "\"5.\"",
    23: "\nThe shuttle car is approaching a brightly-lit area. As you near it, "
        "you make out the concrete platforms of a shuttle station.",
}


def _shuttle_here_is_alfie(gs) -> bool:
    return _here_id(gs) in ("ALFIE-CONTROL-EAST", "ALFIE-CONTROL-WEST")


def make_i_shuttle(gs: "GameState", state: dict | None = None):
    """I-SHUTTLE (globals.zil:1880)."""
    state = state if state is not None else {}

    def i_shuttle() -> bool:
        f = gs.flags
        if not f.get("SHUTTLE-MOVING"):
            f["SHUTTLE-MOVING"] = True
            _set_door("SHUTTLE-DOOR", is_open=False, visible=True)
            if f.get("LEVER-SETTING") == 1:
                f["SHUTTLE-VELOCITY"] = f.get("SHUTTLE-VELOCITY", 0) + 5
                # Convert the lever's delay-1 kick into an every-turn demon
                # (once) — the -1 demon persists and drives the rest of the trip.
                gs.scheduler.enable(gs.scheduler.queue(i_shuttle, -1))
                _send(gs, "\nThe control cabin door slides shut and the shuttle "
                      "car begins to move forward! The display changes to 5.")
            else:
                _send(gs, "\nThe control cabin door slides shut and the shuttle "
                      "car begins to move backward. You hear a loud crunch as "
                      "the shuttle car runs into the rear wall of the station.")
                _shuttle_stop(gs)
            return False

        # Already moving.
        if f.get("SHUTTLE-VELOCITY", 0) > 0:
            f["SHUTTLE-COUNTER"] = f.get("SHUTTLE-COUNTER", 0) + 1
        lever = f.get("LEVER-SETTING", 0)
        if lever == 1:
            f["SHUTTLE-VELOCITY"] = f.get("SHUTTLE-VELOCITY", 0) + 5
        elif lever == -1:
            if f.get("SHUTTLE-VELOCITY", 0) > 0:
                f["SHUTTLE-VELOCITY"] -= 5
            else:
                f["LEVER-SETTING"] = 0
                _send(gs, "\nThe shuttle car comes to a stop and the lever pops "
                      "back to the central position.")

        if f.get("SHUTTLE-COUNTER") == 24:
            _shuttle_arrive(gs)
        elif f.get("SHUTTLE-VELOCITY", 0) > 0:
            _shuttle_describe_trip(gs)
        return False

    i_shuttle.__name__ = "i_shuttle"
    i_shuttle._state = state
    return i_shuttle


def _shuttle_describe_trip(gs) -> None:
    f = gs.flags
    lead = ("still reads" if f.get("LEVER-SETTING") == 0 else "blinks, and now reads")
    _send(gs, f"\nThe shuttle car continues to move. The display {lead} "
          f"{f.get('SHUTTLE-VELOCITY', 0)}.")
    sign = _SHUTTLE_SIGNS.get(f.get("SHUTTLE-COUNTER"))
    if sign:
        _send(gs, sign)


def _shuttle_stop(gs) -> None:
    f = gs.flags
    f["SHUTTLE-ON"] = False
    f["SHUTTLE-MOVING"] = False
    f["LEVER-SETTING"] = 0
    f["SHUTTLE-VELOCITY"] = 0
    _set_door("SHUTTLE-DOOR", is_open=True, visible=False)
    _disable_self(gs, "i_shuttle")


def _shuttle_arrive(gs) -> bool:
    f = gs.flags
    vel = f.get("SHUTTLE-VELOCITY", 0)
    alfie = _shuttle_here_is_alfie(gs)
    if vel == 0:
        _send(gs, "\nThe shuttle car glides into the station and comes to rest "
              "at the concrete platform. You hear the cabin doors slide open.")
    elif vel < 20:
        f["ALFIE-BROKEN" if alfie else "BETTY-BROKEN"] = True
        _send(gs, "\nThe shuttle car rumbles through the station and smashes "
              "into the wall at the far end. You are thrown forward into the "
              "control panel. Both you and the shuttle car produce unhealthy "
              "crunching sounds as the cabin doors creak slowly open.")
    else:
        _shuttle_stop(gs)
        return gs.die("The shuttle car hurtles past the platforms and rams "
                      "into the wall at the far end of the station. The shuttle "
                      "car is destroyed, but you're in no condition to care.")
    f["SHUTTLE-VELOCITY"] = 0
    f["SHUTTLE-MOVING"] = False
    f["SHUTTLE-COUNTER"] = 0
    f["LEVER-SETTING"] = 0
    f["SHUTTLE-ON"] = False
    _set_door("SHUTTLE-DOOR", is_open=True, visible=False)
    _disable_self(gs, "i_shuttle")
    # The car is now parked at the opposite station.
    key = "ALFIE-AT-KALAMONTEE" if alfie else "BETTY-AT-KALAMONTEE"
    f[key] = not f.get(key)
    return False


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
