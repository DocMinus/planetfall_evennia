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
    """BIO-LAB-F (comptwo.zil:2049) — description + the mutant chase (M-END)."""
    gs = _gs()
    if rarg == M_LOOK:
        text = ("This is a huge laboratory filled with many biological "
                "experiments. The lighting is ")
        if _flag(gs, "LAB-LIGHTS-ON"):
            text += "bright."
        else:
            text += ("dim, and a faint blue glow comes from a gaping crack in "
                     "the northern wall.")
        text += " Some of the experiments seem to be out of control..."
        return text
    if rarg == M_END and gs:
        # Standing in the Bio Lab triggers the chase; the fungicide mist (if
        # the lab is flooded) stuns the mutants — but only a gas-masked player
        # survives the mist.
        if not any(getattr(i.routine, "__name__", None) == "i_chase_scene"
                   for i in gs.scheduler._interrupts):
            import world.interrupts as I
            gs.scheduler.enable(gs.scheduler.queue(I.make_i_chase_scene(gs), -1))
        _disable_interrupt(gs, "i_sleep_warnings")
        if gs.flags.get("LAB-FLOODED"):
            mask = _by_zil_id("GAS-MASK")
            character.msg("The air is filled with mist, which is affecting the "
                          "mutants. They appear to be stunned and confused, but "
                          "are slowly recovering.")
            if not (mask and _bit(mask, "WORNBIT")):
                gs.die(" Unfortunately, you don't seem to be that hardy.")
        else:
            gs.die("The mutants attack you and rip you to shreds within "
                   "seconds.")
        return True
    return False


def handle_lab_uniform(ctx: ObjAction) -> bool:
    """LAB-UNIFORM-F (comptwo.zil:1633) — disguise; pocket hides the tele card."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        state = "open" if obj.db.is_open else "closed"
        caller.msg("It is a plain lab uniform. The logo above the pocket "
                   "depicts a flame burning above some kind of sleep chamber. "
                   f"The pocket is {state}.")
        return True
    if v in ("V-SEARCH", "V-OPEN", "V-LOOK-INSIDE"):
        if not (gs and gs.flags.get("UNIFORM-OPENED")):
            obj.db.is_open = True
            gs.flags["UNIFORM-OPENED"] = True
            for cid in ("TELEPORTATION-CARD", "COMBINATION-PAPER"):
                item = _by_zil_id(cid)
                if item:
                    item.move_to(obj, quiet=True, move_type="move")
            caller.msg("You discover a small piece of paper and a teleportation "
                       "access card in the pocket of the uniform.")
        elif any(o for o in obj.contents):
            caller.msg("Opening the uniform's pocket reveals its contents.")
        else:
            caller.msg("The pocket is empty.")
        return True
    if v == "V-WEAR":
        patrol = _by_zil_id("PATROL-UNIFORM")
        if patrol and _bit(patrol, "WORNBIT"):
            caller.msg("It won't fit on top of the Patrol uniform.")
            return True
    return False


def handle_lab_desk(ctx: ObjAction) -> bool:
    """LAB-DESK-F (comptwo.zil:2268) — search reveals the fungicide memo."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    if v in ("V-EXAMINE", "V-SEARCH") and not _bit(obj, "TOUCHBIT"):
        _set_bit(obj, "TOUCHBIT", True)
        memo = _by_zil_id("MEMO")
        if memo:
            memo.move_to(caller, quiet=True, move_type="get")
        caller.msg("After inspecting the various papers on the desk, you find "
                   "only one item of interest, a memo of some sort.")
        return True
    return False


def handle_lab_office(room, character, rarg):
    """LAB-OFFICE-F (comptwo.zil:2224) — M-LOOK passthrough (static desc)."""
    return False


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

    # ZIL FLOYD-F ASK-FOR (compone.zil:2056): Floyd hands over what he holds.
    if ctx.verb == "V-ASK-FOR":
        item = _prsi(ctx)
        if item is not None and item.location is floyd:
            item.move_to(caller, quiet=True, move_type="get")
            caller.msg(f"\"Okay,\" says Floyd, handing you the {item.key}, "
                       "\"but only because you're Floyd's best friend.\"")
        else:
            caller.msg("Floyd doesn't appear to have that.")
        return True

    # ZIL FLOYD-F (compone.zil:2082): searching the de-activated robot turns up
    # his own lower-elevator card — the deterministic "take it from off-Floyd"
    # route (CARD-STOLEN), distinct from the day-2 friendly reveal.
    if ctx.verb in ("V-SEARCH", "V-LOOK-INSIDE") and not on:
        gs = _gs()
        if gs and not gs.flags.get("CARD-REVEALED") and not gs.flags.get("CARD-STOLEN"):
            card = _by_zil_id("LOWER-ELEVATOR-CARD")
            if card:
                _set_bit(card, "INVISIBLE", False)
                card.move_to(caller, quiet=True, move_type="get")
                gs.score_object(card)
                gs.flags["CARD-STOLEN"] = True
                caller.msg("In one of the robot's compartments you find and "
                           "take a magnetic-striped card embossed \"Loowur "
                           "Elavaatur Akses Kard.\"")
                return True
        caller.msg("Your search discovers nothing in the robot's compartments "
                   "except a single crayon which you leave where you found it.")
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


# ---------------------------------------------------------------------------
# Subsystem 10 — Scenery & flavour (globals.zil); the distinctive responses,
# including the death traps. Anything not handled falls through to the generic
# verb handlers, which is almost always right for pure scenery.
# ---------------------------------------------------------------------------

def handle_celery(ctx: ObjAction) -> bool:
    """CELERY-F (globals.zil:791) — the ambassador's celery is fatal to eat."""
    caller, v = ctx.caller, ctx.verb
    if v == "V-EAT":
        gs = _gs()
        if gs:
            gs.die("Oops. Looks like Blow'k-Bibben-Gordoan metabolism is not "
                   "compatible with our own. You die of all sorts of "
                   "convulsions.")
        return True
    if v == "V-TAKE":
        caller.msg("The ambassador seems perturbed by your lack of normal "
                   "protocol.")
        return True
    return False


def handle_ground(ctx: ObjAction) -> bool:
    """GROUND-F (globals.zil:62)."""
    caller, v = ctx.caller, ctx.verb
    if v in ("V-CLIMB-ON", "V-BOARD", "V-SIT"):
        caller.ndb.pf_elapsed = 28
        caller.msg("You sit down on the floor. After a brief rest, you stand "
                   "again.")
        return True
    if v in ("V-EXAMINE", "V-LOOK-CRETIN") and _here_zil(caller) == "ADMIN-CORRIDOR-S":
        caller.msg("A narrow, jagged crevice runs across the floor.")
        return True
    return False


