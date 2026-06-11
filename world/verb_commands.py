"""
Planetfall verb/command system.

Reads syntax.json + synonyms.json to generate one Evennia Command subclass
per unique ZIL verb.  Pattern dispatch happens inside each command's func().
ALL_COMMANDS is built at module import time and registered in the cmdset.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from commands.command import PlanetfallCommand

DATA_DIR = Path(__file__).parent / "data"

_ARTICLES = frozenset({"the", "a", "an", "some", "my", "your"})
_SKIP_TOKENS = frozenset({"RANDOM", "COMMAND", "RECORD", "UNRECORD"})

# Verbs that consume no game time — PlanetfallCommand.timeless = True
# AGAIN is timeless because it replays the previous command, whose own
# at_post_cmd advances the clock — counting AGAIN too would double-tick.
_TIMELESS_VERBS = frozenset({
    "INVENTORY", "SCORE", "SAVE", "RESTOR", "VERSION", "DIAGNOSE",
    "VERBOSE", "BRIEF", "SUPER", "AGAIN",
})

# ZIL truncations → full English words for the Evennia key
_VERB_NORMALIZATIONS = {
    "RESTAR": "restart",
    "RESTOR": "restore",
}


# ---------------------------------------------------------------------------
# Object resolver
# ---------------------------------------------------------------------------

def _by_zil_id(zil_id: str):
    from evennia.utils.search import search_object_attribute
    results = search_object_attribute(key="zil_id", value=zil_id)
    return results[0] if results else None


def _gs():
    """Return the live GameState, or None if the script doesn't exist yet."""
    from world.scheduler_script import get_game_state_script
    script = get_game_state_script()
    return script.get_gs() if script else None


def _strip_articles(noun: str) -> str:
    return " ".join(w for w in noun.lower().split() if w not in _ARTICLES)


def _article(obj) -> str:
    return "an" if "VOWELBIT" in (obj.db.obj_flags or []) else "a"


# ZIL pronoun words that resolve to P-IT-OBJECT (the last-referenced object).
_PRONOUNS = frozenset({"it", "them", "him", "her", "that"})


def _accessible(caller, obj) -> bool:
    """ZIL ACCESSIBLE? — is obj in inventory, the room, or its local-globals?"""
    if obj.location is caller:
        return True
    loc = caller.location
    if not loc:
        return False
    if obj.location is loc:
        return True
    return (obj.db.zil_id or "?") in (loc.db.globals or [])


def resolve_obj(caller, noun: str):
    """
    Find the best-matching visible object for a noun phrase.
    Searches caller's inventory, current room, and room's local-globals.

    ZIL THIS-IS-IT: a successful resolution is remembered on
    `caller.ndb.last_it`, so a later "it"/"them"/"him" resolves to it as long
    as it is still accessible (P-IT-OBJECT + P-IT-LOC == HERE).
    """
    from evennia.objects.objects import DefaultExit

    noun = _strip_articles(noun)
    if not noun:
        return None

    if noun in _PRONOUNS:
        it = getattr(caller.ndb, "last_it", None)
        return it if (it and _accessible(caller, it)) else None

    words = set(noun.split())

    candidates: list = list(caller.contents)
    if caller.location:
        for obj in caller.location.contents:
            if obj is not caller and not isinstance(obj, DefaultExit) and obj not in candidates:
                candidates.append(obj)
        for zil_id in (caller.location.db.globals or []):
            obj = _by_zil_id(zil_id)
            if obj and obj not in candidates:
                candidates.append(obj)

    best = None
    best_score = 0
    for obj in candidates:
        syns = {s.lower() for s in (obj.db.synonyms or [])}
        adjs = {a.lower() for a in (obj.db.adjectives or [])}
        name_words = set(obj.key.lower().split())
        score = len(words & (syns | adjs | name_words))
        if score > best_score:
            best = obj
            best_score = score
    if best is not None:
        caller.ndb.last_it = best   # THIS-IS-IT
    return best


# ---------------------------------------------------------------------------
# Pattern matcher
# ---------------------------------------------------------------------------

def _match_pattern(args: str, pattern: list) -> list | None:
    """
    Try to match args against a ZIL syntax pattern.
    Returns a list of noun-phrase strings (one per OBJECT slot), or None.

    Pattern tokens: 'OBJECT' (noun slot) or a keyword like 'IN', 'WITH', etc.
    Patterns with internal-only tokens (RANDOM, COMMAND…) are always skipped.
    """
    if any(t in _SKIP_TOKENS for t in pattern):
        return None
    args = args.strip()
    if not pattern:
        return [] if not args else None

    parts = []
    for token in pattern:
        if token == "OBJECT":
            parts.append(r"(.+?)")
        else:
            parts.append(re.escape(token.lower()))
    regex = r"^\s*" + r"\s+".join(parts) + r"\s*$"
    m = re.match(regex, args.lower(), re.IGNORECASE)
    return list(m.groups()) if m else None


# ---------------------------------------------------------------------------
# Handlers — each receives (caller, nouns: list[str])
# ---------------------------------------------------------------------------

