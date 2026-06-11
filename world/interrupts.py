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
