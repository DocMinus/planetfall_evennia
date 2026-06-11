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


# ---------------------------------------------------------------------------
# Subsystem 3 — Card access (globals.zil SLOT-F + card-operated doors)
# ---------------------------------------------------------------------------

_WRONG_CARD = ("A sign flashes \"Inkorekt awtharazaashun "
               "kard...akses deeniid.\"")
_ELEVATOR_ENABLED = "A recorded voice chimes \"Elevator enabled.\""


def _queue(gs, maker_name: str, ticks: int) -> None:
    """Queue a turnoff/close interrupt by maker name (world.interrupts)."""
    import world.interrupts as I
    maker = getattr(I, maker_name)
    gs.scheduler.enable(gs.scheduler.queue(maker(gs), ticks))


def handle_slot(ctx: ObjAction) -> bool:
    """SLOT-F (globals.zil:1370) — slide an authorization card through the slot."""
    caller, v = ctx.caller, ctx.verb
    if v == "V-PUT" and (_prsi(ctx) and _prsi(ctx).db.zil_id == "SLOT"):
        caller.msg("The slot is shallow, so you can't put anything in it. It "
                   "may be possible to slide something through the slot, "
                   "though.")
        return True
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        caller.msg("The slot is about ten centimeters wide, but only about two "
                   "centimeters deep. It is surrounded on its long sides by "
                   "parallel ridges of metal.")
        return True
    if v != "V-SLIDE":
        return False

    # SLIDE <card> THROUGH SLOT — SLOT-F is the slot's (PRSI) action, so the
    # card is the direct object (nouns[0]); ctx.obj is the slot itself.
    from world.verb_commands import resolve_obj
    card = resolve_obj(caller, ctx.nouns[0]) if ctx.nouns else None
    if card is None or card.db.zil_id == "SLOT":
        caller.msg("Slide what through the slot?")
        return True
    gs = _gs()
    here = _here_zil(caller)
    card.move_to(caller, quiet=True, move_type="get")

    if _bit(card, "SCRAMBLEDBIT"):
        caller.msg("A sign flashes \"Magnetik striip randumiizd...konsult "
                   "Prajekt Handbuk abowt propur kaar uv awtharazaashun "
                   "kardz.\"")
        return True

    cid = card.db.zil_id
    if cid == "KITCHEN-CARD":
        if here == "MESS-HALL":
            door = _by_zil_id("KITCHEN-DOOR")
            if door and door.db.is_open:
                caller.msg("Nothing happens.")
            else:
                if door:
                    door.db.is_open = True
                _queue(gs, "make_i_kitchen_door_closes", 50)
                caller.msg("The kitchen door quietly slides open.")
                _floyd_reveal_card(gs)
        else:
            caller.msg(_WRONG_CARD)
    elif cid == "UPPER-ELEVATOR-CARD":
        if here == "UPPER-ELEVATOR":
            gs.flags["UPPER-ELEVATOR-ON"] = True
            _queue(gs, "make_i_turnoff_upper_elevator", 180)
            caller.msg(_ELEVATOR_ENABLED)
            _floyd_reveal_card(gs)
        else:
            caller.msg(_WRONG_CARD)
    elif cid == "LOWER-ELEVATOR-CARD":
        if here == "LOWER-ELEVATOR":
            gs.flags["LOWER-ELEVATOR-ON"] = True
            _queue(gs, "make_i_turnoff_lower_elevator", 200)
            caller.msg(_ELEVATOR_ENABLED)
        else:
            caller.msg(_WRONG_CARD)
    elif cid == "TELEPORTATION-CARD":
        if here in ("BOOTH-1", "BOOTH-2", "BOOTH-3"):
            gs.flags["TELEPORTATION-ON"] = True
            _queue(gs, "make_i_turnoff_teleportation", 30)
            caller.msg("Nothing happens for a moment. Then a light flashes "
                       "\"Redee.\"")
        else:
            caller.msg(_WRONG_CARD)
    elif cid == "MINI-CARD":
        if here == "MINI-BOOTH":
            gs.flags["MINI-ACTIVATED"] = True
            _queue(gs, "make_i_turnoff_mini", 30)
            caller.msg("A melodic high-pitched voice says \"Miniaturization "
                       "and teleportation booth activated. Please type in "
                       "damaged sector number.\"")
        else:
            caller.msg(_WRONG_CARD)
    elif cid == "SHUTTLE-CARD":
        _shuttle_activate(gs, caller)
    else:   # ID-CARD or anything else
        caller.msg(_WRONG_CARD)
    return True


