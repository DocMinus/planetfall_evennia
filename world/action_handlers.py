"""
Phase 6 — Room and Object action handler dispatch.

Mirrors ZIL's per-room / per-object ACTION routines.  In ZIL, a room's ACTION is
called with a "reason argument" (M-LOOK / M-ENTER / M-END / M-BEG) while an
object's ACTION gets first crack at the current verb (PRSO) before the generic
verb handler runs.

This module exposes two registries and the helpers that drive them:

    ROOM_HANDLERS[action_name](room, character, rarg) -> bool
        Returns True if the handler produced output for that `rarg` (so the
        caller can suppress the default behaviour, e.g. the stock room desc).

    OBJ_HANDLERS[action_name](ctx: ObjAction) -> bool
        Returns True if the handler fully consumed the verb (so the generic
        verb handler is skipped), False to fall through to the default.

Wiring:
    typeclasses/rooms.py     -> on_enter() calls dispatch_room(..., M_ENTER)
    world/verb_commands.py   -> _h_look() calls dispatch_room(..., M_LOOK);
                                command func() calls dispatch_object() before
                                the generic verb handler.
"""
from __future__ import annotations

from dataclasses import dataclass

# ZIL reason-argument constants passed to a room's ACTION routine.
M_LOOK = "M-LOOK"     # describing the room (replaces the default desc)
M_ENTER = "M-ENTER"   # player just walked in
M_END = "M-END"       # end-of-turn room demon (chase scenes, etc.)
M_BEG = "M-BEG"       # before the verb is dispatched in this room


def _by_zil_id(zil_id: str):
    """Return the first Evennia object whose db.zil_id matches, or None."""
    from evennia.utils.search import search_object_attribute
    results = search_object_attribute(key="zil_id", strvalue=zil_id)
    return results[0] if results else None


def _ddesc(zil_id: str) -> str:
    """ZIL DDESC: 'open' / 'closed' for a door object, by ZIL id."""
    door = _by_zil_id(zil_id)
    return "open" if (door and door.db.is_open) else "closed"


def _flag(gs, name: str, default=False):
    """
    Read a GameState flag tolerantly.

    The flags dict is formally introduced in Phase 7; until then this returns
    `default`, so room/object handlers can be written against the final API now.
    """
    return getattr(gs, "flags", {}).get(name, default) if gs else default


# ---------------------------------------------------------------------------
# Room handlers — handler(room, character, rarg) -> bool
# ---------------------------------------------------------------------------

def handle_deck_nine(room, character, rarg) -> bool:
    """DECK-NINE-F (globals.zil:509) — dynamic corridor description."""
    if rarg != M_LOOK:
        return False
    gangway_open = _ddesc("GANGWAY-DOOR") == "open"
    text = (
        "This is a featureless corridor similar to every other corridor on the "
        "ship. It curves away to starboard, and a gangway leads up"
    )
    text += "." if gangway_open else (
        ", but both of these are blocked by closed bulkheads."
    )
    text += (
        " To port is the entrance to one of the ship's primary escape pods. "
        f"The pod bulkhead is {_ddesc('POD-DOOR')}."
    )
    character.msg(text)
    return True


def handle_bio_lab(room, character, rarg) -> bool:
    """BIO-LAB-F (comptwo.zil:2049) — lighting-dependent description.

    The M-END chase-scene branch is endgame logic deferred to Phase 11.
    """
    if rarg != M_LOOK:
        return False
    from world.scheduler_script import get_game_state_script
    script = get_game_state_script()
    gs = script.get_gs() if script else None
    text = (
        "This is a huge laboratory filled with many biological experiments. "
        "The lighting is "
    )
    if _flag(gs, "LAB-LIGHTS-ON"):
        text += "bright."
    else:
        text += (
            "dim, and a faint blue glow comes from a gaping crack in the "
            "northern wall."
        )
    text += " Some of the experiments seem to be out of control..."
    character.msg(text)
    return True


ROOM_HANDLERS: dict = {
    "DECK-NINE-F": handle_deck_nine,
    "BIO-LAB-F":   handle_bio_lab,
}


# ---------------------------------------------------------------------------
# Object handlers — handler(ctx: ObjAction) -> bool
# ---------------------------------------------------------------------------