def handle_window(ctx: ObjAction) -> bool:
    """WINDOW-F (globals.zil:83) — views; diving through the balcony is fatal."""
    caller, v = ctx.caller, ctx.verb
    here = _here_zil(caller)
    if v == "V-THROUGH" and here == "BALCONY":
        gs = _gs()
        if gs:
            gs.die("You slice yourself to ribbons on the broken windows and "
                   "then plummet into the swirling ocean below. Very clever.")
        return True
    if v == "V-OPEN":
        caller.msg("This window doesn't open.")
        return True
    if v == "V-LOOK-INSIDE":
        views = {
            "BIO-LAB": "You see the Bio Lock.",
            "BALCONY": "Water. Lots and lots of water.",
            "HELICOPTER": "You see the helipad and the ocean beyond.",
            "LARGE-OFFICE": "You can see the dormitories and other parts of the "
                            "complex in the distance. Water is visible in every "
                            "direction.",
        }
        caller.msg(views.get(here, "You see little of interest through it."))
        return True
    return False


def handle_cliff(ctx: ObjAction) -> bool:
    """CLIFF-F (globals.zil:143) — leaping off the West Wing cliff is fatal."""
    caller, v = ctx.caller, ctx.verb
    if _here_zil(caller) == "WEST-WING" and v in ("V-LEAP", "V-JUMP"):
        gs = _gs()
        if gs:
            gs.die("Brilliant idea!")
        return True
    if v in ("V-CLIMB-UP", "V-CLIMB-FOO"):
        from world.verb_commands import _h_walk
        _h_walk(caller, ["up"])
        caller.ndb.pf_clock_handled = True
        return True
    if v == "V-CLIMB-DOWN":
        from world.verb_commands import _h_walk
        _h_walk(caller, ["down"])
        caller.ndb.pf_clock_handled = True
        return True
    return False


def handle_ocean(ctx: ObjAction) -> bool:
    """OCEAN-F (globals.zil:167)."""
    caller, v = ctx.caller, ctx.verb
    if v in ("V-TAKE", "V-THROUGH", "V-RUB"):
        caller.msg("You can't reach the ocean from here.")
        return True
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        caller.msg("It stretches as far as you can see.")
        return True
    return False


def handle_tables(ctx: ObjAction) -> bool:
    """TABLES-F (globals.zil:188) — the mess-hall look-under gag."""
    caller, v = ctx.caller, ctx.verb
    if v in ("V-LOOK-UNDER", "V-LOOK-BEHIND") and _here_zil(caller) == "MESS-HALL":
        caller.msg("Wow!!! Under the table are three keys, a sack of food, a "
                   "reactor elevator access pass, one hundred gold pieces ... "
                   "Just kidding. Actually, there's nothing there.")
        return True
    return False


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


_ELEVATOR_STARTS = ("The elevator door slides shut. After a moment, you feel a "
                    "sensation of vertical movement.")


def handle_elevator_button(ctx: ObjAction) -> bool:
    """ELEVATOR-BUTTON-F (compone.zil:2607) — the inside Up/Down button.

    Moves the car between levels: it must be the enabled car, in this elevator,
    standing at the right end of the shaft, and not already mid-trip. Pushing
    starts the trip (closes the door, queues I-*-ELEVATOR-TRIP), which arrives
    a few turns later to re-open the door at the far level.
    """
    caller, v = ctx.caller, ctx.verb
    if v == "V-PUSH":
        caller.msg("You must specify whether you want to push the Up button or "
                   "the Down button.")
        return True
    if v not in ("V-PUSH-UP", "V-PUSH-DOWN"):
        return False

    gs = _gs()
    if gs is None:
        return False
    here = _here_zil(caller)
    want_up = (v == "V-PUSH-UP")
    in_transit = gs.flags.get("ELEVATOR-IN-TRANSIT")

    def _start(door_id, maker, ticks):
        door = _by_zil_id(door_id)
        if door:
            door.db.is_open = False
        gs.flags["ELEVATOR-IN-TRANSIT"] = True
        _queue(gs, maker, ticks)
        caller.msg(_ELEVATOR_STARTS)

    if (here == "LOWER-ELEVATOR" and gs.flags.get("LOWER-ELEVATOR-ON")
            and not in_transit
            and gs.flags.get("LOWER-ELEVATOR-UP") != want_up):
        _start("LOWER-ELEVATOR-DOOR", "make_i_lower_elevator_trip", 100)
    elif (here == "UPPER-ELEVATOR" and gs.flags.get("UPPER-ELEVATOR-ON")
            and not in_transit
            and gs.flags.get("UPPER-ELEVATOR-UP") != want_up):
        _start("UPPER-ELEVATOR-DOOR", "make_i_upper_elevator_trip", 50)
    else:
        caller.msg("Nothing happens.")
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


# ---------------------------------------------------------------------------
# Subsystem 5 — Reactor repair (compone/comptwo.zil): Defense + Course Control
# ---------------------------------------------------------------------------

def _award(gs, points: int) -> None:
    if gs:
        gs.add_score(points)


def handle_access_panel(ctx: ObjAction) -> bool:
    """ACCESS-PANEL-F (comptwo.zil:391) — Defense: swap in the good board."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-OPEN":
        if obj.db.is_open:
            caller.msg("It's already open.")
        else:
            obj.db.is_open = True
            caller.msg("The panel swings open.")
        return True
    if v == "V-CLOSE":
        if obj.db.is_open:
            obj.db.is_open = False
            caller.msg("The panel swings closed.")
        else:
            caller.msg("It's already closed.")
        return True
    if v in ("V-PUT", "V-INSERT", "V-PUT-ON"):
        item = resolve_obj_local(caller, ctx.nouns[0]) if ctx.nouns else None
        if not obj.db.is_open:
            caller.msg("The panel is closed.")
        elif gs and gs.flags.get("ACCESS-PANEL-FULL"):
            caller.msg("There's no room.")
        elif item and item.db.zil_id == "GOOD-BOARD":
            item.location = None
            second = _by_zil_id("SECOND-BOARD")
            if second:
                second.move_to(obj, quiet=True, move_type="move")
            gs.flags["DEFENSE-FIXED"] = True
            gs.flags["ACCESS-PANEL-FULL"] = True
            _award(gs, 6)
            caller.msg("The card clicks neatly into the socket. The warning "
                       "lights stop flashing.")
        elif item and item.db.zil_id in ("CRACKED-BOARD", "FRIED-BOARD"):
            item.location = None
            second = _by_zil_id("SECOND-BOARD")
            if second:
                second.move_to(obj, quiet=True, move_type="move")
            gs.flags["ACCESS-PANEL-FULL"] = True
            if item.db.zil_id == "CRACKED-BOARD":
                gs.flags["ITS-CRACKED"] = True
            caller.msg("The card clicks neatly into the socket.")
        else:
            name = item.key if item else "thing"
            caller.msg(f"The {name} doesn't fit.")
        return True
    return False


def handle_board(ctx: ObjAction) -> bool:
    """BOARD-F (comptwo.zil:478) — the fromitz boards in the access panel."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-TAKE":
        if obj.db.zil_id == "SECOND-BOARD" and not (gs and gs.flags.get("DEFENSE-FIXED")):
            obj.location = None
            gs.flags["ACCESS-PANEL-FULL"] = False
            bad_id = "CRACKED-BOARD" if gs.flags.get("ITS-CRACKED") else "FRIED-BOARD"
            bad = _by_zil_id(bad_id)
            if bad:
                bad.move_to(caller, quiet=True, move_type="get")
            caller.msg("The fromitz board slides out of the panel, producing "
                       "an empty socket for another board.")
        else:
            caller.msg("You jerk your hand back as you receive a powerful "
                       "shock from the fromitz board.")
        return True
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        caller.msg("Like most fromitz boards, it is a twisted maze of silicon "
                   "circuits. It is square, approximately seventeen centimeters "
                   "on each side.")
        return True
    return False