def _floyd_reveal_card(gs) -> None:
    """FLOYD-REVEAL-CARD-F (globals.zil:1432) — Floyd shows off his own card.

    The ZIL gating is an elaborate day/INTERNAL-MOVES/PROB ramp; we keep the
    one-shot CARD-REVEALED guard and a representative probability.
    """
    if not gs or gs.flags.get("CARD-REVEALED"):
        return
    floyd_obj = _by_zil_id("FLOYD")
    player = gs.player
    if not (floyd_obj and player and floyd_obj.location is player.location):
        return
    import random
    day = gs.day
    chance = 5 if day == 2 else 20 if day == 3 else 100 if day > 3 else 0
    if random.randint(1, 100) > chance:
        return
    gs.flags["CARD-REVEALED"] = True
    if not gs.flags.get("CARD-STOLEN"):
        card = _by_zil_id("LOWER-ELEVATOR-CARD")
        if card:
            card.move_to(floyd_obj, quiet=True, move_type="get")
        player.msg("\nFloyd claps his hands with excitement. \"Those cards are "
                   "really neat, huh? Floyd has one for himself--see?\" He "
                   "reaches behind one of his panels and retrieves a "
                   "magnetic-striped card. He waves it exuberantly in the air.")
    else:
        player.msg("\nFloyd bobs up and down with excitement. \"Those cards are "
                   "really neat! Floyd has one, too.\" He begins searching "
                   "through his compartments, but finds nothing. He scratches "
                   "his head and looks confused.")


_SHUTTLE_REC_1 = "A recorded voice says \"Shuttle controls are already activated.\""
_SHUTTLE_REC_2 = ("A recorded voice says \"Use other control cabin. Control "
                  "activation overridden.\"")
_SHUTTLE_REC_3 = ("A recording of a deep male voice says \"Shuttle controls "
                  "activated.\"")
_SHUTTLE_REC_4 = "A recorded voice says \"Shuttle controls are not currently activated.\""


def _shuttle_activate(gs, caller) -> None:
    """SHUTTLE-ACTIVATE (globals.zil:1790) — power up a control cabin."""
    here = _here_zil(caller)
    is_alfie = here in ("ALFIE-CONTROL-EAST", "ALFIE-CONTROL-WEST")
    is_betty = here in ("BETTY-CONTROL-EAST", "BETTY-CONTROL-WEST")
    if not (is_alfie or is_betty):
        caller.msg(_WRONG_CARD)
        return
    if (is_alfie and gs.flags.get("ALFIE-BROKEN")) or \
       (is_betty and gs.flags.get("BETTY-BROKEN")):
        caller.msg("A garbled recording mentions that the shuttle car has "
                   "undergone some damage and that the repair robot has been "
                   "summoned.")
        return
    if gs.internal_moves > 6000:
        caller.msg("A recorded voice explains that using the shuttle car during "
                   "the evening hours requires special authorization.")
        return
    if gs.flags.get("SHUTTLE-ON"):
        caller.msg(_SHUTTLE_REC_1)
        return
    # The cabin only activates from the side away from the car's parked station.
    at_kala = gs.flags.get("ALFIE-AT-KALAMONTEE" if is_alfie else "BETTY-AT-KALAMONTEE")
    east = here.endswith("EAST")
    wrong_side = (east and not at_kala) or (not east and at_kala)
    if wrong_side:
        caller.msg(_SHUTTLE_REC_2)
        return
    gs.flags["SHUTTLE-ON"] = True
    _queue(gs, "make_i_turnoff_shuttle", 80)
    caller.msg(_SHUTTLE_REC_3)


def handle_lever(ctx: ObjAction) -> bool:
    """LEVER-F (globals.zil:1863) — drive the shuttle by setting acceleration."""
    caller, v = ctx.caller, ctx.verb
    gs = _gs()
    if not gs:
        return False
    setting = gs.flags.get("LEVER-SETTING", 0)
    if v in ("V-PUSH", "V-PUSH-UP"):
        if not gs.flags.get("SHUTTLE-ON"):
            caller.msg(_SHUTTLE_REC_4)
        elif setting == 1:
            caller.msg("The lever is already in the upper position.")
        elif setting == 0:
            gs.flags["LEVER-SETTING"] = 1
            _queue(gs, "make_i_shuttle", 1)
            caller.msg("The lever is now in the upper position.")
        else:
            gs.flags["LEVER-SETTING"] = 0
            caller.msg("The lever is now in the central position.")
        return True
    if v in ("V-PULL", "V-PUSH-DOWN"):
        if not gs.flags.get("SHUTTLE-ON"):
            caller.msg(_SHUTTLE_REC_4)
        elif setting == 1:
            gs.flags["LEVER-SETTING"] = 0
            caller.msg("The lever is now in the central position.")
        elif setting == 0:
            if gs.flags.get("SHUTTLE-VELOCITY", 0) == 0:
                caller.msg("The lever immediately pops back to the central "
                           "position.")
            else:
                gs.flags["LEVER-SETTING"] = -1
                _queue(gs, "make_i_shuttle", 1)
                caller.msg("The lever is now in the lower position.")
        else:
            caller.msg("The lever is already in the lower position.")
        return True
    return False