def build_look(caller, loc) -> str:
    """Render a room the ZIL way as a single string.

    Used by the V-LOOK command, by movement, and by connect (via
    Character.at_look), so every room view is consistent: the room name, its
    ACTION-routine description (or static desc), visible objects (NDESCBIT
    objects like the ADVENTURER are hidden), then the obvious exits.
    """
    from evennia.objects.objects import DefaultExit
    from world.action_handlers import dispatch_room_look
    if not loc:
        return "You are in a void."

    lines = [f"\n{loc.key}"]
    desc = dispatch_room_look(loc, caller)
    if desc is None:
        desc = loc.db.desc or None
    if desc:
        lines.append(desc)
    loc.db.first_visit = False

    visible = [
        obj for obj in loc.contents
        if obj is not caller
        and not isinstance(obj, DefaultExit)
        and "NDESCBIT" not in (obj.db.obj_flags or [])
    ]
    if visible:
        lines.append("")
        for obj in visible:
            lines.append(f"  There is {_article(obj)} {obj.key} here.")

    exits = [e for e in loc.contents
             if isinstance(e, DefaultExit) and e.destination]
    if exits:
        lines.append(f"\nObvious exits: {', '.join(sorted(e.key for e in exits))}")
    return "\n".join(lines)


def _h_look(caller, nouns):
    caller.msg(build_look(caller, caller.location))