def handle_cube(ctx: ObjAction) -> bool:
    """CUBE-F (comptwo.zil:545) — Course Control: swap in the good bedistor."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-OPEN":
        if obj.db.is_open:
            caller.msg("It's already open.")
        else:
            obj.db.is_open = True
            caller.msg("The lid swings open.")
        return True
    if v == "V-CLOSE":
        if obj.db.is_open:
            obj.db.is_open = False
            caller.msg("The lid swings closed.")
        else:
            caller.msg("It's already closed.")
        return True
    if v in ("V-PUT", "V-INSERT", "V-PUT-ON"):
        item = resolve_obj_local(caller, ctx.nouns[0]) if ctx.nouns else None
        bad = _by_zil_id("BAD-BEDISTOR")
        if not obj.db.is_open:
            caller.msg("The cube is closed.")
        elif bad and bad.location is obj:
            caller.msg("There's a fused bedistor in the way.")
        elif item and item.db.zil_id == "GOOD-BEDISTOR":
            item.move_to(obj, quiet=True, move_type="move")
            gs.flags["COURSE-CONTROL-FIXED"] = True
            _award(gs, 6)
            caller.msg("Done. The warning lights go out and another light goes "
                       "on.")
        elif item:
            caller.msg(f"The {item.key} doesn't fit.")
        return True
    return False


def handle_bad_bedistor(ctx: ObjAction) -> bool:
    """BAD-BEDISTOR-F (comptwo.zil:595) — fused; remove only with the pliers."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    cube = _by_zil_id("CUBE")
    if v == "V-TAKE" and cube and obj.location is cube:
        caller.msg("It seems to be fused to its socket.")
        return True
    if v == "V-ZATTRACT":
        prsi = _prsi(ctx)
        if prsi and prsi.db.zil_id == "PLIERS":
            obj.move_to(caller, quiet=True, move_type="get")
            _set_bit(obj, "TRYTAKEBIT", False)
            caller.msg("With a tug, you manage to remove the fused bedistor.")
        else:
            caller.msg("You can't get a grip on the bedistor with that.")
        return True
    return False


def handle_good_bedistor(ctx: ObjAction) -> bool:
    """GOOD-BEDISTOR-F (compone.zil:1424) — live once installed; touching kills."""
    if ctx.verb == "V-TAKE":
        gs = _gs()
        if gs and gs.flags.get("COURSE-CONTROL-FIXED"):
            gs.die("Kerzap!! You should know better than to touch an active "
                   "bedistor!")
            return True
    return False