def handle_shuttle_door(ctx: ObjAction) -> bool:
    """SHUTTLE-DOOR-F (globals.zil:1719)."""
    if ctx.verb == "V-OPEN":
        gs = _gs()
        if gs and gs.flags.get("SHUTTLE-MOVING"):
            ctx.caller.msg("A recorded voice says \"Operator should remain in "
                           "control cabin while shuttle car is between "
                           "stations.\"")
        else:
            ctx.caller.msg("Are you sure it isn't?")
        return True
    return False


def handle_control_cabin(room, character, rarg):
    """CONTROL-CABIN-F (globals.zil:1681) — the M-LOOK driving display."""
    if rarg != M_LOOK:
        return False
    gs = _gs()
    setting = _flag(gs, "LEVER-SETTING", 0)
    pos = "center" if setting == 0 else "upper" if setting == 1 else "lower"
    vel = _flag(gs, "SHUTTLE-VELOCITY", 0)
    here = character.location.db.zil_id if character.location else None
    # The window view: a wall when parked at this cabin's home station, else rails.
    alfie_home = {"ALFIE-CONTROL-WEST": "ALFIE-AT-KALAMONTEE",
                  "ALFIE-CONTROL-EAST": None}
    at_wall = False
    if here == "ALFIE-CONTROL-WEST":
        at_wall = _flag(gs, "ALFIE-AT-KALAMONTEE")
    elif here == "BETTY-CONTROL-WEST":
        at_wall = _flag(gs, "BETTY-AT-KALAMONTEE")
    elif here == "ALFIE-CONTROL-EAST":
        at_wall = not _flag(gs, "ALFIE-AT-KALAMONTEE")
    elif here == "BETTY-CONTROL-EAST":
        at_wall = not _flag(gs, "BETTY-AT-KALAMONTEE")
    if at_wall:
        view = "a featureless concrete wall."
    elif _flag(gs, "SHUTTLE-MOVING") and _flag(gs, "SHUTTLE-COUNTER", 0) == 23:
        view = "parallel rails ending at a brightly-lit station ahead."
    else:
        view = ("parallel rails running along the floor of a long tunnel, "
                "vanishing in the distance.")
    return ("This is a small control cabin. A control panel contains a slot, a "
            "lever, and a display. The lever can be set at a central position, "
            "or it could be pushed up to a position labelled \"+\", or pulled "
            "down to a position labelled \"-\". It is currently at the "
            f"{pos} setting. The display, a digital readout, currently reads "
            f"{vel}. Through the cabin window you can see {view}")


# ---------------------------------------------------------------------------
# Subsystem 2 — Survival cures (food / medicine; sleep is in _h_board + interrupts)
# ---------------------------------------------------------------------------

def _carrying(caller, zil_id: str) -> bool:
    return any(o.db.zil_id == zil_id for o in caller.contents)


def _eat_resets_hunger(gs, caller, obj, requeue_ticks: int, flavor: str) -> None:
    """Shared EAT cure: clear hunger, re-arm the hunger clock, eat the food."""
    obj.location = None
    caller.ndb.pf_elapsed = 15
    gs.flags["HUNGER-LEVEL"] = 0
    import world.interrupts as I
    gs.scheduler.enable(gs.scheduler.queue(I.make_i_hunger_warnings(gs), requeue_ticks))
    caller.msg(flavor)


_GOO_FLAVOR = {
    "RED-GOO": "scrumptious cherry pie",
    "BROWN-GOO": "delicious Nebulan fungus pudding",
    "GREEN-GOO": "yummy lima beans",
}