@dataclass
class ObjAction:
    """Context handed to an object ACTION handler (mirrors ZIL PRSO/verb)."""
    obj: object        # the Evennia object the verb is directed at (PRSO)
    caller: object     # the Character issuing the command
    verb: str          # ZIL handler name, e.g. "V-LAMP-ON"
    nouns: list        # raw noun phrases parsed from the command


def _floyd_is_on(floyd_obj) -> bool:
    """Floyd is 'on' once ACTORBIT has been set (mirrors ZIL RLANDBIT)."""
    return "ACTORBIT" in (floyd_obj.db.obj_flags or [])


def handle_floyd(ctx: ObjAction) -> bool:
    """FLOYD-F (compone.zil:1852) — activation puzzle + de-activation.

    Only the on/off and examine branches are implemented here; the rich
    conversational branches (GIVE/SHOW/THROUGH/FOLLOW) remain Phase 11/12 work
    and fall through to the generic handlers by returning False.
    """
    floyd = ctx.obj
    caller = ctx.caller
    on = _floyd_is_on(floyd)

    if ctx.verb == "V-LAMP-ON":
        if on:
            caller.msg("He's already been activated.")
            return True
        from world.scheduler_script import get_game_state_script
        script = get_game_state_script()
        if script:
            fnpc = script.get_floyd()
            fnpc.activate()
            # ZIL FLOYD-SCORE-FLAG: +2 the first time Floyd is switched on.
            if not fnpc.score_flag:
                fnpc.score_flag = True
                script.get_gs().add_score(2)
        floyd.db.obj_flags = list(floyd.db.obj_flags or []) + ["ACTORBIT", "RLANDBIT"]
        caller.msg("Nothing happens.")
        return True

    if ctx.verb == "V-LAMP-OFF":
        if not on:
            caller.msg("The robot doesn't seem to be on.")
            return True
        _deactivate_floyd(floyd, caller)
        return True

    if ctx.verb in ("V-EXAMINE", "V-LOOK-CRETIN") and not on:
        caller.msg(
            "The de-activated robot is leaning against the wall, its head "
            "lolling to the side. It is short, and seems to be equipped for "
            "general-purpose work. It has apparently been turned off."
        )
        return True

    # Anything else falls through to the generic verb handler.
    return False


def _deactivate_floyd(floyd, caller) -> None:
    """Turn Floyd off: drop his cargo, disable his interrupt, mourn."""
    from world.scheduler_script import get_game_state_script
    script = get_game_state_script()
    if script:
        fnpc = script.get_floyd()
        fnpc.gs.scheduler.remove(fnpc.i_floyd)

    floyd.db.obj_flags = [
        f for f in (floyd.db.obj_flags or []) if f not in ("ACTORBIT", "RLANDBIT")
    ]

    dropped = list(floyd.contents)
    for item in dropped:
        item.move_to(caller.location, quiet=True, move_type="drop")

    msg = (
        "Floyd, shocked by this betrayal from his new-found friend, whimpers "
        "and keels over"
    )
    msg += ", dropping what he was carrying." if dropped else "."
    caller.msg(msg)


OBJ_HANDLERS: dict = {
    "FLOYD-F": handle_floyd,
}


# ---------------------------------------------------------------------------
# Dispatch helpers — called from typeclasses / verb commands
# ---------------------------------------------------------------------------

def dispatch_room(room, character, rarg) -> bool:
    """Run `room`'s ACTION handler for the given reason arg, if registered.

    Returns True if a handler ran and produced output for that `rarg`.
    """
    handler = ROOM_HANDLERS.get(room.db.action)
    if handler is None:
        return False
    return bool(handler(room, character, rarg))


def dispatch_object(obj, caller, verb, nouns) -> bool:
    """Give `obj`'s ACTION handler first crack at `verb` (ZIL PRSO semantics).

    Returns True if the handler fully consumed the verb (skip the default),
    False to fall through to the generic verb handler.
    """
    handler = OBJ_HANDLERS.get(obj.db.action)
    if handler is None:
        return False
    return bool(handler(ObjAction(obj=obj, caller=caller, verb=verb, nouns=nouns)))
