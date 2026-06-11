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


# --- object-bit helpers (ZIL FSET? / FSET / FCLEAR) ------------------------

def _bit(obj, flag: str) -> bool:
    return flag in (obj.db.obj_flags or [])


def _set_bit(obj, flag: str, on: bool = True) -> None:
    flags = [f for f in (obj.db.obj_flags or []) if f != flag]
    if on:
        flags.append(flag)
    obj.db.obj_flags = flags


def _here_zil(caller) -> str | None:
    return caller.location.db.zil_id if caller.location else None


def _prsi(ctx) -> object | None:
    """Resolve the indirect object (second noun) of an ObjAction, if any."""
    if len(ctx.nouns) < 2:
        return None
    from world.verb_commands import resolve_obj
    return resolve_obj(ctx.caller, ctx.nouns[1])


def _gs():
    from world.scheduler_script import get_game_state_script
    script = get_game_state_script()
    return script.get_gs() if script else None


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
# Further room handlers are registered with ROOM_HANDLERS.update(...) below, as
# their handler functions are defined (Subsystem 1 onward).


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
        # ZIL JIGS-UP (globals.zil:751): attacking Blather is fatal.
        from world.scheduler_script import get_game_state_script
        script = get_game_state_script()
        gs = script.get_gs() if script else None
        if gs:
            gs.die("Blather removes several of your appendages and internal "
                   "organs.")
        else:
            msg("Blather removes several of your appendages and internal "
                "organs.")
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


# ---------------------------------------------------------------------------
# Subsystem 1 — Brig escape (compone.zil)
# ---------------------------------------------------------------------------

def handle_padlock(ctx: ObjAction) -> bool:
    """PADLOCK-F (compone.zil:551) — the brig-door padlock."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    if _here_zil(caller) == "BRIG":
        caller.msg("You can't see or reach the lock from inside the cell.")
        return True
    if v in ("V-UNLOCK", "V-OPEN", "V-OPEN-WITH"):
        if obj.db.is_open:
            caller.msg("The padlock is already unlocked.")
            return True
        prsi = _prsi(ctx)
        if prsi is None:
            caller.msg("You can't open it with your hands.")
        elif prsi.db.zil_id == "KEY":
            if _bit(obj, "MUNGEDBIT"):
                caller.msg("Tsk, tsk ... the padlock seems to be fused shut.")
            else:
                obj.db.is_open = True
                caller.msg("The padlock springs open.")
        else:
            caller.msg("That doesn't work.")
        return True
    if v in ("V-CLOSE", "V-LOCK"):
        if obj.db.is_open:
            obj.db.is_open = False
            caller.msg("The padlock closes with a sharp click.")
        else:
            caller.msg("The padlock is already locked.")
        return True
    if v == "V-TAKE":
        gs = _gs()
        if gs and not gs.flags.get("PADLOCK-REMOVED"):
            if obj.db.is_open:
                gs.flags["PADLOCK-REMOVED"] = True
                _set_bit(obj, "TRYTAKEBIT", False)
                _set_bit(obj, "NDESCBIT", False)
                return False   # fall through to generic take (now succeeds)
            caller.msg("The padlock is locked to the door.")
            return True
    if v == "V-MUNG":
        caller.msg("And, as we go into the next round, it's Padlock 1, "
                   "Adventurer 0...")
        return True
    return False


def handle_crevice(ctx: ObjAction) -> bool:
    """CREVICE-F (compone.zil:952) — the key is hidden here."""
    caller, v = ctx.caller, ctx.verb
    if v == "V-REACH":
        caller.msg("The crevice is too narrow to reach into.")
        return True
    if v in ("V-LOOK-INSIDE", "V-EXAMINE", "V-LOOK-CRETIN", "V-SEARCH"):
        key = _by_zil_id("KEY")
        if key and _bit(key, "TOUCHBIT"):
            caller.msg("Nothing there but bunches of dust.")
        elif key:
            _set_bit(key, "INVISIBLE", False)
            caller.msg("Lying at the bottom of the narrow crack, partly "
                       "covered by layers of dust, is a shiny steel key!")
        return True
    return False


def handle_key(ctx: ObjAction) -> bool:
    """KEY-F (compone.zil:974) — can't be grabbed by hand from the crevice."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    if v in ("V-TAKE", "V-ZATTRACT", "V-MOVE") and not _bit(obj, "TOUCHBIT"):
        prsi = _prsi(ctx)
        if prsi and prsi.db.zil_id == "PLIERS":
            caller.msg("These are heavy-duty pliers, too large to reach into "
                       "this narrow crack.")
        elif prsi and prsi.db.zil_id == "MAGNET":
            # The magnet fishes the key out (ZIL PERFORM V?ATTRACT MAGNET KEY).
            _set_bit(obj, "TOUCHBIT", True)
            _set_bit(obj, "INVISIBLE", False)
            obj.move_to(caller, quiet=True, move_type="get")
            caller.msg("The magnet attracts the key, and you pluck it from the "
                       "crevice.")
        elif prsi:
            caller.msg("Nice try.")
        else:
            caller.msg("Either the crevice is too narrow, or your fingers are "
                       "too large.")
        return True
    return False