def handle_spool_reader(ctx: ObjAction) -> bool:
    """SPOOL-READER-F (comptwo.zil:1189) — read the manual spools."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    spool = next((o for o in obj.contents), None)
    if v == "V-LAMP-ON":
        if _bit(obj, "ONBIT"):
            caller.msg("The spool reader is already on.")
        else:
            _set_bit(obj, "ONBIT", True)
            if spool:
                caller.msg(spool.db.obj_text or "The screen lights up.")
            else:
                caller.msg("The machine hums quietly, and the screen lights up "
                           "with the phrase \"Pleez insurt spuul.\"")
        return True
    if v == "V-LAMP-OFF":
        if _bit(obj, "ONBIT"):
            _set_bit(obj, "ONBIT", False)
            caller.msg("The spool reader is now off.")
        else:
            caller.msg("It's not on!")
        return True
    if v in ("V-EXAMINE", "V-LOOK-CRETIN", "V-READ"):
        if _bit(obj, "ONBIT") and spool:
            caller.msg(spool.db.obj_text or "The screen displays information.")
        else:
            caller.msg("The screen is blank.")
        return True
    if v in ("V-PUT", "V-INSERT"):
        item = resolve_obj_local(caller, ctx.nouns[0]) if ctx.nouns else None
        if spool:
            caller.msg("There's already a spool in the reader.")
        elif item and item.db.zil_id in ("GREEN-SPOOL", "RED-SPOOL"):
            item.move_to(obj, quiet=True, move_type="move")
            msg = "The spool fits neatly into the opening."
            if _bit(obj, "ONBIT"):
                msg += " Some information appears on the screen."
            caller.msg(msg)
        else:
            caller.msg("It doesn't fit in the circular opening.")
        return True
    return False


def resolve_obj_local(caller, noun):
    from world.verb_commands import resolve_obj
    return resolve_obj(caller, noun)


# Reactor room status displays (M-LOOK) -------------------------------------

def handle_planetary_defense(room, character, rarg):
    """PLANETARY-DEFENSE-F (comptwo.zil:370)."""
    if rarg != M_LOOK:
        return False
    gs = _gs()
    text = "This room is filled with a dazzling array of lights and controls. "
    if not _flag(gs, "DEFENSE-FIXED"):
        text += ("One light, blinking quickly, catches your eye. It reads "
                 "\"Surkit Boord Faalyur. WORNEENG: xis boord kuntroolz xe "
                 "diskriminaashun surkits.\" ")
    text += f"There is a small access panel on one wall which is {_ddesc('ACCESS-PANEL')}."
    return text


def handle_course_control(room, character, rarg):
    """PLANETARY-COURSE-CONTROL-F (comptwo.zil:522)."""
    if rarg != M_LOOK:
        return False
    gs = _gs()
    text = ("This is a long room whose walls are covered with complicated "
            "controls and colored lights. ")
    if _flag(gs, "COURSE-CONTROL-FIXED"):
        text += "One blinking light says \"Kors diivurjins minimiizeeng.\""
    else:
        text += ("Two of these lights are blinking. The first one reads "
                 "\"Bedistur Faalyur!\" The other light reads \"Kritikul "
                 "diivurjins frum pland kors.\"")
    text += f" In one corner is a large metal cube whose lid is {_ddesc('CUBE')}."
    return text


def handle_systems_monitors(room, character, rarg):
    """SYSTEMS-MONITORS-F (compone.zil:1125) — the three-system status board."""
    if rarg != M_LOOK:
        return False
    gs = _gs()
    d = _flag(gs, "DEFENSE-FIXED")
    c = _flag(gs, "COURSE-CONTROL-FIXED")
    m = _flag(gs, "COMM-FIXED")
    green = []
    if d: green.append("PLANATEREE DEFENS")
    if c: green.append("PLANATEREE KORS KUNTROOL")
    if m: green.append("KUMUUNIKAASHUNZ")
    green += ["LIIBREREE", "REEAKTURZ", "LIIF SUPORT"]
    bad = []
    if not d: bad.append("PLANATEREE DEFENS")
    if not c: bad.append("PLANATEREE KORS KUNTROOL")
    if not m: bad.append("KUMUUNIKAASHUNZ")
    bad.append("PRAJEKT KUNTROOL")
    all_fixed = d and c and m
    return ("This is a large room filled with tables full of strange "
            "equipment. The far wall is filled with a number of monitors. Of "
            f"these, the ones labelled {', '.join(green)} are green, but the "
            f"one{'' if all_fixed else 's'} labelled {', '.join(bad)} "
            f"indicate{'s' if all_fixed else ''} a malfunctioning condition.")


# ---------------------------------------------------------------------------
# Subsystem 5b — Comm chemistry (compone.zil) + laser miniaturization
# ---------------------------------------------------------------------------

_CHEM_COLORS = {1: "red", 2: "blue", 3: "green", 4: "yellow", 5: "gray",
                6: "brown", 7: "black", 8: "clear", 9: "clear"}
_CHEM_BUTTON_COLOR = {
    "RED-BUTTON": 1, "BLUE-BUTTON": 2, "GREEN-BUTTON": 3, "YELLOW-BUTTON": 4,
    "GRAY-BUTTON": 5, "BROWN-BUTTON": 6, "BLACK-BUTTON": 7,
    "ROUND-WHITE-BUTTON": 8, "SQUARE-WHITE-BUTTON": 9,
}


def _comm_init(gs) -> None:
    """RANDOMIZE-ORDER + STEPS-TO-GO (compone.zil:3124) — the secret sequence."""
    if gs.flags.get("COMM-SEQ") is not None:
        return
    import random
    steps = random.randint(2, 3)               # <+ 1 <RANDOM 2>>
    seq = random.sample(range(1, 8), steps)     # distinct colours 1-7
    gs.flags["COMM-SEQ"] = seq
    gs.flags["CHEMICAL-REQUIRED"] = seq[0]


def handle_chem_button(ctx: ObjAction) -> bool:
    """CHEM-BUTTON-F (compone.zil:1787) — dispense a coloured fluid to the flask."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    if v != "V-PUSH":
        return False
    gs = _gs()
    color = _CHEM_BUTTON_COLOR.get(obj.db.zil_id, 0)
    placed = gs.flags.get("SPOUT-PLACED") if gs else None
    flask = _by_zil_id("FLASK")
    fluid = _by_zil_id("CHEMICAL-FLUID")
    if placed == "FLASK":
        if fluid and fluid.location is flask:
            caller.msg("Another dose of the chemical fluid pours out of the "
                       "spout, splashes over the already-full flask, spills "
                       "onto the floor, and dries up.")
        else:
            if fluid and flask:
                fluid.move_to(flask, quiet=True, move_type="move")
            gs.flags["CHEMICAL-FLAG"] = color
            caller.msg(f"The flask fills with some {_CHEM_COLORS[color]} "
                       "chemical fluid. The fluid gradually turns milky white.")
    else:
        where = placed.lower().replace("-", " ") if placed else "spout"
        caller.msg(f"Some sort of chemical fluid pours out of the spout, spills "
                   f"all over the {where}, and dries up.")
    return True


def handle_chemical_dispenser(ctx: ObjAction) -> bool:
    """CHEMICAL-DISPENSER-F (compone.zil:1680) — place a vessel under the spout."""
    caller, v = ctx.caller, ctx.verb
    gs = _gs()
    if v in ("V-PUT-UNDER", "V-PUT"):
        item = resolve_obj_local(caller, ctx.nouns[0]) if ctx.nouns else None
        placed = gs.flags.get("SPOUT-PLACED") if gs else None
        if placed:
            cur = _by_zil_id(placed)
            caller.msg(f"The {cur.key if cur else placed} is already resting "
                       "under the spout.")
        elif item:
            gs.flags["SPOUT-PLACED"] = item.db.zil_id
            caller.msg(f"The {item.key} is now sitting under the spout.")
        return True
    return False


def handle_chemical_fluid(ctx: ObjAction) -> bool:
    """CHEMICAL-FLUID-F (compone.zil:2964) — pour into the funnel to fix comm."""
    caller, v = ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-EAT":
        if gs:
            gs.die("Mmmmm....that tasted just like delicious poisonous "
                   "chemicals!")
        return True
    if v in ("V-POUR", "V-PUT"):
        flask = _by_zil_id("FLASK")
        if flask and flask.location is not caller:
            caller.msg("You're not holding the flask.")
            return True
        prsi = _prsi(ctx)
        target = prsi.db.zil_id if prsi else None
        fluid = ctx.obj
        if target == "FUNNEL-HOLE":
            _comm_init(gs)
            fluid.location = None
            gs.flags["CHEMICAL-FLAG_used"] = True
            if gs.flags.get("CHEMICAL-FLAG") == gs.flags.get("CHEMICAL-REQUIRED"):
                seq = list(gs.flags.get("COMM-SEQ") or [])
                if seq:
                    seq.pop(0)
                gs.flags["COMM-SEQ"] = seq
                if not seq:
                    gs.flags["COMM-FIXED"] = True
                    _award(gs, 6)
                    caller.msg("The liquid disappears into the hole. The lights "
                               "on the enunciator panel blink rapidly and then "
                               "go dark. The coolant system warning light goes "
                               "off, and another flashes, indicating that the "
                               "help message is now being sent.")
                else:
                    gs.flags["CHEMICAL-REQUIRED"] = seq[0]
                    caller.msg("The liquid disappears into the hole. The lights "
                               "blink rapidly and all go off except one, a "
                               f"{_CHEM_COLORS[seq[0]]} light.")
            else:
                gs.flags["COMM-SHUTDOWN"] = True
                if gs.flags.get("COMM-FIXED"):
                    gs.flags["COMM-FIXED"] = False
                    _award(gs, -6)
                caller.msg("An alarm sounds briefly, and a sign flashes "
                           "\"Sistum Shutdown.\" A moment later, the lights in "
                           "the room dim and the send console shuts down.")
            gs.flags["CHEMICAL-FLAG"] = 0
            return True
        # Poured elsewhere.
        fluid.location = None
        caller.msg(f"It pours over the {prsi.key if prsi else 'ground'} and "
                   "evaporates.")
        return True
    return False