def handle_goo(ctx: ObjAction) -> bool:
    """GOO-F (globals.zil:1063) — the survival-kit food blobs."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-EAT":
        if gs and gs.flags.get("HUNGER-LEVEL", 0) == 0:
            caller.msg("Thanks, but you're not hungry.")
        elif not _carrying(caller, "FOOD-KIT"):
            caller.msg("You're not holding the survival kit.")
        else:
            flavor = _GOO_FLAVOR.get(obj.db.zil_id, "food")
            _eat_resets_hunger(gs, caller, obj, 1450,
                               f"Mmmm...that tasted just like {flavor}.")
        return True
    if v in ("V-TAKE", "V-DROP"):
        lead = ("It would ooze through your fingers" if v == "V-TAKE"
                else "The goo, being gooey, sticks where it is")
        caller.msg(lead + ". You'll have to eat it right from the survival kit.")
        return True
    return False


def handle_high_protein(ctx: ObjAction) -> bool:
    """HIGH-PROTEIN-F (compone.zil:844) — the canteen's protein drink."""
    caller, v = ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-EAT":
        if not _carrying(caller, "CANTEEN"):
            caller.msg("You're not holding the canteen.")
        elif gs and gs.flags.get("HUNGER-LEVEL", 0) == 0:
            caller.msg("Thanks, but you're not hungry.")
        else:
            _eat_resets_hunger(gs, caller, ctx.obj, 3600,
                               "Mmmm....that was good. It certainly quenched "
                               "your thirst and satisfied your hunger.")
        return True
    return False


def handle_medicine(ctx: ObjAction) -> bool:
    """MEDICINE-F (comptwo.zil:169) — bitter medicine slows the sickness."""
    caller, v = ctx.caller, ctx.verb
    gs = _gs()
    bottle_held = _carrying(caller, "MEDICINE-BOTTLE")
    bottle = _by_zil_id("MEDICINE-BOTTLE")
    if v in ("V-TASTE", "V-EAT", "V-POUR") and not bottle_held:
        caller.msg("You're not holding the medicine bottle.")
        return True
    if v in ("V-TASTE", "V-EAT", "V-POUR") and bottle and not bottle.db.is_open:
        caller.msg("The bottle is closed.")
        return True
    if v == "V-TASTE":
        caller.msg("It tastes fairly bitter.")
        return True
    if v == "V-EAT":
        ctx.obj.location = None
        caller.ndb.pf_elapsed = 15
        if gs:
            gs.flags["SICKNESS-LEVEL"] = max(0, gs.flags.get("SICKNESS-LEVEL", 0) - 2)
        caller.msg("The medicine tasted extremely bitter.")
        return True
    return False


def handle_kitchen_door(ctx: ObjAction) -> bool:
    """KITCHEN-DOOR-F (compone.zil:772) — opens only via the card slot."""
    if ctx.verb == "V-OPEN":
        ctx.caller.msg("A light flashes \"Pleez yuuz kitcin akses kard.\"")
        return True
    return False


def handle_conference_door(ctx: ObjAction) -> bool:
    """CONFERENCE-DOOR-F (compone.zil:291) — combination-locked door."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    if v == "V-OPEN":
        if obj.db.is_open:
            caller.msg("It's already open.")
        elif _here_zil(caller) == "REC-AREA":
            caller.msg("The door is locked. You probably have to turn the dial "
                       "to some number to open it.")
        else:
            caller.msg("The door seems to be locked from the other side.")
        return True
    if v == "V-CLOSE":
        if obj.db.is_open:
            obj.db.is_open = False
            caller.msg("The door closes and you hear a click as it locks.")
        else:
            caller.msg("It's already closed.")
        return True
    return False


OBJ_HANDLERS: dict = {
    "FLOYD-F": handle_floyd,
    "PADLOCK-F": handle_padlock,
    "CREVICE-F": handle_crevice,
    "KEY-F": handle_key,
    "LADDER-F": handle_ladder,
    "RIFT-F": handle_rift,
    "SLOT-F": handle_slot,
    "KITCHEN-DOOR-F": handle_kitchen_door,
    "CONFERENCE-DOOR-F": handle_conference_door,
    "GOO-F": handle_goo,
    "HIGH-PROTEIN-F": handle_high_protein,
    "MEDICINE-F": handle_medicine,
    "LEVER-F": handle_lever,
    "SHUTTLE-DOOR-F": handle_shuttle_door,
}
ROOM_HANDLERS.update({
    "CONTROL-CABIN-F": handle_control_cabin,
})
OBJ_HANDLERS.update({
    "BLUE-ELEVATOR-BUTTON-F": handle_blue_elevator_button,
    "RED-ELEVATOR-BUTTON-F": handle_red_elevator_button,
    "BLATHER-F": handle_blather,
    "AMBASSADOR-F": handle_ambassador,
})


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
