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
    results = search_object_attribute(key="zil_id", value=zil_id)
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

def handle_deck_nine(room, character, rarg):
    """DECK-NINE-F (globals.zil:509) — dynamic corridor description.

    For M-LOOK the handler returns the description text (consumed by
    dispatch_room_look); for any other reason arg it is a no-op.
    """
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
    return text


def handle_bio_lab(room, character, rarg):
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
    return text


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


def _arrival_pending(gs, name: str) -> bool:
    """ZIL <GET <INT routine> C-ENABLED?> — is this one-shot already queued?"""
    return any(
        getattr(i.routine, "__name__", None) == name and i.enabled
        for i in gs.scheduler._interrupts
    )


def handle_blue_elevator_button(ctx: ObjAction) -> bool:
    """BLUE-ELEVATOR-BUTTON-F (compone.zil:2548) — call the upper car down."""
    if ctx.verb != "V-PUSH":
        return False
    from world.scheduler_script import get_or_create_game_state_script
    from world.interrupts import make_i_upper_elevator_arrive
    import random

    gs = get_or_create_game_state_script().get_gs()
    if not gs.flags.get("UPPER-ELEVATOR-UP"):
        return False  # car not at top; ZIL falls through to the generic push
    if _arrival_pending(gs, "i_upper_elevator_arrive"):
        ctx.caller.msg("Patience, patience...")
    else:
        intr = gs.scheduler.queue(make_i_upper_elevator_arrive(gs), random.randint(0, 19) + 40)
        gs.scheduler.enable(intr)
        ctx.caller.msg("You hear a faint whirring noise from behind the blue door.")
    return True


def handle_red_elevator_button(ctx: ObjAction) -> bool:
    """RED-ELEVATOR-BUTTON-F (compone.zil:2560) — call the lower car up."""
    if ctx.verb != "V-PUSH":
        return False
    from world.scheduler_script import get_or_create_game_state_script
    from world.interrupts import make_i_lower_elevator_arrive
    import random

    gs = get_or_create_game_state_script().get_gs()
    if gs.flags.get("LOWER-ELEVATOR-UP"):
        return False  # car not at bottom; ZIL falls through to the generic push
    if _arrival_pending(gs, "i_lower_elevator_arrive"):
        ctx.caller.msg("Patience, patience...")
    else:
        intr = gs.scheduler.queue(make_i_lower_elevator_arrive(gs), random.randint(0, 39) + 80)
        gs.scheduler.enable(intr)
        ctx.caller.msg("The red door begins vibrating a bit.")
    return True


def handle_blather(ctx: ObjAction) -> bool:
    """BLATHER-F (globals.zil:739) — Ensign Blather's verb responses."""
    v = ctx.verb
    msg = ctx.caller.msg
    if v in ("V-TALK", "V-HELLO", "V-TELL"):
        msg("Blather shouts \"Speak when you're spoken to, Ensign Seventh "
            "Class!\" He breaks three pencil points in a frenzied rush to give "
            "you more demerits.")
        return True
    if v in ("V-ATTACK", "V-KICK"):
        msg("Blather removes several of your appendages and internal organs.")
        return True
    if v == "V-SALUTE":
        msg("Blather's sneer softens a bit. \"First right thing you've done "
            "today. Only five demerits.\"")
        return True
    if v == "V-THROW":
        item = ctx.nouns[0] if ctx.nouns else "object"
        if ctx.obj.location:
            for held in list(ctx.caller.contents):
                held.move_to(ctx.obj.location, quiet=True, move_type="drop")
                break
        msg(f"The {item} bounces off Blather's bulbous nose. He becomes livid, "
            "orders you to do five hundred push-ups, gives you ten thousand "
            "demerits, and assigns you five years of extra galley duty.")
        return True
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        msg("Ensign Blather is a tall, beefy officer with a tremendous, "
            "misshapen nose. His uniform is perfect in every respect, and the "
            "crease in his trousers could probably slice diamonds in half.")
        return True
    if v == "V-TAKE":
        msg("Blather brushes you away, muttering about suspended shore leave.")
        return True
    return False


def handle_ambassador(ctx: ObjAction) -> bool:
    """AMBASSADOR-F (globals.zil:870) — the alien ambassador's verb responses."""
    v = ctx.verb
    msg = ctx.caller.msg
    if v in ("V-TALK", "V-HELLO", "V-TELL"):
        msg("The ambassador taps his translator, and then touches his center "
            "knee to his left ear (the Blow'k-bibben-Gordoan equivalent of "
            "shrugging).")
        return True
    if v == "V-ASK-FOR" and any("celery" in n.lower() for n in ctx.nouns):
        msg("The ambassador seems willing to let you eat some of it, but I "
            "doubt he wants to part with the entire stalk.")
        return True
    if v in ("V-ATTACK", "V-KICK"):
        msg("The ambassador is startled, and emits an amazing quantity of "
            "slime which spreads across the section of the deck you just "
            "polished.")
        return True
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        msg("The ambassador has around twenty eyes, seven of which are "
            "currently open. Half of his six legs are retracted. Green slime "
            "oozes from multiple orifices in his scaly skin. He speaks through "
            "a mechanical translator slung around his neck.")
        return True
    if v == "V-LISTEN":
        msg("The alien makes a wheezing noise as he breathes.")
        return True
    return False


OBJ_HANDLERS: dict = {
    "FLOYD-F": handle_floyd,
    "BLUE-ELEVATOR-BUTTON-F": handle_blue_elevator_button,
    "RED-ELEVATOR-BUTTON-F": handle_red_elevator_button,
    "BLATHER-F": handle_blather,
    "AMBASSADOR-F": handle_ambassador,
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


def dispatch_room_look(room, character):
    """Return the room's ZIL M-LOOK description text, or None.

    None means the room has no registered ACTION handler (or it produced no
    text for M-LOOK), so the caller should fall back to the static db.desc.
    """
    handler = ROOM_HANDLERS.get(room.db.action)
    if handler is None:
        return None
    result = handler(room, character, M_LOOK)
    return result if isinstance(result, str) else None


def dispatch_object(obj, caller, verb, nouns) -> bool:
    """Give `obj`'s ACTION handler first crack at `verb` (ZIL PRSO semantics).

    Returns True if the handler fully consumed the verb (skip the default),
    False to fall through to the generic verb handler.
    """
    handler = OBJ_HANDLERS.get(obj.db.action)
    if handler is None:
        return False
    return bool(handler(ObjAction(obj=obj, caller=caller, verb=verb, nouns=nouns)))