def handle_flask(ctx: ObjAction) -> bool:
    """FLASK-F (compone.zil:1543) — a simple chemical container (look only)."""
    return False


def _laser_number(ctx):
    for tok in ctx.nouns:
        tok = tok.strip()
        if tok.isdigit():
            return int(tok)
    return None


def handle_laser_dial(ctx: ObjAction) -> bool:
    """LASER-DIAL-F (comptwo.zil:2594) — set the beam from 1 (red) to 6 (violet)."""
    caller, v = ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-SET":
        n = _laser_number(ctx)
        if n is None:
            caller.msg("You must specify a number to set the dial to.")
        elif n == gs.flags.get("LASER-SETTING"):
            caller.msg("That's where it's set now!")
        elif n > 6 or n == 0:
            caller.msg("The dial can only be set from 1 to 6.")
        else:
            gs.flags["LASER-SETTING"] = n
            caller.msg(f"The dial is now set to {n}.")
        return True
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        caller.msg(f"The dial is currently set to {_flag(gs, 'LASER-SETTING', 5)}.")
        return True
    return False


def _zap_count(gs) -> bool:
    """ZAP-COUNT (comptwo.zil:2640) — True if the laser has no shot to fire."""
    laser = _by_zil_id("LASER")
    for bat, key in (("OLD-BATTERY", "OLD-SHOTS"), ("NEW-BATTERY", "NEW-SHOTS")):
        b = _by_zil_id(bat)
        if b and laser and b.location is laser:
            if gs.flags.get(key, 0) > 0:
                gs.flags[key] -= 1
                return False
            return True
    return True   # no battery


