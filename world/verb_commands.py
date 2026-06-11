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


def resolve_obj(caller, noun: str):
    """
    Find the best-matching visible object for a noun phrase.
    Searches caller's inventory, current room, and room's local-globals.
    """
    from evennia.objects.objects import DefaultExit

    noun = _strip_articles(noun)
    if not noun:
        return None
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

def _h_look(caller, nouns):
    from evennia.objects.objects import DefaultExit
    loc = caller.location
    if not loc:
        caller.msg("You are in a void.")
        return
    caller.msg(f"\n{loc.key}")
    from world.action_handlers import dispatch_room, M_LOOK
    if not dispatch_room(loc, caller, M_LOOK):
        if loc.db.desc:
            caller.msg(loc.db.desc)
    loc.db.first_visit = False
    visible = [
        obj for obj in loc.contents
        if obj is not caller
        and not isinstance(obj, DefaultExit)
        and "NDESCBIT" not in (obj.db.obj_flags or [])
    ]
    if visible:
        caller.msg("")
        for obj in visible:
            caller.msg(f"  There is {_article(obj)} {obj.key} here.")
    exits = [e for e in loc.contents
             if isinstance(e, DefaultExit) and e.destination]
    if exits:
        caller.msg(f"\nObvious exits: {', '.join(sorted(e.key for e in exits))}")


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
            before = caller.location
            ex.at_traverse(caller, ex.destination)
            if caller.location is not before:
                caller.ndb.pf_clock_handled = True
            return
    caller.msg("You can't go that way.")


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
    "V-DIAGNOSE":    _h_diagnose,
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


# Built once at import time; registered by CharacterCmdSet.
ALL_COMMANDS: list = build_all_commands()
