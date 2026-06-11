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
                return gs.die(
                    "Unfortunately, you don't seem to have survived the night."
                )
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
                return gs.die("You collapse from extreme thirst and hunger.")
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
                return gs.die(
                    "You finally succumb to the ravages of your illness and "
                    "collapse."
                )
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