def handle_laser(ctx: ObjAction) -> bool:
    """LASER-F (comptwo.zil:2652) — the miniaturization laser."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-SET":   # "set laser to N" → set the dial
        return handle_laser_dial(ctx)
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        bat = next((b for b in ("OLD-BATTERY", "NEW-BATTERY")
                    if (_by_zil_id(b) and _by_zil_id(b).location is obj)), None)
        depr = f"contains {('an' if bat == 'OLD-BATTERY' else 'a')} " \
               f"{_by_zil_id(bat).key}" if bat else "is empty"
        caller.msg("The laser, though portable, is still fairly heavy. It has a "
                   "dial with six settings, currently on setting "
                   f"{gs.flags.get('LASER-SETTING', 5)}. The depression on top "
                   f"{depr}.")
        return True
    if v in ("V-PUT", "V-INSERT"):
        item = resolve_obj_local(caller, ctx.nouns[0]) if ctx.nouns else None
        if item and item.db.zil_id in ("OLD-BATTERY", "NEW-BATTERY"):
            if any(_by_zil_id(b) and _by_zil_id(b).location is obj
                   for b in ("OLD-BATTERY", "NEW-BATTERY")):
                caller.msg("There's already a battery in the laser.")
            else:
                item.move_to(obj, quiet=True, move_type="move")
                caller.msg("The battery clicks into the depression.")
            return True
        return False
    if v in ("V-ZAP", "V-SZAP"):
        if obj.location is not caller:
            caller.msg("You're not holding the laser.")
            return True
        if gs and not gs.flags.get("LASER-SCORE-FLAG"):
            gs.flags["LASER-SCORE-FLAG"] = True
            gs.add_score(2)
        # The target is the referenced object that isn't the laser itself
        # (handles both "fire laser at speck" and "shoot speck with laser").
        target_obj = None
        for n in ctx.nouns:
            o = resolve_obj_local(caller, n)
            if o and o is not obj:
                target_obj = o
                break
        target = target_obj.db.zil_id if target_obj else None
        if _zap_count(gs):
            caller.msg("Click.")
            return True
        # The laser fires — start warming, and resolve the target.
        gs.flags["LASER-JUST-SHOT"] = True
        if not any(getattr(i.routine, "__name__", None) == "i_warmth"
                   for i in gs.scheduler._interrupts):
            import world.interrupts as I
            gs.scheduler.enable(gs.scheduler.queue(I.make_i_warmth(gs), -1))
        if target == "SPECK":
            _shoot_speck(gs, caller)
        elif target in ("ME", "HANDS", "ADVENTURER"):
            caller.msg("Ouch! You managed to burn yourself nicely.")
        else:
            caller.msg("A thin beam shoots from the laser.")
        return True
    return False


_BEAM_MISSES = ("The beam just misses the speck!", "A near miss!",
                "A good shot, but just a little wide of the target.")


def _shoot_speck(gs, caller) -> None:
    """SHOOT-SPECK (comptwo.zil:2842) — two precise hits fix the computer."""
    import random
    if gs.flags.get("LASER-SETTING") != 1:
        relay = _by_zil_id("RELAY")
        if relay:
            relay.location = None
        caller.msg("A thin beam slices through the red plastic covering of the "
                   "relay like a hot knife through butter. Air rushes in and "
                   "the relay collapses into a heap of plastic shards.")
        return
    # Setting 1 (red): marksmanship ramps up with each miss.
    if random.randint(1, 100) <= gs.flags.get("MARKSMANSHIP-COUNTER", 0):
        if gs.flags.get("SPECK-HIT"):
            gs.flags["COMPUTER-FIXED"] = True
            door = _by_zil_id("CRYO-ELEVATOR-DOOR")
            if door:
                door.db.is_open = True
                _set_bit(door, "INVISIBLE", False)
            projcon = _by_zil_id("PROJCON-OFFICE")
            if projcon:
                _set_bit(projcon, "TOUCHBIT", False)
            gs.scheduler.enable(gs.scheduler.queue(
                __import__("world.interrupts", fromlist=["make_i_fry"]).make_i_fry(gs), 200))
            gs.add_score(8)
            speck = _by_zil_id("SPECK")
            if speck:
                speck.location = None
            caller.msg("The beam hits the speck again! This time, it vaporizes "
                       "into a fine cloud of ash. The relay slowly begins to "
                       "close, and a voice whispers in your ear \"Sector 384 "
                       "will activate in 200 millichrons. Proceed to exit "
                       "station.\"")
        else:
            gs.flags["SPECK-HIT"] = True
            caller.msg("The speck is hit by the beam! It sizzles a little, but "
                       "isn't destroyed yet.")
    else:
        gs.flags["MARKSMANSHIP-COUNTER"] = gs.flags.get("MARKSMANSHIP-COUNTER", 0) + 12
        caller.msg(random.choice(_BEAM_MISSES))


# ---------------------------------------------------------------------------
# Subsystem 6 — Cryo / Project Control endgame (comptwo.zil)
# ---------------------------------------------------------------------------

_CRYO_ANTEROOM_DESC = (
    "The elevator closes as you leave it, and you find yourself in a small, "
    "chilly room. To the north, through a wide arch, is an enormous chamber "
    "lined from floor to ceiling with thousands of cryo-units. You can see "
    "similar chambers beyond, and your mind staggers at the thought of the "
    "millions of individuals asleep for countless centuries.\n\n"
    "In the anteroom where you stand is a solitary cryo-unit, its cover "
    "frosted. Next to the cryo-unit is a complicated control panel."
)


def handle_cryo_anteroom(room, character, rarg):
    """CRYO-ANTEROOM-F (comptwo.zil:1393) — the endgame.

    M-LOOK describes the chamber; M-END wakes Veldina and resolves the game,
    branching on which of the three planetary systems you repaired.
    """
    if rarg == M_LOOK:
        return _CRYO_ANTEROOM_DESC
    if rarg != M_END:
        return False

    gs = _gs()
    if gs is None or gs.dead:
        return True
    comm = gs.flags.get("COMM-FIXED")
    defense = gs.flags.get("DEFENSE-FIXED")
    course = gs.flags.get("COURSE-CONTROL-FIXED")

    wake = (
        "A door slides open and a medical robot glides in. It opens the "
        "cryo-unit and administers an injection to its inhabitant. As the robot "
        "glides away, a figure rises from the cryo-unit -- a handsome, "
        "middle-aged woman with flowing red hair. She spends some time studying "
        "readouts from the control panel"
    )
    wake += ", pressing several keys." if (comm and defense) else "."

    if course:
        text = wake + (
            "\n\nAs other cryo-units in the chambers beyond begin opening, the "
            "woman turns to you, bows gracefully, and speaks in a beautiful, "
            "lilting voice. \"I am Veldina, leader of Resida. Thanks to you, the "
            "cure has been discovered, and the planetary systems repaired. We "
            "are eternally grateful.\""
        )
        if comm and defense:
            text += (
                "\n\n\"You will also be glad to hear that a ship of your Stellar "
                "Patrol now orbits the planet.\" A landing party from the S.P.S. "
                "Flathead materializes nearby. Captain Sterling acknowledges "
                "your heroic actions and informs you of your promotion to "
                "Lieutenant First Class. The grateful people of Resida offer you "
                "leadership of their world, and a familiar figure comes "
                "bounding toward you! \"Hi!\" shouts Floyd. \"Floyd feeling "
                "better now!\""
            )
            return gs.victory(text)
        # Planet saved but you're stranded.
        text += "\n\n\"Unfortunately, a second ship from your Stellar Patrol has "
        text += ("been destroyed by our malfunctioning meteor defenses.\""
                 if not defense else
                 "come looking for survivors, and because of our malfunctioning "
                 "communications system, has given up and departed.\"")
        text += (" I fear that you are stranded on Resida, possibly forever. "
                 "However, we show our gratitude by offering you an unlimited "
                 "bank account and a house in the country.")
        return gs.victory(text)

    # Course control not repaired: the orbit has decayed.
    text = wake + (
        "\n\nShe turns to you and, with a strained voice says, \"You have fixed "
        "our computer and a Cure has been discovered, and we are grateful. But "
        "alas, it was all in vain. Our planetary course control system has "
        "malfunctioned, and the orbit has now decayed beyond correction. Soon "
        "Resida will plunge into the sun.\""
    )
    if comm and defense:
        text += ("\n\nVeldina examines the control panel again. \"Fortunately, "
                 "another ship from your Stellar Patrol has arrived, so at least "
                 "you will survive.\" A landing party from the S.P.S. Flathead "
                 "materializes and takes you away from the doomed world.")
        return gs.victory(text)
    return gs.die(text)


def handle_projcon_office(room, character, rarg):
    """PROJCON-OFFICE-F (comptwo.zil:1321) — the computer-fixed mural reveal."""
    gs = _gs()
    if rarg == M_LOOK:
        text = ("This office looks like a headquarters of some kind. Exits lead "
                "north and east. The west wall displays a logo. ")
        if _flag(gs, "COMPUTER-FIXED"):
            text += ("The mural that previously adorned the south wall has slid "
                     "away, revealing an open doorway to a large elevator!")
        else:
            text += ("The south wall is completely covered by a garish mural "
                     "which clashes with the other decor of the room.")
        return text
    if rarg == M_END and gs and not gs.flags.get("MURAL-FLAG"):
        floyd = _by_zil_id("FLOYD")
        if floyd and floyd.location is character.location:
            gs.flags["MURAL-FLAG"] = True
            character.msg("Floyd surveys the mural and scratches his head. \"I "
                          "don't remember seeing this before,\" he comments.")
            return True
    return False


def handle_comm_room(room, character, rarg):
    """COMM-ROOM-F (compone.zil:2822) — send-station status + enunciator hint."""
    gs = _gs()
    if rarg == M_LOOK:
        text = ("This is a small room with no windows. The sole exit is "
                "southwest. The console on the left is the \"Reeseev "
                "Staashun\", its \"Tranzmishun Reeseevd\" light blinking. The "
                "console on the right is the \"Send Staashun.\" Next to its "
                "screen is a flashing sign which says ")
        if _flag(gs, "COMM-SHUTDOWN"):
            text += "\"Sistum Shutdown.\""
        elif _flag(gs, "COMM-FIXED"):
            text += "\"Tranzmishun in pragres.\""
        else:
            text += "\"Malfunkshun in Sendeeng Kuulint Sistum.\""
        text += (" Next to this console is an enunciator panel, and a "
                 "funnel-shaped hole labelled \"Kuulint Sistum Manyuuwul "
                 "Oovuriid.\"")
        return text
    if rarg == M_END and gs and not gs.flags.get("COMM-FIXED") \
            and not gs.flags.get("COMM-SHUTDOWN"):
        _comm_init(gs)
        req = gs.flags.get("CHEMICAL-REQUIRED", 0)
        if req in _CHEM_COLORS:
            character.msg(f"A {_CHEM_COLORS[req]} colored light is flashing on "
                          "the enunciator panel.")
            return True
    return False


_FAMILIAR_WRENCHING = ("You feel the familiar wrenching of your innards, and "
                       "find yourself in a vast room whose distant walls are "
                       "rushing straight toward you...")


def handle_station_384(room, character, rarg):
    """STATION-384-F (comptwo.zil:2410) — entering from the strip un-shrinks you.

    BEEN-HERE is set by V-TYPE *after* the arrival move (so the entry M-ENTER
    no-ops); returning here from the strip then wrenches you back to full size
    in the mini-booth — or, once the computer is fixed, out via the auxiliary
    booth (with the cryo-access announcement).
    """
    gs = _gs()
    if rarg == M_ENTER and gs and gs.flags.get("BEEN-HERE"):
        gs.flags["BEEN-HERE"] = False
        if gs.flags.get("COMPUTER-FIXED"):
            character.msg("A voice seems to whisper in your ear \"Main "
                          "Miniaturization and Teleportation Booth has "
                          "malfunctioned...switching to Auxiliary Booth...\" "
                          + _FAMILIAR_WRENCHING)
            import world.interrupts as I
            gs.scheduler.enable(gs.scheduler.queue(I.make_i_announcement(gs), 130))
            dest = _by_zil_id("AUXILIARY-BOOTH")
        else:
            character.msg(_FAMILIAR_WRENCHING)
            dest = _by_zil_id("MINI-BOOTH")
        if dest:
            character.move_to(dest, quiet=True, move_type="teleport")
        return True
    return False


def handle_cryo_elevator(room, character, rarg):
    """CRYO-ELEVATOR-F (comptwo.zil:1362)."""
    if rarg == M_LOOK:
        return ("This is a large, plain elevator with one solitary button and a "
                f"door to the north which is {_ddesc('CRYO-ELEVATOR-DOOR')}.")
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
    # ZIL DEQUEUE first (globals.zil): drop any pending hunger-warning timer —
    # including the short-interval escalation ones — before re-arming, or the
    # orphaned timer keeps firing and eating only stacks more warnings.
    gs.scheduler._interrupts = [
        i for i in gs.scheduler._interrupts
        if getattr(i.routine, "__name__", None) != "i_hunger_warnings"
    ]
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


# ---------------------------------------------------------------------------
# Subsystem 8 — Magnet / dead Floyd / robot hole (compone.zil)
# ---------------------------------------------------------------------------

def handle_magnet(ctx: ObjAction) -> bool:
    """MAGNET-F (compone.zil:1570) — fishes out the key; scrambles carried cards."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    if v == "V-TAKE":
        # Picking it up starts the card-scrambling demon (I-MAGNET).
        if gs and not any(getattr(i.routine, "__name__", None) == "i_magnet"
                          for i in gs.scheduler._interrupts):
            import world.interrupts as I
            gs.scheduler.enable(gs.scheduler.queue(I.make_i_magnet(gs), -1))
        return False   # fall through to the generic take
    if v in ("V-ATTRACT", "V-ZATTRACT", "V-PUT-ON"):
        if obj.location is not caller:
            caller.msg("You're not holding the magnet.")
            return True
        key = _by_zil_id("KEY")
        prsi = _prsi(ctx)
        target = prsi.db.zil_id if prsi else None
        if key and _bit(key, "TOUCHBIT") and target == "KEY":
            key.move_to(caller, quiet=True, move_type="get")
            caller.msg("The key jumps against the ends of the magnet and sticks "
                       "there. Proud of your feat, you remove the key from the "
                       "magnet.")
        elif key and not _bit(key, "TOUCHBIT") and target in ("KEY", "CREVICE"):
            key.move_to(caller, quiet=True, move_type="get")
            _set_bit(key, "INVISIBLE", False)
            _set_bit(key, "TRYTAKEBIT", False)
            _set_bit(key, "TOUCHBIT", True)
            caller.msg("With a spray of dust and a loud clank, a piece of metal "
                       "leaps from the crevice and affixes itself to the "
                       "magnet. It is a steel key! With a tug, you remove the "
                       "key from the magnet.")
        else:
            caller.msg("Nothing interesting happens.")
        return True
    return False


