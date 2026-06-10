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
_TIMELESS_VERBS = frozenset({
    "INVENTORY", "SCORE", "SAVE", "RESTOR", "VERSION", "DIAGNOSE",
    "VERBOSE", "BRIEF", "SUPER",
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
    results = search_object_attribute(key="zil_id", strvalue=zil_id)
    return results[0] if results else None


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
    caller.msg("Your score is 0 points out of a possible 80, in 0 moves.")


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
}


# ---------------------------------------------------------------------------
# Command factory
# ---------------------------------------------------------------------------

def make_command(verb: str, entries: list, aliases: list, timeless: bool = False) -> type:
    """Return a PlanetfallCommand subclass for the given verb."""
    # Most specific (longest) pattern tried first
    sorted_entries = sorted(entries, key=lambda e: len(e[0]), reverse=True)

    def func(self):
        args = self.args.strip()
        for pattern, handler_name in self._pf_entries:
            nouns = _match_pattern(args, pattern)
            if nouns is not None:
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