def _h_examine(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    if obj.db.fdesc and not obj.db.fdesc_seen:
        caller.msg(obj.db.fdesc)
        obj.db.fdesc_seen = True
    elif obj.db.desc:
        caller.msg(obj.db.desc)
    else:
        caller.msg(f"You see nothing special about the {obj.key}.")
    if "CONTBIT" in (obj.db.obj_flags or []) and obj.db.is_open:
        inner = [o for o in obj.contents if "NDESCBIT" not in (o.db.obj_flags or [])]
        if inner:
            caller.msg("The " + obj.key + " contains: " + ", ".join(o.key for o in inner) + ".")
        else:
            caller.msg(f"The {obj.key} is empty.")


def _h_look_inside(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    if "CONTBIT" not in (obj.db.obj_flags or []):
        caller.msg(f"You can't look inside the {obj.key}.")
        return
    if not obj.db.is_open:
        caller.msg(f"The {obj.key} is closed.")
        return
    inner = [o for o in obj.contents if "NDESCBIT" not in (o.db.obj_flags or [])]
    if inner:
        caller.msg("The " + obj.key + " contains: " + ", ".join(o.key for o in inner) + ".")
    else:
        caller.msg(f"The {obj.key} is empty.")


def _h_inventory(caller, nouns):
    carried = list(caller.contents)
    if not carried:
        caller.msg("You are carrying nothing.")
        return
    caller.msg("You are carrying:")
    for obj in carried:
        worn = " (worn)" if "WORNBIT" in (obj.db.obj_flags or []) else ""
        caller.msg(f"  {_article(obj)} {obj.key}{worn}")


def _h_take(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    if obj in caller.contents:
        caller.msg("You're already carrying that.")
        return
    if "TAKEBIT" not in (obj.db.obj_flags or []):
        caller.msg("You can't pick that up.")
        return
    obj.move_to(caller, quiet=True, move_type="get")
    gs = _gs()
    if gs:
        gs.score_object(obj)   # ZIL SCORE-OBJ: valuable items score on first take
    caller.msg("Taken.")


def _h_drop(caller, nouns):
    noun = _strip_articles(nouns[0])
    words = set(noun.split())
    obj = None
    for item in caller.contents:
        syns = {s.lower() for s in (item.db.synonyms or [])}
        adjs = {a.lower() for a in (item.db.adjectives or [])}
        name_words = set(item.key.lower().split())
        if words & (syns | adjs | name_words):
            obj = item
            break
    if not obj:
        caller.msg(f"You're not carrying any {nouns[0]}.")
        return
    obj.move_to(caller.location, quiet=True, move_type="drop")
    caller.msg("Dropped.")


def _h_open(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    flags = obj.db.obj_flags or []
    if "DOORBIT" not in flags and "CONTBIT" not in flags:
        caller.msg("That's not something you can open.")
        return
    if obj.db.is_open:
        caller.msg("It's already open.")
        return
    if obj.db.is_locked:
        caller.msg("It's locked.")
        return
    obj.db.is_open = True
    caller.msg("Opened.")


def _h_close(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    flags = obj.db.obj_flags or []
    if "DOORBIT" not in flags and "CONTBIT" not in flags:
        caller.msg("That's not something you can close.")
        return
    if not obj.db.is_open:
        caller.msg("It's already closed.")
        return
    obj.db.is_open = False
    caller.msg("Closed.")


def _h_wait(caller, nouns):
    caller.msg("Time passes.")


def _h_read(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    text = obj.db.obj_text
    if text:
        caller.msg(text)
    else:
        caller.msg(f"There's nothing to read on the {obj.key}.")


def _h_diagnose(caller, nouns):
    from world.scheduler_script import get_game_state_script
    script = get_game_state_script()
    if not script:
        caller.msg("Your condition appears normal.")
        return
    gs = script.get_gs()
    caller.msg(f"Day {gs.day}. You feel reasonably well.")


def _h_score(caller, nouns):
    """V-SCORE (verbs.zil:222) — score, day, time, and rank."""
    gs = _gs()
    score = gs.score if gs else 0
    day = gs.day if gs else 1
    line = f"Your score is {score} (out of 80 points). It is Day {day} of your adventure."

    chrono = next(
        (o for o in caller.contents if o.db.zil_id == "CHRONOMETER"), None
    )
    if chrono:
        moves = gs.internal_moves if gs else 0
        line += (
            " Current Galactic Standard Time (adjusted to your local day-cycle) "
            f"is {moves}."
        )
    else:
        line += (
            " Current Galactic Standard Time is impossible to determine, since "
            "you're not wearing your chronometer."
        )

    from world.state import GameState
    line += f"\nThis score gives you the rank of {GameState.rank_for(score)}."
    caller.msg(line)


# ---------------------------------------------------------------------------
# SAVE / RESTORE (Phase 10)
# ---------------------------------------------------------------------------

# GameStateScript.db fields that make up a complete game snapshot. Each is a
# primitive / plain dict, so the snapshot is JSON-safe and Evennia-storable.
_SAVE_SCRIPT_FIELDS = (
    "internal_moves", "day", "score",
    "chronometer_present", "chronometer_munged",
    "flags", "interrupt_states", "floyd_state",
)

# Per-object mutable state captured for each inventory item. The rest of an
# object's db (zil_id, synonyms, action, …) is static game data reloaded with
# the world, so only the fields that change in play are saved.
_SAVE_OBJ_FIELDS = ("obj_flags", "value", "fdesc_seen", "is_open", "is_locked")


def _save_slot(nouns) -> str:
    """Named save slot, or 'autosave' when SAVE/RESTORE is given no argument.

    The raw noun is used verbatim (lowercased, spaces → underscores); articles
    are NOT stripped, so 'my game' stays a distinct slot rather than collapsing.
    """
    return nouns[0].strip().lower().replace(" ", "_") if nouns and nouns[0].strip() else "autosave"


def _h_save(caller, nouns):
    """V-SAVE (verbs.zil) — snapshot game state to a named slot on the player.

    Captures the GameStateScript clock/Floyd/flags/score, the player's room
    (by zil_id), and the player's inventory (each item's mutable db state).
    """
    import copy
    from world.scheduler_script import get_or_create_game_state_script

    script = get_or_create_game_state_script()
    # Flush live GameState/Floyd to db so the snapshot is current.
    script._persist(script.get_gs())

    inventory = []
    for obj in caller.contents:
        if not obj.db.zil_id:
            continue
        inventory.append({
            "zil_id": obj.db.zil_id,
            **{f: copy.deepcopy(obj.attributes.get(f)) for f in _SAVE_OBJ_FIELDS},
        })

    blob = {
        "script": {f: copy.deepcopy(script.attributes.get(f)) for f in _SAVE_SCRIPT_FIELDS},
        "location": caller.location.db.zil_id if caller.location else None,
        "inventory": inventory,
    }

    saves = dict(caller.db.saves or {})
    slot = _save_slot(nouns)
    saves[slot] = blob
    caller.db.saves = saves
    caller.msg(f'Game saved to "{slot}".')


def _h_restore(caller, nouns):
    """V-RESTORE (verbs.zil) — reload a snapshot saved by _h_save."""
    from world.scheduler_script import get_or_create_game_state_script

    slot = _save_slot(nouns)
    blob = (caller.db.saves or {}).get(slot)
    if not blob:
        caller.msg(f'No saved game in slot "{slot}".')
        return

    # 1. Restore the GameStateScript db and rebuild its live GameState/Floyd.
    script = get_or_create_game_state_script()
    for field, value in blob["script"].items():
        script.attributes.add(field, value)
    # A snapshot is only ever taken while alive, so restoring always revives
    # (the `dead` halt-state is not part of the save blob).
    script.attributes.add("dead", False)
    script.ndb.game_state = None
    script.ndb.floyd = None
    script._rebuild()

    # 2. Teleport the player back to the saved room.
    room = _by_zil_id(blob["location"]) if blob["location"] else None
    if room and caller.location is not room:
        caller.move_to(room, quiet=True, move_type="teleport")

    # 3. Rebuild inventory: drop held items absent from the save, then pull in
    #    and re-state every saved item.
    dest = room or caller.location
    saved_ids = {item["zil_id"] for item in blob["inventory"]}
    for obj in list(caller.contents):
        if obj.db.zil_id not in saved_ids and dest:
            obj.move_to(dest, quiet=True, move_type="drop")
    for item in blob["inventory"]:
        obj = _by_zil_id(item["zil_id"])
        if obj is None:
            continue
        if obj.location is not caller:
            obj.move_to(caller, quiet=True, move_type="get")
        for field in _SAVE_OBJ_FIELDS:
            obj.attributes.add(field, item.get(field))

    caller.msg(f'Game restored from "{slot}".')
    _h_look(caller, [])


def _h_put(caller, nouns):
    item = resolve_obj(caller, nouns[0])
    container = resolve_obj(caller, nouns[1]) if len(nouns) > 1 else None
    if not item:
        caller.msg(f"You don't see any {nouns[0]} here.")
        return
    if not container:
        caller.msg("Put it where?")
        return
    if item not in caller.contents:
        caller.msg(f"You're not carrying the {item.key}.")
        return
    if "CONTBIT" not in (container.db.obj_flags or []):
        caller.msg(f"You can't put things in the {container.key}.")
        return
    if not container.db.is_open:
        caller.msg(f"The {container.key} is closed.")
        return
    item.move_to(container, quiet=True, move_type="drop")
    gs = _gs()
    if gs:
        gs.score_object(item)   # ZIL SCORE-OBJ also fires on PUT (verbs.zil:624)
    caller.msg("Done.")


def _h_tell(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    if "ACTORBIT" not in (obj.db.obj_flags or []):
        caller.msg("There's no one here by that name.")
        return
    caller.msg(f"The {obj.key} doesn't seem to hear you.")


def _h_wear(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    if "WEARBIT" not in (obj.db.obj_flags or []):
        caller.msg(f"You can't wear the {obj.key}.")
        return
    if "WORNBIT" in (obj.db.obj_flags or []):
        caller.msg("You're already wearing that.")
        return
    if obj not in caller.contents:
        if "TAKEBIT" not in (obj.db.obj_flags or []):
            caller.msg("You can't pick that up.")
            return
        obj.move_to(caller, quiet=True, move_type="get")
    flags = list(obj.db.obj_flags or [])
    flags.append("WORNBIT")
    obj.db.obj_flags = flags
    caller.msg(f"You put on the {obj.key}.")


def _h_take_off(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    if "WORNBIT" not in (obj.db.obj_flags or []):
        caller.msg("You're not wearing that.")
        return
    obj.db.obj_flags = [f for f in (obj.db.obj_flags or []) if f != "WORNBIT"]
    caller.msg(f"You remove the {obj.key}.")


def _h_lamp_on(caller, nouns):
    """Generic 'turn on' for a LIGHTBIT object (object ACTION handlers, e.g.
    Floyd, intercept this first via dispatch_object)."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if not obj:
        caller.msg(f"You see no {nouns[0]} here." if nouns else "Turn on what?")
        return
    if "LIGHTBIT" not in (obj.db.obj_flags or []):
        caller.msg(f"You can't turn on the {obj.key}.")
        return
    if "ONBIT" in (obj.db.obj_flags or []):
        caller.msg("It's already on.")
        return
    obj.db.obj_flags = list(obj.db.obj_flags or []) + ["ONBIT"]
    caller.msg(f"The {obj.key} is now on.")


def _h_lamp_off(caller, nouns):
    """Generic 'turn off' for a LIGHTBIT object."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if not obj:
        caller.msg(f"You see no {nouns[0]} here." if nouns else "Turn off what?")
        return
    if "LIGHTBIT" not in (obj.db.obj_flags or []):
        caller.msg(f"You can't turn off the {obj.key}.")
        return
    if "ONBIT" not in (obj.db.obj_flags or []):
        caller.msg("It's already off.")
        return
    obj.db.obj_flags = [f for f in (obj.db.obj_flags or []) if f != "ONBIT"]
    caller.msg(f"The {obj.key} is now off.")


def _h_again(caller, nouns):
    """V-AGAIN (verbs.zil:1) — replay the most recent non-AGAIN command.

    `last_command` is recorded in the command factory's func() below; the
    AGAIN command itself is never recorded, so this replays the real verb
    that preceded it. The guard is a belt-and-braces check.
    """
    last = getattr(caller.ndb, "last_command", None)
    if not last or last.strip().lower() in ("g", "again"):
        caller.msg("You can't repeat that.")
        return
    caller.execute_cmd(last)


# ZIL HO-HUM table (verbs.zil:1144) — random flavour for a futile push/pull.
_HO_HUM = (
    " isn't notably helpful.",
    " has no effect.",
    " is as worthwhile as cleaning a Grotch cage.",
)


def _h_push(caller, nouns):
    """V-PUSH default (verbs.zil:1084, HACK-HACK branch).

    Object ACTION routines (e.g. the elevator call-buttons) get first crack at
    PUSH via dispatch_object; this is the generic fall-through for everything
    else.
    """
    import random
    name = "that"
    if nouns:
        obj = resolve_obj(caller, nouns[0])
        name = obj.key if obj else _strip_articles(nouns[0])
    caller.msg(f"Pushing the {name}{random.choice(_HO_HUM)}")


# Direction words (and their ZIL abbreviations) → the exit key used by the
# loader. Lets "go west" / "walk n" resolve to the room's directional exit.
_DIR_CANON = {
    "north": "north", "n": "north", "south": "south", "s": "south",
    "east": "east", "e": "east", "west": "west", "w": "west",
    "northeast": "northeast", "ne": "northeast",
    "northwest": "northwest", "nw": "northwest",
    "southeast": "southeast", "se": "southeast",
    "southwest": "southwest", "sw": "southwest",
    "up": "up", "u": "up", "down": "down", "d": "down",
    "out": "out", "o": "out", "in": "in",
}


def _h_walk(caller, nouns):
    """V-WALK (verbs.zil:431) — "go/walk <direction>".

    ZIL sets P-WALK-DIR from the parsed direction and traverses that exit. Here
    the direction arrives as the noun; we find the matching exit and let its
    at_traverse run the conditional/per logic (which also advances the clock on
    a successful move). The one-shot `pf_clock_handled` flag tells
    PlanetfallCommand.at_post_cmd not to tick a second time for that move.
    """
    from evennia.objects.objects import DefaultExit
    word = _strip_articles(nouns[0]).strip().lower() if nouns else ""
    direction = _DIR_CANON.get(word)
    loc = caller.location
    if not direction or not loc:
        caller.msg("You can't go that way.")
        return
    for ex in loc.contents:
        if not isinstance(ex, DefaultExit):
            continue
        names = {ex.key.lower()} | {a.lower() for a in ex.aliases.all()}
        if direction in names:
            # at_traverse advances the clock for any exit attempt (success or
            # refusal), so flag it to stop at_post_cmd ticking the move twice.
            ex.at_traverse(caller, ex.destination)
            caller.ndb.pf_clock_handled = True
            return
    # No exit at all in that direction: at_traverse never ran, so let the
    # command's own at_post_cmd tick the failed move (one default turn).
    caller.msg("You can't go that way.")


# Fallback movement commands, one per compass/vertical direction. Real exits
# create their own commands at cmdset priority 101; CharacterCmdSet (where these
# live) is priority 0, so an exit always outranks the matching fallback. A
# fallback therefore only fires for a direction that has NO exit in the current
# room, turning Evennia's "Command 'down' is not available. Maybe you meant…"
# into ZIL's "You can't go that way." (Each delegates to _h_walk, so on the off
# chance it ever runs with an exit present it still traverses correctly.)
_DIRECTION_COMMANDS = {
    "north": ["n"], "south": ["s"], "east": ["e"], "west": ["w"],
    "northeast": ["ne"], "northwest": ["nw"],
    "southeast": ["se"], "southwest": ["sw"],
    "up": ["u"], "down": ["d"], "out": ["o"], "in": [],
}


def _make_direction_command(direction: str, aliases: list) -> type:
    def func(self):
        _h_walk(self.caller, [direction])

    return type(
        f"CmdDir{direction.title()}",
        (PlanetfallCommand,),
        {
            "key": direction,
            "aliases": list(aliases),
            "func": func,
            "__doc__": f"Move {direction}.",
        },
    )


# ---------------------------------------------------------------------------
# Generic verb handlers (verbs.zil) — the fall-through behaviour for verbs that
# no object's ACTION routine consumes. Object-specific reactions live in
# world/action_handlers.py OBJ_HANDLERS and get first crack via dispatch_object.
# ---------------------------------------------------------------------------

import random as _random


def _set_elapsed(caller, ticks: int) -> None:
    """ZIL <SETG C-ELAPSED n> — make this command cost `ticks` instead of 7."""
    caller.ndb.pf_elapsed = ticks


def _prso(caller, nouns):
    """Resolve the verb's direct object, messaging if it isn't here."""
    if not nouns:
        return None
    obj = resolve_obj(caller, nouns[0])
    if obj is None:
        caller.msg(f"You see no {_strip_articles(nouns[0])} here.")
    return obj


def _is(obj, flag) -> bool:
    return flag in (obj.db.obj_flags or [])


def _do_walk(caller, direction: str) -> None:
    """ZIL DO-WALK — funnel a verb (ENTER/EXIT/GO-UP/…) into movement."""
    _h_walk(caller, [direction])
    caller.ndb.pf_clock_handled = True


# --- HACK-HACK / fixed-flavour tables (verbs.zil) --------------------------

_YUKS = ("Fat chance.", "A valiant attempt.", "You can't be serious.",
         "Not bloody likely.", "An interesting idea...", "What a concept!")
_WHEEEEE = ("You've spent too much time among the Leaping Loon-toads of Leonia.",
            "Having fun?", "Wheeeeeee!!!")
_HELLOS = ("Hello.", "Nice weather we're having.", "Goodbye.")


def _hack_hack(caller, obj, lead: str) -> None:
    """ZIL HACK-HACK (verbs.zil:1145) — '<lead> the <obj><ho-hum>'."""
    caller.msg(f"{lead}the {obj.key}{_random.choice(_HO_HUM)}")


# --- futile-action verbs ---------------------------------------------------

def _h_kick(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        _hack_hack(caller, obj, "Kicking ")

def _h_wave(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        _hack_hack(caller, obj, "Waving ")

def _h_rub(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        _hack_hack(caller, obj, "Fiddling with ")

def _h_pull(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        _hack_hack(caller, obj, "Pulling ")

def _h_push_up(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        _hack_hack(caller, obj, "Pushing up ")

def _h_push_down(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        _hack_hack(caller, obj, "Pushing down ")

def _h_mung(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        _hack_hack(caller, obj, "Trying to destroy ")


# --- simple fixed-message verbs (no object) --------------------------------

def _h_curse(caller, nouns):
    caller.msg("Such language from an Ensign in the Stellar Patrol!")

def _h_yell(caller, nouns):
    caller.msg("Aarrrrggggggghhhhhhhh!")

def _h_zork(caller, nouns):
    caller.msg("Gesundheit!")

def _h_no(caller, nouns):
    caller.msg("You sound rather negative.")

def _h_yes(caller, nouns):
    caller.msg("You sound rather positive.")

def _h_maybe(caller, nouns):
    caller.msg("You sound rather indecisive.")

def _h_smile(caller, nouns):
    caller.msg("How pleasant!")

def _h_fly(caller, nouns):
    caller.msg("Humans are not usually equipped for flying.")

def _h_rape(caller, nouns):
    caller.msg("What a (ahem!) strange idea.")

def _h_kiss(caller, nouns):
    caller.msg("I'd sooner kiss a pile of Antarian swamp mold.")

def _h_skip(caller, nouns):
    caller.msg(_random.choice(_WHEEEEE))

def _h_slide(caller, nouns):
    caller.msg(_random.choice(_YUKS))

def _h_help(caller, nouns):
    caller.msg("If you're really stuck, you can order a complete map and "
               "InvisiClues Hint Booklet using the order form in your game "
               "package.")

def _h_fix_it(caller, nouns):
    caller.msg("You shouldn't expect sweeping general commands like this to "
               "work. If you want to repair something, you must perform the "
               "specific steps required.")

def _h_span(caller, nouns):
    caller.msg("You can't.")

def _h_cant_do_that(caller, nouns):
    caller.msg("You can't do that.")    # V-TURN / V-LOCK / V-UNLOCK / V-PUT-UNDER

def _h_throw_off(caller, nouns):
    caller.msg("It's difficult to see how that can be done.")

def _h_step_on(caller, nouns):
    caller.msg("That's a silly thing to do.")

def _h_answer(caller, nouns):
    caller.msg("Nobody is awaiting your answer.")


# --- fixed-message verbs that mention the object ---------------------------

def _h_eat(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        caller.msg(f"I don't think that the {obj.key} would agree with you.")

def _h_smell(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        caller.msg(f"It smells just like {_article(obj)} {obj.key}.")

def _h_taste(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    if obj.db.zil_id in ("HIGH-PROTEIN", "RED-GOO", "BROWN-GOO", "GREEN-GOO"):
        caller.msg("It tastes edible.")
    elif obj.db.zil_id == "CHEMICAL-FLUID":
        caller.msg("It burns your tongue.")
    else:
        caller.msg(f"It tastes just like {_article(obj)} {obj.key}.")

def _h_listen(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        _set_elapsed(caller, 18)
        caller.msg(f"The {obj.key} makes no sound.")

def _h_follow(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        caller.msg(f"The {obj.key} is right here!")

def _h_pour(caller, nouns):
    caller.msg("Pouring or spilling non-liquids is specifically forbidden by "
               "section 17.9.2 of the Galactic Adventure Game Compendium of "
               "Rules.")

def _h_salute(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        caller.msg(f"The {obj.key} fails to return your salute.")

def _h_flush(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        caller.msg(f"Flush {_article(obj)} {obj.key}?")

def _h_play(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        caller.msg(f"How does one play {_article(obj)} {obj.key}?")

def _h_alarm(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        caller.msg(f"The {obj.key} isn't sleeping.")

def _h_reply(caller, nouns):
    obj = _prso(caller, nouns)
    if obj:
        caller.msg(f"It is hardly likely that the {obj.key} is interested.")

def _h_attack(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    if _is(obj, "ACTORBIT"):
        caller.msg(f"The {obj.key} is frightened and backs away.")
    else:
        caller.msg(f"I've known strange beings, but attacking "
                   f"{_article(obj)} {obj.key}???")

def _h_knock(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    if "door" in {s.lower() for s in (obj.db.synonyms or [])} or "DOORBIT" in (obj.db.obj_flags or []):
        caller.msg("Nobody's home.")
    else:
        caller.msg(f"Why knock on {_article(obj)} {obj.key}?")

def _h_look_under(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    stuff = "slime" if obj.db.zil_id == "AMBASSADOR" else "dust"
    caller.msg(f"There is nothing but {stuff} there.")

def _h_search(caller, nouns):
    # Object ACTION routines (CREVICE, etc.) intercept SEARCH first; this is the
    # generic fall-through.
    _set_elapsed(caller, 32)
    caller.msg("You find nothing unusual.")

def _h_reach(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    has = any(o for o in obj.contents)
    word = "something" if has else "nothing"
    caller.msg(f"There is {word} inside the {obj.key}.")

def _h_set(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    if obj.db.zil_id in ("COMBINATION-DIAL", "LASER-DIAL"):
        caller.msg("You must specify a number to set the dial to.")
    else:
        caller.msg(f"Turning the {obj.key} accomplishes nothing.")

def _h_hello(caller, nouns):
    if nouns:
        obj = resolve_obj(caller, nouns[0])
        if obj:
            caller.msg("Until now, I've only heard demented Denebian Devils "
                       f"say \"Hello\" to {_article(obj)} {obj.key}.")
            return
    caller.msg(_random.choice(_HELLOS))

def _h_move(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    if _is(obj, "TAKEBIT"):
        caller.msg(f"Moving the {obj.key} reveals nothing.")
    else:
        caller.msg(f"You can't move the {obj.key}.")

def _h_give(caller, nouns):
    # GIVE OBJECT TO ACTOR — PRSO is the gift, PRSI the recipient.
    if len(nouns) < 2:
        caller.msg("Give it to whom?")
        return
    gift = resolve_obj(caller, nouns[0])
    recipient = resolve_obj(caller, nouns[1])
    if not gift:
        caller.msg(f"You see no {_strip_articles(nouns[0])} here.")
        return
    if gift not in caller.contents:
        caller.msg(f"You're not holding the {gift.key}.")
        return
    if not recipient or not _is(recipient, "ACTORBIT"):
        rname = recipient.key if recipient else _strip_articles(nouns[1])
        caller.msg(f"You can't give {_article(gift)} {gift.key} to a {rname}!")
        return
    caller.msg(f"The {recipient.key} declines your offer.")

def _h_show(caller, nouns):
    if len(nouns) < 2:
        caller.msg("Show it to whom?")
        return
    item = resolve_obj(caller, nouns[0])
    viewer = resolve_obj(caller, nouns[1])
    if not item:
        caller.msg(f"You see no {_strip_articles(nouns[0])} here.")
        return
    if item not in caller.contents:
        caller.msg(f"You're not holding the {item.key}.")
        return
    if viewer and _is(viewer, "ACTORBIT"):
        caller.msg(f"The {viewer.key} looks at the {item.key}.")
    else:
        vname = viewer.key if viewer else _strip_articles(nouns[1])
        caller.msg(f"Why would you want to show something to a {vname}?")

def _h_oil(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    oil_can = next((o for o in caller.contents if o.db.zil_id == "OIL-CAN"), None)
    if not oil_can:
        caller.msg("Oil it with what?")
        return
    if obj.db.zil_id == "FLOYD" and _is(obj, "RLANDBIT"):
        caller.msg("Floyd thanks you for your thoughtfulness.")
    else:
        caller.msg(f"The {obj.key} doesn't need oiling.")

def _h_scrub(caller, nouns):
    obj = _prso(caller, nouns)
    if not obj:
        return
    have_tool = any(o.db.zil_id in ("SCRUB-BRUSH", "TOWEL") for o in caller.contents)
    if not have_tool:
        caller.msg("You don't have anything to scrub with!")
    elif _is(obj, "ACTORBIT"):
        caller.msg(f"The {obj.key} prefers cleaning himself.")
    else:
        caller.msg(f"The {obj.key} is a bit shinier now.")

def _h_diagnose_full(caller, nouns):
    """V-DIAGNOSE (verbs.zil:1431) — sickness / sleep / hunger status."""
    _set_elapsed(caller, 18)
    gs = _gs()
    sick = gs.flags.get("SICKNESS-LEVEL", 0) if gs else 0
    sleepy = gs.flags.get("SLEEPY-LEVEL", 0) if gs else 0
    hunger = gs.flags.get("HUNGER-LEVEL", 0) if gs else 0
    lines = []
    if sick == 0:
        lines.append("You are in perfect health.")
    else:
        deg = ("severely" if sick > 7 else "very" if sick > 5
               else "somewhat" if sick > 3 else "a bit")
        lines.append(f"You are {deg} sick and feverish.")
    if sleepy == 0:
        lines.append("You feel well-rested.")
    else:
        deg = ("phenomenally" if sleepy > 2 else "quite" if sleepy > 1 else "sort of")
        lines.append(f"You feel {deg} tired.")
    if hunger == 0:
        lines.append("You seem to be well-fed.")
    else:
        deg = ("awesomely phenomenally" if hunger > 4 else "noticeably"
               if hunger > 2 else "fairly")
        lines.append(f"You seem to be {deg} thirsty and hungry.")
    caller.msg("\n".join(lines))


# --- movement-delegating verbs (ZIL DO-WALK) -------------------------------

def _h_go_up(caller, nouns):
    _do_walk(caller, "up")

def _h_enter(caller, nouns):
    _do_walk(caller, "in")

def _h_exit(caller, nouns):
    _do_walk(caller, "out")

def _h_leave(caller, nouns):
    _do_walk(caller, "out")

def _h_climb_up(caller, nouns):
    _do_walk(caller, "up")

def _h_climb_down(caller, nouns):
    _do_walk(caller, "down")

def _h_walk_dir(caller, nouns):
    """V-WALK-AROUND / V-WALK-TO — ZIL USE-DIRECTIONS prompt."""
    caller.msg("You should specify a direction to walk.")


def _h_stub(caller, nouns):
    caller.msg("That action isn't implemented yet.")


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

HANDLERS: dict = {
    "V-LOOK":        _h_look,
    "V-LOOK-CRETIN": _h_examine,   # "look lamp" → examine it
    "V-EXAMINE":     _h_examine,
    "V-LOOK-INSIDE": _h_look_inside,
    "V-INVENTORY":   _h_inventory,
    "V-TAKE":        _h_take,
    "V-DROP":        _h_drop,
    "V-OPEN":        _h_open,
    "V-OPEN-WITH":   _h_open,
    "V-CLOSE":       _h_close,
    "V-WAIT":        _h_wait,
    "V-READ":        _h_read,
    "V-DIAGNOSE":    _h_diagnose_full,
    "V-SCORE":       _h_score,
    "V-PUT":         _h_put,
    "V-PUT-ON":      _h_put,
    "V-INSERT":      _h_put,
    "V-TELL":        _h_tell,
    "V-TALK":        _h_tell,
    "V-ASK-FOR":     _h_tell,
    "V-REPLY":       _h_tell,
    "V-WEAR":        _h_wear,
    "V-TAKE-OFF":    _h_take_off,
    "V-REMOVE":      _h_take_off,
    "V-LAMP-ON":     _h_lamp_on,
    "V-LAMP-OFF":    _h_lamp_off,
    "V-AGAIN":       _h_again,
    "V-SAVE":        _h_save,
    "V-RESTORE":     _h_restore,
    "V-PUSH":        _h_push,
    "V-WALK":        _h_walk,
    # Generic verb fall-throughs (verbs.zil) — engine feature #2.
    "V-KICK":        _h_kick,
    "V-WAVE":        _h_wave,
    "V-RUB":         _h_rub,
    "V-PULL":        _h_pull,
    "V-PUSH-UP":     _h_push_up,
    "V-PUSH-DOWN":   _h_push_down,
    "V-MUNG":        _h_mung,
    "V-CURSE":       _h_curse,
    "V-YELL":        _h_yell,
    "V-SCOLD":       _h_yell,
    "V-ZORK":        _h_zork,
    "V-NO":          _h_no,
    "V-YES":         _h_yes,
    "V-MAYBE":       _h_maybe,
    "V-SMILE":       _h_smile,
    "V-FLY":         _h_fly,
    "V-RAPE":        _h_rape,
    "V-KISS":        _h_kiss,
    "V-SKIP":        _h_skip,
    "V-SLIDE":       _h_slide,
    "V-HELP":        _h_help,
    "V-FIX-IT":      _h_fix_it,
    "V-SPAN":        _h_span,
    "V-TURN":        _h_cant_do_that,
    "V-LOCK":        _h_cant_do_that,
    "V-UNLOCK":      _h_cant_do_that,
    "V-PUT-UNDER":   _h_cant_do_that,
    "V-THROW-OFF":   _h_throw_off,
    "V-STEP-ON":     _h_step_on,
    "V-ANSWER":      _h_answer,
    "V-EAT":         _h_eat,
    "V-SMELL":       _h_smell,
    "V-TASTE":       _h_taste,
    "V-LISTEN":      _h_listen,
    "V-FOLLOW":      _h_follow,
    "V-POUR":        _h_pour,
    "V-SALUTE":      _h_salute,
    "V-FLUSH":       _h_flush,
    "V-PLAY":        _h_play,
    "V-ALARM":       _h_alarm,
    "V-REPLY":       _h_reply,
    "V-ATTACK":      _h_attack,
    "V-KNOCK":       _h_knock,
    "V-LOOK-UNDER":  _h_look_under,
    "V-LOOK-BEHIND": _h_look_under,
    "V-SEARCH":      _h_search,
    "V-REACH":       _h_reach,
    "V-SET":         _h_set,
    "V-HELLO":       _h_hello,
    "V-MOVE":        _h_move,
    "V-GIVE":        _h_give,
    "V-SHOW":        _h_show,
    "V-OIL":         _h_oil,
    "V-SCRUB":       _h_scrub,
    "V-GO-UP":       _h_go_up,
    "V-ENTER":       _h_enter,
    "V-EXIT":        _h_exit,
    "V-LEAVE":       _h_leave,
    "V-CLIMB-UP":    _h_climb_up,
    "V-CLIMB-DOWN":  _h_climb_down,
    "V-WALK-AROUND": _h_walk_dir,
    "V-WALK-TO":     _h_walk_dir,
}


# ---------------------------------------------------------------------------
# Command factory
# ---------------------------------------------------------------------------

def make_command(verb: str, entries: list, aliases: list, timeless: bool = False) -> type:
    """Return a PlanetfallCommand subclass for the given verb."""
    # Most specific (longest) pattern tried first
    sorted_entries = sorted(entries, key=lambda e: len(e[0]), reverse=True)

    def func(self):
        from world.action_handlers import dispatch_object
        args = self.args.strip()
        for pattern, handler_name in self._pf_entries:
            nouns = _match_pattern(args, pattern)
            if nouns is not None:
                # Phase 9: remember the last real command so AGAIN/G can replay
                # it. AGAIN itself is never recorded, so "g g" still repeats the
                # original verb rather than chasing its own tail.
                if handler_name != "V-AGAIN":
                    self.caller.ndb.last_command = self.raw_string.strip()
                # ZIL PRSO semantics: the object's ACTION routine gets first
                # crack at the verb. If it consumes it, skip the default.
                if nouns:
                    obj = resolve_obj(self.caller, nouns[0])
                    if obj and dispatch_object(obj, self.caller, handler_name, nouns):
                        return
                HANDLERS.get(handler_name, _h_stub)(self.caller, nouns)
                return
        self.caller.msg("I don't understand what you mean.")

    verb_key = _VERB_NORMALIZATIONS.get(verb, verb.lower())
    clean_aliases = [a.lower() for a in aliases if a.lower() != verb_key]

    return type(
        f"Cmd{''.join(w.title() for w in re.split(r'[-_]', verb))}",
        (PlanetfallCommand,),
        {
            "key": verb_key,
            "aliases": clean_aliases,
            "func": func,
            "timeless": timeless,
            "_pf_entries": sorted_entries,
            "__doc__": f"Planetfall: {verb}",
        },
    )


def build_all_commands() -> list:
    """Load syntax + synonyms and return one Command class per ZIL verb."""
    with open(DATA_DIR / "synonyms.json") as f:
        synonyms_raw: dict = json.load(f)
    aliases_by_verb: dict = {}
    for word, canonical in synonyms_raw.items():
        aliases_by_verb.setdefault(canonical, []).append(word)

    with open(DATA_DIR / "syntax.json") as f:
        syntax: list = json.load(f)

    entries_by_verb: dict = {}
    for entry in syntax:
        verb = entry["verb"]
        if verb.startswith(("$", "\\")):
            continue
        pattern = entry.get("pattern", [])
        if any(t in _SKIP_TOKENS for t in pattern):
            continue
        entries_by_verb.setdefault(verb, []).append((pattern, entry["handler"]))

    return [
        make_command(
            verb,
            entries,
            aliases_by_verb.get(verb, []),
            timeless=(verb in _TIMELESS_VERBS),
        )
        for verb, entries in entries_by_verb.items()
    ]


# Built once at import time; registered by CharacterCmdSet. The direction
# fallbacks are appended so a directionless room reports "You can't go that
# way." rather than Evennia's command-not-found suggestions.
ALL_COMMANDS: list = build_all_commands() + [
    _make_direction_command(d, a) for d, a in _DIRECTION_COMMANDS.items()
]