def handle_dead_floyd(ctx: ObjAction) -> bool:
    """DEAD-FLOYD-F (compone.zil:2303)."""
    caller, v = ctx.caller, ctx.verb
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        caller.msg("You turn to look at Floyd, but a tremendous sense of loss "
                   "overcomes you, and you turn away.")
        return True
    if v == "V-LAMP-ON":
        caller.msg("As you touch Floyd's on-off switch, it falls off in your "
                   "hands.")
        return True
    if v == "V-LAMP-OFF":
        caller.msg("I'm afraid that Floyd has already been turned off, "
                   "permanently, and gone to that great robot shop in the sky.")
        return True
    return False


def handle_robot_hole(ctx: ObjAction) -> bool:
    """ROBOT-HOLE-F (comptwo.zil:243) + Floyd's fetch of the good board."""
    caller, v = ctx.caller, ctx.verb
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        caller.msg("It's too small for you to get through. It was presumably "
                   "intended for robots, such as the broken repair robot lying "
                   "over there.")
        return True
    if v == "V-LOOK-INSIDE":
        caller.msg("You can make out a small supply room of some sort.")
        return True
    if v in ("V-OPEN", "V-CLOSE"):
        caller.msg("There's no door, just an opening in the wall.")
        return True
    if v in ("V-POINT", "V-THROUGH"):
        return _floyd_through_hole(caller)
    return False


def _floyd_through_hole(caller) -> bool:
    """FLOYD-THROUGH-HOLE (comptwo.zil:258) — Floyd fetches the shiny board."""
    gs = _gs()
    floyd = _by_zil_id("FLOYD")
    if not (floyd and floyd.location is caller.location):
        return False
    if gs and gs.flags.get("HOLE-TRIP-FLAG"):
        caller.msg("\"Not again,\" whines Floyd.")
        return True
    caller.ndb.pf_elapsed = 50
    if gs:
        gs.flags["HOLE-TRIP-FLAG"] = True
    good = _by_zil_id("GOOD-BOARD")
    if good:
        _set_bit(good, "INVISIBLE", False)
    caller.msg("Floyd squeezes through the opening and is gone for quite a "
               "while. You hear thudding noises and squeals of enjoyment. After "
               "a while the noise stops, and Floyd emerges, looking downcast. "
               "\"Floyd found a rubber ball inside. Lots of fun for a while, "
               "but must have been old, because it fell apart. Nothing else "
               "interesting inside. Just a shiny fromitz board.\"")
    return True


# ---------------------------------------------------------------------------
# Subsystem 7 — Teleport booths (globals.zil TELEPORT)
# ---------------------------------------------------------------------------