def handle_ladder(ctx: ObjAction) -> bool:
    """LADDER-F (compone.zil:649) — extend / collapse / span the rift."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    extended = gs.flags.get("LADDER-EXTENDED") if gs else False
    spanning = gs.flags.get("LADDER-FLAG") if gs else False
    here = _here_zil(caller)

    if v == "V-TAKE":
        if extended:
            caller.msg("You can't possibly carry the ladder while it's "
                       "extended.")
            return True
        return False   # generic take
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        if extended:
            caller.msg("It is a heavy-duty ladder built of sturdy aluminum "
                       "tubing. It is currently extended to its full length of "
                       "about 8 meters, but could be collapsed to a shorter "
                       "length for easier carrying.")
        else:
            caller.msg("It is a heavy-duty ladder built of sturdy aluminum "
                       "tubing. It is currently collapsed and is around "
                       "two-and-a-half meters long, but if extended would "
                       "obviously be much longer.")
        return True
    if v == "V-OPEN":   # extend
        if extended:
            caller.msg("The ladder is already extended.")
        elif here in ("STORAGE-EAST", "STORAGE-WEST", "BOOTH-2",
                      "UPPER-ELEVATOR", "LOWER-ELEVATOR"):
            caller.msg("You can't extend the ladder in this tiny space!")
        elif obj.location is caller:
            caller.msg("You couldn't possibly extend the ladder while you're "
                       "holding it.")
        else:
            _set_bit(obj, "TRYTAKEBIT", True)
            gs.flags["LADDER-EXTENDED"] = True
            ctx.caller.ndb.pf_elapsed = 36
            caller.msg("The ladder extends to a length of around eight meters.")
        return True
    if v == "V-CLOSE":   # collapse
        if not extended:
            caller.msg("The ladder is already in its collapsed state.")
            return True
        ctx.caller.ndb.pf_elapsed = 21
        if spanning:
            gs.flags["LADDER-FLAG"] = False
            obj.location = None
            caller.msg("As the ladder shortens it plunges into the rift.")
        else:
            gs.flags["LADDER-EXTENDED"] = False
            _set_bit(obj, "TRYTAKEBIT", False)
            caller.msg("The ladder collapses to a length of around "
                       "two-and-a-half meters.")
        return True
    if v in ("V-SPAN", "V-ATTRACT"):
        prsi = _prsi(ctx)
        if prsi and prsi.db.zil_id == "RIFT":
            if spanning:
                caller.msg("The ladder already spans the rift.")
            elif extended:
                gs.flags["LADDER-FLAG"] = True
                _set_bit(obj, "NDESCBIT", True)
                caller.msg("The ladder swings out across the rift and comes to "
                           "rest on the far edge, spanning the precipice.")
            else:
                obj.location = None
                caller.msg("The ladder, far too short to reach the other edge "
                           "of the rift, plunges into the rift and is lost "
                           "forever.")
            return True
    if v in ("V-CLIMB-UP", "V-CLIMB-FOO"):
        if spanning:
            caller.msg("You can't climb a horizontal ladder!")
            return True
        if obj.location is caller:
            caller.msg("That would be a neat trick, considering that you're "
                       "holding it.")
            return True
    return False


def handle_rift(ctx: ObjAction) -> bool:
    """RIFT-F (compone.zil:1075) — leaping in is fatal; things thrown in vanish."""
    caller, v = ctx.caller, ctx.verb
    if v in ("V-LEAP", "V-JUMP"):
        gs = _gs()
        if gs:
            gs.die("You get a brief (but much closer) view of the sharp and "
                   "nasty rocks at the bottom of the rift.")
        return True
    if v == "V-PUT":
        prsi = _prsi(ctx)
        thrown = ctx.obj if ctx.obj.db.zil_id != "RIFT" else None
        # PUT <thing> IN RIFT: PRSO is the thing, PRSI the rift.
        if prsi and prsi.db.zil_id == "RIFT":
            item = ctx.obj
            if item.db.zil_id == "LASER":
                from world.scheduler_script import get_game_state_script
                gs = _gs()
                _disable_interrupt(gs, "i_warmth")
            item.location = None
            if item.db.zil_id == "SCRUB-BRUSH":
                caller.msg("You watch with tremendous satisfaction as the "
                           "brush is lost forever.")
            else:
                caller.msg(f"The {item.key} sails gracefully into the rift.")
            return True
    return False


def _disable_interrupt(gs, name: str) -> None:
    if not gs:
        return
    for i in gs.scheduler._interrupts:
        if getattr(i.routine, "__name__", None) == name:
            gs.scheduler.disable(i)


# Room handlers for the admin corridors -------------------------------------

def handle_admin_corridor_s(room, character, rarg):
    """ADMIN-CORRIDOR-S-F (compone.zil:936) — M-END glint hint at the key."""
    if rarg == M_END:
        key = _by_zil_id("KEY")
        if key and _bit(key, "INVISIBLE"):
            import random
            if random.randint(1, 100) <= 20:
                character.msg("You catch, out of the corner of your eye, a "
                              "glint of light from the direction of the floor.")
        return True
    return False


def handle_admin_corridor(room, character, rarg):
    """ADMIN-CORRIDOR-F (compone.zil:1008) — rift room, ladder appears if laid."""
    gs = _gs()
    spanning = _flag(gs, "LADDER-FLAG")
    if rarg == M_ENTER and spanning:
        ladder = _by_zil_id("LADDER")
        if ladder and room:
            ladder.move_to(room, quiet=True, move_type="move")
        return True
    if rarg == M_LOOK:
        text = ("The hallway, in fact the entire building, has been rent apart "
                "here, presumably by seismic upheaval. You can see the sky "
                "through the severed roof above, and the ground is thick with "
                "rubble. To the north is a gaping rift, at least eight meters "
                "across and thirty meters deep. ")
        if spanning:
            text += "A metal ladder spans the rift. "
        text += "A wide doorway, labelled \"Sistumz Moniturz,\" leads west."
        return text
    return False


def handle_admin_corridor_n(room, character, rarg):
    """ADMIN-CORRIDOR-N-F (compone.zil:1039)."""
    gs = _gs()
    spanning = _flag(gs, "LADDER-FLAG")
    if rarg == M_ENTER and spanning:
        ladder = _by_zil_id("LADDER")
        if ladder and room:
            ladder.move_to(room, quiet=True, move_type="move")
        return True
    if rarg == M_LOOK:
        text = ("The corridor ends here. Portals lead west, north, and east. "
                "Signs above these portals read, respectively, "
                "\"Administraativ Awfisiz,\" \"Tranzportaashun Suplii,\" and "
                "\"Plan Ruum.\" To the south is a wide rift")
        if spanning:
            text += ", spanned by a metal ladder,"
        text += " separating this area from the rest of the building."
        return text
    return False


ROOM_HANDLERS.update({
    "ADMIN-CORRIDOR-S-F": handle_admin_corridor_s,
    "ADMIN-CORRIDOR-F":   handle_admin_corridor,
    "ADMIN-CORRIDOR-N-F": handle_admin_corridor_n,
})


OBJ_HANDLERS: dict = {
    "FLOYD-F": handle_floyd,
    "PADLOCK-F": handle_padlock,
    "CREVICE-F": handle_crevice,
    "KEY-F": handle_key,
    "LADDER-F": handle_ladder,
    "RIFT-F": handle_rift,
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