_BUTTON_DEST = {
    "TELEPORTATION-BUTTON-1-F": "BOOTH-1",
    "TELEPORTATION-BUTTON-2-F": "BOOTH-2",
    "TELEPORTATION-BUTTON-3-F": "BOOTH-3",
}


def _teleport(gs, caller, dest_zil: str) -> None:
    """TELEPORT (globals.zil:1505)."""
    if not gs or not gs.flags.get("TELEPORTATION-ON"):
        caller.msg("A sign flashes \"Teleportaashun buux not aktivaatid.\"")
        return
    dest = _by_zil_id(dest_zil)
    if dest is None:
        caller.msg("Nothing happens.")
        return
    caller.msg("You experience a strange feeling in the pit of your stomach.")
    floyd = _by_zil_id("FLOYD")
    if floyd and floyd.location is caller.location:
        caller.msg("Floyd gives a terrified squeal, and clutches at his "
                   "guidance mechanism.")
        floyd.move_to(dest, quiet=True, move_type="teleport")
    here = caller.location
    if here:
        for obj in list(here.contents):
            if obj is not caller and _bit(obj, "TAKEBIT"):
                obj.move_to(dest, quiet=True, move_type="move")
    caller.move_to(dest, quiet=True, move_type="teleport")
    gs.flags["TELEPORTATION-ON"] = False
    _disable_interrupt(gs, "i_turnoff_teleportation")
    from world.verb_commands import build_look
    caller.msg(build_look(caller, dest))


def handle_teleport_button(ctx: ObjAction) -> bool:
    """TELEPORTATION-BUTTON-1/2/3-F (globals.zil:1531)."""
    if ctx.verb != "V-PUSH":
        return False
    dest = _BUTTON_DEST.get(ctx.obj.db.action)
    if dest:
        _teleport(_gs(), ctx.caller, dest)
        return True
    return False


def handle_kitchen_door(ctx: ObjAction) -> bool:
    """KITCHEN-DOOR-F (compone.zil:772) — opens only via the card slot."""
    if ctx.verb == "V-OPEN":
        ctx.caller.msg("A light flashes \"Pleez yuuz kitcin akses kard.\"")
        return True
    return False


def handle_combination_dial(ctx: ObjAction) -> bool:
    """COMBINATION-DIAL-F (compone.zil:268) — open the conference-room safe."""
    obj, caller, v = ctx.obj, ctx.caller, ctx.verb
    gs = _gs()
    if v in ("V-EXAMINE", "V-LOOK-CRETIN"):
        caller.msg("The dial can be turned to any number between 0 and 1000.")
        return True
    if v == "V-SET":
        n = _laser_number(ctx)
        if n is None:
            caller.msg("You must specify a number to set the dial to.")
        elif _bit(obj, "MUNGEDBIT"):
            caller.msg("The dial has somehow become fused and won't move.")
        elif n == gs.flags.get("DIAL-NUMBER"):
            caller.msg("That's what the dial is set to now!")
        elif n == gs.flags.get("NUMBER-NEEDED"):
            gs.flags["DIAL-NUMBER"] = 0
            door = _by_zil_id("CONFERENCE-DOOR")
            if door:
                door.db.is_open = True
            caller.msg("The door swings open, and the dial resets to 0.")
        elif n > 1000:
            caller.msg("The dial cannot be turned to a number that high.")
        else:
            gs.flags["DIAL-NUMBER"] = n
            caller.msg(f"The dial is now set to {n}.")
        return True
    return False


def handle_combination_paper(ctx: ObjAction) -> bool:
    """COMBINATION-PAPER-F (comptwo.zil:1680) — the combination, found in the
    lab uniform pocket."""
    caller, v = ctx.caller, ctx.verb
    if v in ("V-READ", "V-EXAMINE", "V-LOOK-CRETIN"):
        gs = _gs()
        caller.msg("Week uv 14-Juun--2882. Kombinaashun tuu Konfurins Ruum: "
                   f"{_flag(gs, 'NUMBER-NEEDED', 0)}.")
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
    "ACCESS-PANEL-F": handle_access_panel,
    "BOARD-F": handle_board,
    "CUBE-F": handle_cube,
    "BAD-BEDISTOR-F": handle_bad_bedistor,
    "GOOD-BEDISTOR-F": handle_good_bedistor,
    "SPOOL-READER-F": handle_spool_reader,
    "TELEPORTATION-BUTTON-1-F": handle_teleport_button,
    "TELEPORTATION-BUTTON-2-F": handle_teleport_button,
    "TELEPORTATION-BUTTON-3-F": handle_teleport_button,
    "MAGNET-F": handle_magnet,
    "DEAD-FLOYD-F": handle_dead_floyd,
    "ROBOT-HOLE-F": handle_robot_hole,
    "CHEM-BUTTON-F": handle_chem_button,
    "CHEMICAL-DISPENSER-F": handle_chemical_dispenser,
    "CHEMICAL-FLUID-F": handle_chemical_fluid,
    "FLASK-F": handle_flask,
    "LASER-DIAL-F": handle_laser_dial,
    "LASER-F": handle_laser,
    "LAB-UNIFORM-F": handle_lab_uniform,
    "LAB-DESK-F": handle_lab_desk,
    "COMBINATION-DIAL-F": handle_combination_dial,
    "COMBINATION-PAPER-F": handle_combination_paper,
}
ROOM_HANDLERS.update({
    "CONTROL-CABIN-F": handle_control_cabin,
    "PLANETARY-DEFENSE-F": handle_planetary_defense,
    "PLANETARY-COURSE-CONTROL-F": handle_course_control,
    "SYSTEMS-MONITORS-F": handle_systems_monitors,
    "CRYO-ANTEROOM-F": handle_cryo_anteroom,
    "PROJCON-OFFICE-F": handle_projcon_office,
    "CRYO-ELEVATOR-F": handle_cryo_elevator,
    "COMM-ROOM-F": handle_comm_room,
    "STATION-384-F": handle_station_384,
})
OBJ_HANDLERS.update({
    "BLUE-ELEVATOR-BUTTON-F": handle_blue_elevator_button,
    "RED-ELEVATOR-BUTTON-F": handle_red_elevator_button,
    "ELEVATOR-BUTTON-F": handle_elevator_button,
    "BLATHER-F": handle_blather,
    "AMBASSADOR-F": handle_ambassador,
    # Subsystem 10 scenery.
    "CELERY-F": handle_celery,
    "GROUND-F": handle_ground,
    "WINDOW-F": handle_window,
    "CLIFF-F": handle_cliff,
    "OCEAN-F": handle_ocean,
    "TABLES-F": handle_tables,
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
