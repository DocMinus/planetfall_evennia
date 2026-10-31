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

from evennia.commands.cmdhandler import CMD_NOMATCH as _CMD_NOMATCH

from commands.command import PlanetfallCommand

DATA_DIR = Path(__file__).parent / "data"

_ARTICLES = frozenset({"the", "a", "an", "some", "my", "your"})
_SKIP_TOKENS = frozenset({"RANDOM", "COMMAND", "RECORD", "UNRECORD"})

# Verbs that consume no game time — PlanetfallCommand.timeless = True
# AGAIN is timeless because it replays the previous command, whose own
# at_post_cmd advances the clock — counting AGAIN too would double-tick.
_TIMELESS_VERBS = frozenset({
    "INVENTORY", "SCORE", "SAVE", "RESTOR", "VERSION", "DIAGNOSE",
    "VERBOSE", "BRIEF", "SUPER", "AGAIN", "TIME",
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


def _vocab_tokens(*groups) -> set:
    """Lower-cased token set over one or more word lists, splitting hyphens.

    ZIL keeps multi-word adjectives like ``WIDE-NOSED`` / ``PROTEIN-RICH`` and
    compound card names as single hyphenated tokens, but a player naturally
    types them spaced ("wide nosed pliers") or hyphenated either way. Splitting
    on hyphens on both the input and the object vocabulary lets all spellings
    match (e.g. "upper-elevator card" == "upper elevator card", "high protein"
    matching the PROTEIN-RICH liquid). Accepts several iterables so callers can
    pass Evennia ``_SaverList`` attributes without concatenating them.
    """
    out = set()
    for group in groups:
        for s in group:
            for tok in str(s).lower().split():
                out.add(tok)
                if "-" in tok:
                    out.update(p for p in tok.split("-") if p)
    return out


def _article(obj) -> str:
    return "an" if "VOWELBIT" in (obj.db.obj_flags or []) else "a"


# ZIL pronoun words that resolve to P-IT-OBJECT (the last-referenced object).
_PRONOUNS = frozenset({"it", "them", "him", "her", "that"})


def _global_object_ids():
    """ZIL GLOBAL-OBJECTS — zil_ids of objects in scope from *every* room.

    The loader flags each GLOBAL-OBJECTS member (floor, hands, doorway, …) with
    a "GLOBAL-OBJECT" obj_flag. Membership is static game data, so cache the
    small set (~6) once; the zil_ids stay valid across world reloads.
    """
    global _GLOBAL_OBJECT_IDS
    if _GLOBAL_OBJECT_IDS is None:
        from typeclasses.objects import PlanetfallObject
        _GLOBAL_OBJECT_IDS = {
            o.db.zil_id for o in PlanetfallObject.objects.all()
            if o.db.zil_id and "GLOBAL-OBJECT" in (o.db.obj_flags or [])
        }
    return _GLOBAL_OBJECT_IDS


_GLOBAL_OBJECT_IDS = None


class _PseudoAttrs:
    """A stand-in for Evennia's ``db`` handler: unset attributes read as None."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def __getattr__(self, name):
        return None


class PseudoObject:
    """ZIL PSEUDO-OBJECT — a room-local noun backed only by an ACTION routine.

    A room's ``(PSEUDO "TOILET" TOILET-PSEUDO ...)`` pairs are searched by
    GLOBAL-CHECK after every real object. On a match the parser points the
    PSEUDO-OBJECT's ACTION at the routine and copies the typed word into its
    DESC, so default verbs can still say "the toilet". It is reachable only
    while you stay in that room (ACCESSIBLE? via LAST-PSEUDO-LOC).
    """

    location = None
    contents = ()

    def __init__(self, word: str, routine: str, room):
        self.key = word
        self.room = room
        self.db = _PseudoAttrs(zil_id="PSEUDO-OBJECT", action=routine,
                               obj_flags=[])

    def move_to(self, *args, **kwargs):
        return False


def _word_match(typed: str, word: str) -> bool:
    """Z-machine dictionary lookup: words compare on their first six letters
    (so "benches" finds BENCHE and "enunciator" finds ENUNCI)."""
    return typed[:6] == word.lower()[:6]


def _pseudo_match(caller, noun: str):
    """ZIL GLOBAL-CHECK's PSEUDO pass for the current room, or None."""
    room = caller.location
    pseudo = (room.db.pseudo or {}) if room else {}
    for typed in reversed(noun.split()):
        for word, routine in pseudo.items():
            if _word_match(typed, word):
                return PseudoObject(typed, routine, room)
    return None


def _accessible(caller, obj) -> bool:
    """ZIL ACCESSIBLE? — inventory, the room, its local-globals, or a global-object."""
    if isinstance(obj, PseudoObject):
        return obj.room is caller.location
    if obj.location is caller:
        return True
    if "GLOBAL-OBJECT" in (obj.db.obj_flags or []):
        return True   # floor/hands/doorway/… are in scope everywhere
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
    noun = _strip_articles(noun)
    if not noun:
        return None

    # A multi-object command ("drop all except kit") pins each object in turn.
    forced = getattr(caller.ndb, "pf_forced", None)
    if forced is not None and noun == _strip_articles(forced.key):
        return forced
    # So does a command whose ambiguous nouns were settled up front.
    pins = getattr(caller.ndb, "pf_pins", None)
    if pins and noun in pins:
        caller.ndb.last_it = pins[noun]   # THIS-IS-IT
        return pins[noun]

    if noun in _PRONOUNS:
        it = getattr(caller.ndb, "last_it", None)
        return it if (it and _accessible(caller, it)) else None

    found = _matches(caller, noun)
    if not found:
        return None
    caller.ndb.last_it = found[0]   # THIS-IS-IT
    return found[0]


def _obj_vocab(obj) -> set:
    # The display name adds words ZIL never had as vocabulary; "of" (as in
    # "pair of wide-nosed pliers") would match "get out of bed".
    return _vocab_tokens(obj.db.synonyms or [], obj.db.adjectives or [],
                         obj.key.split()) - {"of"}


def _matches(caller, noun: str) -> list:
    """Every in-scope object tied for the best match on `noun`.

    Order is inventory, room, local-globals, global-objects, then the insides
    of open containers. A local object beats a global one with the same score
    (ZIL only tries GLOBAL-CHECK when nothing local matched); when nothing at
    all matches, the room's PSEUDO words are tried last.
    """
    from evennia.objects.objects import DefaultExit

    words = _vocab_tokens([noun])
    candidates: list = list(caller.contents)
    if caller.location:
        for obj in caller.location.contents:
            if obj is not caller and not isinstance(obj, DefaultExit) and obj not in candidates:
                candidates.append(obj)
        for zil_id in (caller.location.db.globals or []):
            obj = _by_zil_id(zil_id)
            if obj and obj not in candidates:
                candidates.append(obj)
    # ZIL GLOBAL-OBJECTS: floor/hands/doorway/… are referable from every room.
    for zil_id in _global_object_ids():
        obj = _by_zil_id(zil_id)
        if obj and obj not in candidates:
            candidates.append(obj)

    # ZIL ACCESSIBLE?: you can also refer to things inside a visible container —
    # one that is open or transparent (the dial in the laser, the speck in the
    # translucent relay). Search one level into such containers.
    def _is_see_in(o):
        # ZIL SEE-INSIDE?: visible contents iff not invisible and (open or
        # transparent) — the relay is TRANSBIT but not a CONTBIT container.
        flags = o.db.obj_flags or []
        return "INVISIBLE" not in flags and (o.db.is_open or "TRANSBIT" in flags)
    for container in list(candidates):
        if _is_see_in(container):
            for inner in container.contents:
                if inner not in candidates and not isinstance(inner, DefaultExit):
                    candidates.append(inner)

    # ZIL THIS-IT?: the phrase's last word is the noun (P-NAM) and must be one
    # of the object's nouns; the words before it are adjectives. Overlap still
    # scores, so loose phrasings resolve, but a head-noun hit outranks them
    # ("laser" is the laser, not the laser setting dial).
    head = _vocab_tokens([noun.split()[-1]])
    best: list = []
    best_score = 0
    for obj in candidates:
        # ZIL ACCESSIBLE?: an INVISIBLE object can't be referred to until it is
        # revealed (e.g. the key hidden in the crevice).
        if "INVISIBLE" in (obj.db.obj_flags or []):
            continue
        score = len(words & _obj_vocab(obj))
        if score and head & _vocab_tokens(obj.db.synonyms or [], obj.key.split()[-1:]):
            score += len(words)   # beats any adjectives-only match
        if score > best_score:
            best, best_score = [obj], score
        elif score and score == best_score:
            best.append(obj)
    tier = min((_scope_tier(caller, o) for o in best), default=3)
    best = [o for o in best if _scope_tier(caller, o) == tier]
    if tier >= 2:
        # ZIL GLOBAL-CHECK: room PSEUDO words come before GLOBAL-OBJECTS.
        pseudo = _pseudo_match(caller, noun)
        if pseudo:
            return [pseudo]
    return best


def _scope_tier(caller, obj) -> int:
    """ZIL search order: 0 held or here, 1 the room's local-globals,
    2 GLOBAL-OBJECTS (floor, hands, doorway, ...)."""
    if "GLOBAL-OBJECT" in (obj.db.obj_flags or []):
        return 2
    loc = caller.location
    if obj.location in (caller, loc) or (
            obj.location is not None and obj.location.location in (caller, loc)):
        return 0
    return 1


# ---------------------------------------------------------------------------
# Pattern matcher
# ---------------------------------------------------------------------------

# ZIL preposition synonym groups (syntax.zil:8-12). A pattern keyword like WITH
# matches any word in its group, so "slide card through slot" hits a WITH slot.
_PREP_SYNONYMS = {
    "WITH": ("with", "using", "through", "thru"),
    "IN": ("in", "inside", "into"),
    "ON": ("on", "onto"),
    "OVER": ("over", "above"),
}


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
            syns = _PREP_SYNONYMS.get(token.upper())
            if syns:
                parts.append("(?:" + "|".join(re.escape(s) for s in syns) + ")")
            else:
                parts.append(re.escape(token.lower()))
    regex = r"^\s*" + r"\s+".join(parts) + r"\s*$"
    m = re.match(regex, args.lower(), re.IGNORECASE)
    return list(m.groups()) if m else None


# ---------------------------------------------------------------------------
# Handlers — each receives (caller, nouns: list[str])
# ---------------------------------------------------------------------------

def build_look(caller, loc, brief_ok=False) -> str:
    """Render a room the ZIL way as a single string.

    Used by the V-LOOK command, by movement, and by connect (via
    Character.at_look), so every room view is consistent: the room name, its
    ACTION-routine description (or static desc), visible objects (NDESCBIT
    objects like the ADVENTURER are hidden), then the obvious exits.

    ``brief_ok`` is set for the automatic look on movement, which honours the
    ZIL verbosity mode (BRIEF default: full description only on a room's first
    visit; VERBOSE: always; SUPER-BRIEF: never). The explicit LOOK command
    always shows the full description regardless of mode.
    """
    from evennia.objects.objects import DefaultExit
    from world.action_handlers import dispatch_room_look
    if not loc:
        return "You are in a void."

    first_visit = loc.db.first_visit
    show_desc = True
    if brief_ok:
        gs = _gs()
        if gs and gs.flags.get("SUPER-BRIEF"):
            show_desc = False
        elif gs and gs.flags.get("VERBOSE"):
            show_desc = True
        else:                       # BRIEF (the ZIL default): full only once
            show_desc = bool(first_visit)

    lines = [f"\n{loc.key}"]
    if show_desc:
        desc = dispatch_room_look(loc, caller)
        if desc is None:
            desc = loc.db.desc or None
        if desc:
            lines.append(desc)
    loc.db.first_visit = False

    gs = _gs()
    spout = gs.flags.get("SPOUT-PLACED") if gs else None   # ZIL DESCRIBE-OBJECT
    visible = [
        obj for obj in loc.contents
        if obj is not caller
        and not isinstance(obj, DefaultExit)
        and "NDESCBIT" not in (obj.db.obj_flags or [])
        and "INVISIBLE" not in (obj.db.obj_flags or [])
        and not (spout and obj.db.zil_id == spout)
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
    _set_elapsed(caller, 9)   # ZIL V-LOOK
    caller.msg(build_look(caller, caller.location))


def _h_examine(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    _set_elapsed(caller, 32)   # ZIL V-EXAMINE
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
    _set_elapsed(caller, 18)   # ZIL V-INVENTORY
    carried = list(caller.contents)
    if not carried:
        caller.msg("You are carrying nothing.")
        return
    caller.msg("You are carrying:")
    for obj in carried:
        worn = " (worn)" if "WORNBIT" in (obj.db.obj_flags or []) else ""
        caller.msg(f"  {_article(obj)} {obj.key}{worn}")


_ALL_WORDS = frozenset({"all", "everything", "both"})


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
        if gs.flags.get("SPOUT-PLACED") == obj.db.zil_id:
            gs.flags["SPOUT-PLACED"] = None   # ZIL ITAKE: off the spout
    caller.msg("Taken.")


def _held(caller, obj) -> bool:
    """ZIL HELD? — carried directly or inside something carried (the pocket)."""
    loc = obj.location
    return loc is caller or (loc is not None and loc.location is caller)


def _h_drop(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if obj is not None and not _held(caller, obj):
        # Best overall match is on the floor; fall back to the best held match
        # so "drop card" still drops the one you carry.
        words = _vocab_tokens([_strip_articles(nouns[0])])
        held = [i for o in caller.contents for i in [o, *o.contents]]
        scored = [(len(words & _vocab_tokens(i.db.synonyms or [], i.db.adjectives or [],
                                             i.key.split())), i) for i in held]
        scored = [s for s in scored if s[0]]
        obj = max(scored, key=lambda s: s[0])[1] if scored else None
    if not obj:
        caller.msg(f"You're not carrying any {nouns[0]}.")
        return
    if "WORNBIT" in (obj.db.obj_flags or []):
        caller.msg("You'll have to take it off, first.")   # ZIL IDROP
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
    _set_elapsed(caller, 40)   # ZIL V-WAIT: <SETG C-ELAPSED 40>
    caller.msg("Time passes...")


def _h_read(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    text = obj.db.obj_text
    if text:
        _set_elapsed(caller, 18)   # ZIL V-READ
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
    _set_elapsed(caller, 18)   # ZIL V-DIAGNOSE
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


def _h_version(caller, nouns):
    """V-VERSION (verbs.zil:?) — the game's copyright/release banner."""
    caller.msg(
        "PLANETFALL\n"
        "Infocom interactive fiction - a science fiction story\n"
        "Copyright (c) 1983 by Infocom, Inc. All rights reserved.\n"
        "PLANETFALL is a registered trademark of Infocom, Inc.\n"
        "Release 37 / Serial number 851003"
    )


def _h_time(caller, nouns):
    """V-TIME (verbs.zil) — the chronometer reading, if you're wearing it."""
    gs = _gs()
    chrono = next((o for o in caller.contents if o.db.zil_id == "CHRONOMETER"), None)
    if chrono is None:
        caller.msg("It's hard to say, since you've removed your chronometer.")
        return
    moves = (gs.chronometer_munged and getattr(gs, "munged_time", 0)) \
        if gs else 0
    if gs and not gs.chronometer_munged:
        moves = gs.internal_moves
    caller.msg(f"According to the chronometer, the current time is {moves}.")


def _h_verbosity(caller, nouns, *, verbose, super_brief, message):
    """V-BRIEF / V-VERBOSE / V-SUPER-BRIEF — set room-description verbosity."""
    gs = _gs()
    if gs:
        gs.flags["VERBOSE"] = verbose
        gs.flags["SUPER-BRIEF"] = super_brief
    caller.msg(message)
    if verbose:                     # ZIL V-VERBOSE re-looks at the room
        caller.msg(build_look(caller, caller.location))


def _h_brief(caller, nouns):
    _h_verbosity(caller, nouns, verbose=False, super_brief=False,
                 message="Brief descriptions.")


def _h_verbose(caller, nouns):
    _h_verbosity(caller, nouns, verbose=True, super_brief=False,
                 message="Maximum verbosity.")


def _h_super_brief(caller, nouns):
    _h_verbosity(caller, nouns, verbose=False, super_brief=True,
                 message="Super-brief descriptions.")


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
    if "CONTBIT" not in (container.db.obj_flags or []):
        caller.msg(f"You can't put things in the {container.key}.")
        return
    if not container.db.is_open:
        caller.msg(f"The {container.key} isn't open.")
        return
    if item is container:
        caller.msg("How can you do that?")
        return
    if item.location is container:
        caller.msg(f"The {item.key} is already in the {container.key}.")
        return
    if not _held(caller, item):
        # ZIL V-PUT: TRYTAKE an item you aren't holding (cards from a desk).
        if "TAKEBIT" not in (item.db.obj_flags or []):
            caller.msg(f"You can't pick up the {item.key}.")
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
    _set_elapsed(caller, 18)   # ZIL V-WEAR
    caller.msg(f"You are wearing the {obj.key}.")


def _h_take_off(caller, nouns):
    obj = resolve_obj(caller, nouns[0])
    if not obj:
        caller.msg(f"You see no {nouns[0]} here.")
        return
    if "WORNBIT" not in (obj.db.obj_flags or []):
        caller.msg("You're not wearing that.")
        return
    obj.db.obj_flags = [f for f in (obj.db.obj_flags or []) if f != "WORNBIT"]
    _set_elapsed(caller, 18)   # ZIL V-TAKE-OFF
    caller.msg(f"You are no longer wearing the {obj.key}.")


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
    # Keep any "Which ...?" answer, for objects still in reach.
    pins = getattr(caller.ndb, "last_pins", None) or {}
    caller.ndb.pf_preset = {k: o for k, o in pins.items() if o in _matches(caller, k)}
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
    # ZIL V-PUSH INTNUM branch (verbs.zil:1085): pushing a booth number presses
    # the matching teleport button (or rejects it if it's this very booth).
    if nouns and _strip_articles(nouns[0]).strip() in ("1", "2", "3"):
        n = _strip_articles(nouns[0]).strip()
        here = caller.location.db.zil_id if caller.location else None
        if here in ("BOOTH-1", "BOOTH-2", "BOOTH-3"):
            if here == f"BOOTH-{n}":
                caller.msg(f"There's no button here that's labelled with the "
                           f"number {n}.")
                return
            from world.action_handlers import _by_zil_id as _byz, handle_teleport_button, ObjAction
            btn = _byz(f"TELEPORTATION-BUTTON-{n}")
            if btn:
                handle_teleport_button(ObjAction(obj=btn, caller=caller,
                                                 verb="V-PUSH", nouns=nouns))
                return
        caller.msg("Push a number?!?")
        return
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
    # ZIL SAFETY-WEB-F / BED M-BEG: you must leave the vehicle before walking.
    gs = _gs()
    if gs and (gs.flags.get("IN-WEB") or gs.flags.get("IN-BED")):
        caller.msg("You'll have to stand up, first.")
        return
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
    # ZIL V-LEAVE: disembark a vehicle if in one, else walk out.
    gs = _gs()
    if gs and (gs.flags.get("IN-WEB") or gs.flags.get("IN-BED")):
        _h_disembark(caller, nouns)
    else:
        _do_walk(caller, "out")

def _h_climb_up(caller, nouns):
    _do_walk(caller, "up")

def _h_climb_down(caller, nouns):
    _do_walk(caller, "down")

def _h_walk_dir(caller, nouns):
    """V-WALK-AROUND / V-WALK-TO — ZIL USE-DIRECTIONS prompt."""
    caller.msg("You should specify a direction to walk.")


# --- vehicle boarding: SAFETY-WEB (pod) and BED (survival) ------------------
# ZIL models these as VEHBIT objects you move ADVENTURER into, while HERE stays
# the room. We track occupancy as GameState flags (IN-WEB / IN-BED) so HERE,
# look, and the interrupts keep working.

def _here_zil(caller) -> str | None:
    return caller.location.db.zil_id if caller.location else None


def _stand_from_web(caller) -> None:
    """ZIL SAFETY-WEB-F EXIT/STAND branch (globals.zil:986)."""
    gs = _gs()
    if not gs:
        return
    gs.flags["IN-WEB"] = False
    # After the pod has landed (TRIP-COUNTER>14), standing up starts it sinking.
    if gs.flags.get("TRIP-COUNTER", 0) > 14 and not _sink_running(gs):
        from world.interrupts import make_i_sink_pod
        gs.scheduler.enable(gs.scheduler.queue(make_i_sink_pod(gs), -1))
        caller.msg("As you stand, the pod shifts slightly and you feel it "
                   "falling. A moment later, the fall stops with a shock, and "
                   "you see water rising past the viewport.")
    else:
        caller.msg("You are standing again.")


def _sink_running(gs) -> bool:
    return any(getattr(i.routine, "__name__", None) == "i_sink_pod" and i.enabled
               for i in gs.scheduler._interrupts)


def _h_board(caller, nouns):
    """V-BOARD (verbs.zil:860) — board a vehicle (SAFETY-WEB / BED)."""
    obj = _prso(caller, nouns)
    if not obj:
        return
    gs = _gs()
    zid = obj.db.zil_id
    if zid == "SAFETY-WEB":
        if gs:
            gs.flags["IN-WEB"] = True
        caller.msg("You are now in the safety webbing.")
    elif zid == "BED":
        here = _here_zil(caller)
        if here == "INFIRMARY" and gs:
            # The infirmary's diagnostic robot is a death trap (BED-F).
            gs.die("You climb into the bed. It is soft and comfortable. After "
                   "a few moments, a previously unseen panel opens, and a "
                   "diagnostic robot comes wheeling out. As the robot straps "
                   "you to the bed, it injects you with all 347 serums and "
                   "medicines it carries. The last thing you notice before you "
                   "pass out is the robot preparing to saw your legs off.")
            return
        if gs:
            gs.flags["IN-BED"] = True
            if gs.flags.get("SLEEPY-LEVEL", 0) > 0:
                from world.interrupts import make_i_fall_asleep, _disable_self
                _disable_self(gs, "i_sleep_warnings")
                gs.scheduler.enable(
                    gs.scheduler.queue(make_i_fall_asleep(gs), 16))
                caller.msg("Ahhh...the bed is soft and comfortable. You should "
                           "be asleep in short order.")
                return
        caller.msg("You are now in bed.")
    elif "VEHBIT" in (obj.db.obj_flags or []):
        caller.msg(f"You are now in the {obj.key}.")
    else:
        caller.msg(f"I suppose you have a theory on boarding {_article(obj)} "
                   f"{obj.key}.")


def _h_sit(caller, nouns):
    """V-SIT (verbs.zil:1262) — sit; in the pod or a dorm this boards a vehicle."""
    here = _here_zil(caller)
    if here == "ESCAPE-POD":
        caller.msg("(in the web)")
        _h_board(caller, ["safety webbing"])
    elif here in ("DORM-A", "DORM-B", "DORM-C", "DORM-D", "INFIRMARY"):
        caller.msg("(on the bed)")
        _h_board(caller, ["bed"])
    else:
        _set_elapsed(caller, 31)
        caller.msg("You recline on the floor for a bit, and then stand up "
                   "again.")


def _h_stand(caller, nouns):
    """V-STAND (verbs.zil:892) — stand up / out of a vehicle."""
    gs = _gs()
    if gs and gs.flags.get("IN-WEB"):
        _stand_from_web(caller)
    elif gs and gs.flags.get("IN-BED"):
        gs.flags["IN-BED"] = False
        caller.msg("You are on your own feet again.")
    else:
        caller.msg("You are already standing, I think.")


def _h_disembark(caller, nouns):
    """V-DISEMBARK / EXIT from a vehicle (verbs.zil:866)."""
    gs = _gs()
    if gs and gs.flags.get("IN-WEB"):
        _stand_from_web(caller)
    elif gs and gs.flags.get("IN-BED"):
        gs.flags["IN-BED"] = False
        caller.msg("You are on your own feet again.")
    else:
        _do_walk(caller, "out")


def _h_zap(caller, nouns):
    """V-ZAP / V-SZAP fallback (verbs.zil PRE-ZAP) — fire the held laser.

    Reached only when no object's ACTION consumed the verb (e.g. "fire at
    speck" with the laser implied from inventory). Locates the laser and the
    target and re-dispatches to LASER-F.
    """
    from world.action_handlers import dispatch_object
    laser = next((o for o in caller.contents if o.db.zil_id == "LASER"), None)
    target_noun = None
    for n in nouns:
        obj = resolve_obj(caller, n)
        if obj and obj.db.zil_id == "LASER":
            laser = obj
        elif n and n.strip():
            target_noun = n
    if not laser:
        caller.msg("You have nothing to shoot it with.")
        return
    dn = [laser.key] + ([target_noun] if target_noun else [])
    if not dispatch_object(laser, caller, "V-ZAP", dn):
        caller.msg("Nothing happens.")


def _h_type(caller, nouns):
    """V-TYPE (verbs.zil:1630) — the mini-booth sector keypad."""
    gs = _gs()
    here = caller.location.db.zil_id if caller.location else None
    num = next((int(t.strip()) for t in nouns if t.strip().isdigit()), None)
    if here == "LIBRARY-LOBBY":
        from world.library import library_type
        library_type(caller, num)
        return
    if here != "MINI-BOOTH":
        caller.msg("Type on what???")
        return
    if num is None:
        caller.msg("This keyboard only has numeric keys. You can type numbers "
                   "on it, but not words.")
        return
    if not (gs and gs.flags.get("MINI-ACTIVATED")):
        caller.msg("A recording says \"Internal computer repair booth not "
                   "activated.\"")
        return
    if num == 384:
        caller.msg("You notice the walls of the booth sliding away in all "
                   "directions, followed by a momentary queasiness in the pit "
                   "of your stomach...\n")
        station = _by_zil_id("STATION-384")
        if station:
            caller.move_to(station, quiet=True, move_type="teleport")
        gs.flags["BEEN-HERE"] = True   # set AFTER the move (M-ENTER no-ops)
        caller.msg(build_look(caller, caller.location))
    elif num < 10:
        caller.msg("After a pause a recorded voice says \"There are no "
                   "one-digit computer sectors...clearing entry...please type "
                   "damaged sector number.\"")
    elif num > 1024:
        caller.msg("A recorded voice says \"Databanks indicate no computer "
                   "sector corresponding to that number. Please check with your "
                   "supervisor.\"")
    else:
        gs.die("Ooops! You seem to have transported yourself into an active "
               "sector of the computer. You are fried by powerful electric "
               "currents.")


def _h_point(caller, nouns):
    """V-POINT (verbs.zil:1517) — object actions (robot hole) get first crack."""
    floyd = next((o for o in (caller.location.contents if caller.location else [])
                  if o.db.zil_id == "FLOYD"), None)
    if floyd:
        caller.msg("Floyd looks where you're pointing, but sees nothing of "
                   "interest.")
    else:
        caller.msg("It's usually impolite to point.")


def _h_sleep(caller, nouns):
    """V-SLEEP (verbs.zil:1726)."""
    gs = _gs()
    sleepy = gs.flags.get("SLEEPY-LEVEL", 0) if gs else 0
    if sleepy == 0:
        caller.msg("You're not tired!")
        return
    falling = gs and any(
        getattr(i.routine, "__name__", None) == "i_fall_asleep" and i.enabled
        for i in gs.scheduler._interrupts
    )
    if falling:
        caller.msg("You'll probably be asleep before you know it.")
    else:
        caller.msg("Civilized members of society usually sleep in beds.")


def _h_stub(caller, nouns):
    caller.msg("That action isn't implemented yet.")


# ---------------------------------------------------------------------------
# Trivial / flavour verbs (Phase 14b item 3) — faithful ZIL defaults so a
# player who types these gets the real response, not "isn't implemented yet".
# ---------------------------------------------------------------------------

def _here_actor(caller):
    """First ACTORBIT (NPC) in the player's room, ZIL <FIND-IN HERE ACTORBIT>."""
    loc = caller.location
    if not loc:
        return None
    return next((o for o in loc.contents
                 if "ACTORBIT" in (o.db.obj_flags or [])), None)


def _h_look_down(caller, nouns):
    """V-LOOK-DOWN (verbs.zil:1054) — examine the ground."""
    _h_examine(caller, ["ground"])


def _h_say(caller, nouns):
    """V-SAY (verbs.zil:1400) — you must address an NPC directly, else talk to self."""
    actor = _here_actor(caller)
    if actor:
        caller.msg(f"You must address the {actor.key} directly.")
    else:
        caller.msg("Talking to yourself is a sign of impending mental collapse.")


def _h_stand_on(caller, nouns):
    """V-STAND-ON (verbs.zil:1571)."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    name = obj.key if obj else "that"
    caller.msg(f"Standing on the {name} seems like a waste of time.")


def _h_play_with(caller, nouns):
    """V-PLAY-WITH (verbs.zil:1819)."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if obj and "ACTORBIT" in (obj.db.obj_flags or []):
        caller.msg(f"The {obj.key} doesn't seem interested in playing games.")
    else:
        caller.msg("I sometimes wonder about your mental health.")


def _h_swim(caller, nouns):
    """V-SWIM / V-SWIM-DIR / V-SWIM-UP (verbs.zil:1236) — no water to swim in."""
    caller.msg("You can't swim here!")


def _h_find(caller, nouns):
    """V-FIND (verbs.zil:1337) — where is the object?"""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if obj is None:
        caller.msg("Beats me.")
        return
    _set_elapsed(caller, 18)   # ZIL V-FIND
    loc = obj.location
    if obj.location is caller:
        caller.msg("You have it.")
    elif loc is caller.location:
        caller.msg("It's right here.")
    elif loc and "ACTORBIT" in (loc.db.obj_flags or []):
        caller.msg(f"The {loc.key} has it.")
    elif loc and loc is not caller and loc is not caller.location:
        caller.msg(f"It's in the {loc.key}.")
    else:
        caller.msg("Beats me.")


def _h_reach_for(caller, nouns):
    """V-REACH-FOR (verbs.zil:1583) — take it if you can, else it's out of reach."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if obj and "TAKEBIT" in (obj.db.obj_flags or []):
        _h_take(caller, nouns)
    elif obj and obj.location is caller.location:
        caller.msg("It's here! Now what?")
    else:
        caller.msg("It is out of reach.")


def _h_shake(caller, nouns):
    """V-SHAKE (verbs.zil:1169) — held check, container rattles/spills, else Shaken."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if obj is None:
        caller.msg("Shaken.")
        return
    if obj.location is not caller and obj.db.zil_id != "HANDS":
        caller.msg("You aren't holding it.")
        return
    contents = list(obj.contents)
    if obj.db.is_open and contents:
        for item in contents:
            item.move_to(caller.location, quiet=True, move_type="drop")
        caller.msg(f"The contents of the {obj.key} spill onto the floor.")
    elif contents:
        caller.msg(f"It sounds as if there is something inside the {obj.key}.")
    else:
        caller.msg("Shaken.")


def _h_shake_with(caller, nouns):
    """V-SHAKE-WITH (verbs.zil:1215) — shaking hands and such."""
    target = resolve_obj(caller, nouns[1]) if len(nouns) > 1 else None
    if target and "ACTORBIT" in (target.db.obj_flags or []):
        caller.msg(f"The {target.key} doesn't appear interested in shaking hands.")
    else:
        caller.msg("Huh?")


def _h_empty(caller, nouns):
    """V-EMPTY (verbs.zil:1706)."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if obj is None:
        caller.msg("You can't empty that.")
        return
    if not obj.db.is_open:
        caller.msg("You can't empty it when it's closed!")
        return
    contents = list(obj.contents)
    if contents:
        for item in contents:
            item.move_to(caller.location, quiet=True, move_type="drop")
        caller.msg(f"The {obj.key} is now empty.")
    else:
        caller.msg(f"There's nothing in the {obj.key}.")


def _h_script(caller, nouns):
    """V-SCRIPT (verbs.zil:781) — transcripting is a no-op in this port."""
    caller.msg("Here begins a transcript of interaction with Planetfall.")


def _h_unscript(caller, nouns):
    """V-UNSCRIPT (verbs.zil:791)."""
    caller.msg("Here ends a transcript of interaction with Planetfall.")


def _h_verify(caller, nouns):
    """V-$VERIFY (verbs.zil:1536) — the game-file integrity check."""
    caller.msg("Verifying...\nGame correct. (YAY!)")


def _h_debug_noop(caller, nouns):
    """V-$COMMAND/$RANDOM/$RECORD/$UNRECORD — developer internals; harmless here."""
    caller.msg("Nothing happens.")


def _h_throw(caller, nouns):
    """V-THROW (verbs.zil:660) — a bare throw just drops the object."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if obj and obj.location is caller:
        obj.move_to(caller.location, quiet=True, move_type="drop")
        caller.msg("Thrown.")
    else:
        caller.msg("You're not holding it.")


def _h_leap(caller, nouns):
    """V-LEAP (verbs.zil:942) — generic jump fallback."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if obj is None:
        _h_skip(caller, nouns)
    elif obj.location is caller.location:
        if "ACTORBIT" in (obj.db.obj_flags or []):
            caller.msg(f"The {obj.key} is too big to jump over.")
        else:
            _h_skip(caller, nouns)
    else:
        caller.msg("That would be a good trick.")


def _h_escape(caller, nouns):
    """V-ZESCAPE (verbs.zil:1798)."""
    here = caller.location.db.zil_id if caller.location else None
    if here == "BRIG":
        caller.msg("Houdini himself would be stumped by this cell.")
    else:
        caller.msg("There is no escape. We control the horizontal. We control "
                   "the vertical. We control the disk drives...")


def _h_eat_from(caller, nouns):
    """V-EAT-FROM (verbs.zil:920) — eat the lone item in an open container."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    if obj is None:
        caller.msg("You can't eat from that.")
        return
    if not obj.db.is_open:
        caller.msg("It's closed.")
        return
    contents = list(obj.contents)
    if len(contents) > 1:
        caller.msg(f"There's more than one thing in the {obj.key}.")
    elif contents:
        _h_eat(caller, [contents[0].key])
    else:
        caller.msg("It's empty!")


def _h_quit(caller, nouns):
    """V-QUIT (verbs.zil:288) — show the score, point at the real exit."""
    _h_score(caller, nouns)
    caller.msg("\nTo leave the game, type SAVE to keep your progress, close "
               "the browser, then run `make stop` to shut down the server.")


def _h_restart(caller, nouns):
    """V-RESTART (verbs.zil:395) — not offered as an in-game verb in this port."""
    caller.msg("You can't restart from here. RESTORE a saved game to pick up "
               "where you left off.")


def _h_attract(caller, nouns):
    """V-ATTRACT / V-ZATTRACT (verbs.zil:1615) — generic (no magnet) fallback."""
    caller.msg("Nothing interesting happens.")


def _h_through(caller, nouns):
    """V-THROUGH (globals.zil:375) — generic fallback for a solid object."""
    obj = resolve_obj(caller, nouns[0]) if nouns else None
    name = obj.key if obj else "that"
    caller.msg(f"You hit your head against the {name} as you attempt this feat.")


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
    "V-VERSION":     _h_version,
    "V-TIME":        _h_time,
    "V-BRIEF":       _h_brief,
    "V-VERBOSE":     _h_verbose,
    "V-SUPER-BRIEF": _h_super_brief,
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
    # Vehicle boarding (pod safety-web / beds) — engine #4 + survival.
    "V-BOARD":       _h_board,
    "V-CLIMB-ON":    _h_board,
    "V-SIT":         _h_sit,
    "V-SIT-DOWN":    _h_sit,
    "V-STAND":       _h_stand,
    "V-DISEMBARK":   _h_disembark,
    "V-SLEEP":       _h_sleep,
    "V-POINT":       _h_point,
    "V-TYPE":        _h_type,
    "V-ZAP":         _h_zap,
    "V-SZAP":        _h_zap,
    # Phase 14b item 3 — trivial flavour / debug verbs.
    "V-SAY":         _h_say,
    "V-SHAKE":       _h_shake,
    "V-SHAKE-WITH":  _h_shake_with,
    "V-EMPTY":       _h_empty,
    "V-FIND":        _h_find,
    "V-PLAY-WITH":   _h_play_with,
    "V-SWIM":        _h_swim,
    "V-SWIM-DIR":    _h_swim,
    "V-SWIM-UP":     _h_swim,
    "V-LOOK-DOWN":   _h_look_down,
    "V-REACH-FOR":   _h_reach_for,
    "V-STAND-ON":    _h_stand_on,
    "V-SCRIPT":      _h_script,
    "V-UNSCRIPT":    _h_unscript,
    "V-$VERIFY":     _h_verify,
    "V-$COMMAND":    _h_debug_noop,
    "V-$RANDOM":     _h_debug_noop,
    "V-$RECORD":     _h_debug_noop,
    "V-$UNRECORD":   _h_debug_noop,
    "V-THROW":       _h_throw,
    "V-LEAP":        _h_leap,
    "V-ZESCAPE":     _h_escape,
    "V-EAT-FROM":    _h_eat_from,
    "V-SGIVE":       _h_give,
    "V-QUIT":        _h_quit,
    "V-RESTART":     _h_restart,
    "V-ATTRACT":     _h_attract,
    "V-ZATTRACT":    _h_attract,
    "V-THROUGH":     _h_through,
    "V-CLIMB-FOO":   _h_climb_up,
}


# ---------------------------------------------------------------------------
# Command factory
# ---------------------------------------------------------------------------

def _dispatch(caller, handler_name: str, nouns: list) -> None:
    """Run one parsed command: object ACTIONs first, then the generic handler."""
    from world.action_handlers import dispatch_object
    # ZIL BED-F M-BEG: from the bed you can't reach anything but the bed.
    gs = _gs()
    if (gs and gs.flags.get("IN-BED") and nouns
            and handler_name in ("V-TAKE", "V-OPEN", "V-CLOSE", "V-RUB")):
        target = resolve_obj(caller, nouns[0])
        if target is None or target.db.zil_id != "BED":
            caller.msg("You can't reach it from here.")
            return
    # ZIL action precedence: the direct object's (PRSO) ACTION gets first
    # crack at the verb, then the indirect object's (PRSI) — e.g. SLOT-F fires
    # when the slot is the PRSI of SLIDE/PUT. If either consumes the verb, skip
    # the generic handler. Room PSEUDO words resolve to a PseudoObject whose
    # ACTION is the room's routine, so they dispatch the same way.
    if nouns:
        obj = resolve_obj(caller, nouns[0])
        if obj and dispatch_object(obj, caller, handler_name, nouns):
            return
        if len(nouns) > 1:
            prsi = resolve_obj(caller, nouns[1])
            if prsi and dispatch_object(prsi, caller, handler_name, nouns):
                return
    HANDLERS.get(handler_name, _h_stub)(caller, nouns)


# Verbs whose omitted object is the room (syntax.zil FIND RMUNGBIT).
_ROOM_OBJECT_HANDLERS = frozenset({"V-STAND", "V-DISEMBARK"})

# Verbs whose direct object is MANY in syntax.zil: they accept "all",
# "all except X" and "X and Y".
_MANY_HANDLERS = frozenset({"V-TAKE", "V-DROP", "V-PUT", "V-PUT-ON",
                            "V-PUT-UNDER", "V-GIVE"})
_EXCEPT_RE = re.compile(r"\s+(?:except|but)\s+")
_AND_RE = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+")


def _expand_many(caller, handler_name: str, noun: str):
    """Expand a multi-object noun phrase, or return None for a single object.

    ZIL MANY: "all" is everything takeable here (TAKE) or everything held
    (DROP/PUT/GIVE), optionally minus an "except"/"but" list; "X and Y" /
    "X, Y" names each object. Unresolvable names are reported and skipped.
    """
    noun = _strip_articles(noun)
    head, _, rest = noun.partition(" ")
    if head in _ALL_WORDS:
        m = _EXCEPT_RE.match(" " + rest) if rest else None
        if rest and not m:
            return None
        if handler_name == "V-TAKE":
            from evennia.objects.objects import DefaultExit
            room = caller.location
            objs = [o for o in (room.contents if room else [])
                    if o is not caller and not isinstance(o, DefaultExit)
                    and "TAKEBIT" in (o.db.obj_flags or [])
                    and "INVISIBLE" not in (o.db.obj_flags or [])]
        else:
            objs = list(caller.contents)
        if m:
            skip = {resolve_obj(caller, n) for n in _AND_RE.split(rest[m.end() - 1:])}
            objs = [o for o in objs if o not in skip]
        return objs
    if not _AND_RE.search(noun):
        return None
    objs = []
    for part in _AND_RE.split(noun):
        obj = resolve_obj(caller, part) if part else None
        if obj is None:
            caller.msg(f"You can't see any {part} here.")
        elif obj not in objs:
            objs.append(obj)
    return objs


def _dispatch_many(caller, handler_name: str, nouns: list, objs: list) -> None:
    """Run the verb once per object, prefixing each reply "name: " like ZIL."""
    if not objs:
        caller.msg("There is nothing here to take." if handler_name == "V-TAKE"
                   else "You aren't carrying anything.")
        return
    orig_msg = caller.msg
    shadowed = "msg" in vars(caller)   # e.g. a test harness capturing output
    for obj in objs:
        first = [True]

        def _prefixed(text=None, _obj=obj, _first=first, **kwargs):
            if _first[0] and isinstance(text, str):
                _first[0] = False
                text = f"{_obj.key}: {text}"
            return orig_msg(text, **kwargs)

        caller.msg = _prefixed
        caller.ndb.pf_forced = obj
        try:
            _dispatch(caller, handler_name, [_strip_articles(obj.key)] + list(nouns[1:]))
        finally:
            caller.ndb.pf_forced = None
            if shadowed:
                caller.msg = orig_msg
            else:
                del caller.msg


# Verbs whose noun slots hold words or directions rather than objects.
_NO_WHICH_HANDLERS = frozenset({"V-WALK", "V-SAY", "V-TYPE", "V-ANSWER",
                                "V-REPLY", "V-TELL", "V-AGAIN"})
# ZIL syntax location bits (P-SLOCBITS) that narrow an ambiguous match.
_LOC_BITS = frozenset({"HELD", "CARRIED", "ON-GROUND", "IN-ROOM"})


def _loc_ok(caller, obj, bits) -> bool:
    """Does `obj` sit where a syntax slot's location bits say to look?"""
    loc, here = obj.location, caller.location
    if loc is caller:
        return "HELD" in bits
    if loc is not None and loc.location is caller:
        return "CARRIED" in bits
    if loc is not None and loc is here:
        return "ON-GROUND" in bits
    if loc is not None and loc.location is here:
        return "IN-ROOM" in bits
    return False


def _which_print(caller, noun: str, objs: list) -> None:
    """ZIL WHICH-PRINT — "Which liquid do you mean, the X or the Y?"."""
    names = [f"the {o.key}" for o in objs]
    if len(names) == 2:
        listed = f"{names[0]} or {names[1]}"
    else:
        listed = ", ".join(names[:-1]) + ", or " + names[-1]
    caller.msg(f"Which {noun} do you mean, {listed}?")


def _settle_nouns(caller, raw: str, handler_name: str, nouns: list,
                  filters: list, skip_first: bool) -> dict | None:
    """ZIL GET-OBJECT for each noun slot: pin the one object each names.

    When several objects tie, the syntax slot's location bits (HELD for DROP,
    ON-GROUND for TAKE, ...) get a chance to narrow them. Still more than one
    and the parser asks which, remembering the command so a bare reply ("the
    chemical one") finishes it (ZIL ORPHAN). Returns the pins, or None after
    asking.
    """
    pins = dict(getattr(caller.ndb, "pf_preset", None) or {})
    caller.ndb.pf_preset = None
    if handler_name in _NO_WHICH_HANDLERS:
        return pins
    for idx, noun in enumerate(nouns):
        key = _strip_articles(noun)
        if (idx == 0 and skip_first) or not key or key in pins or key in _PRONOUNS:
            continue
        found = _matches(caller, key)
        if len(found) > 1:
            bits = _LOC_BITS.intersection(filters[idx] if idx < len(filters) else [])
            narrowed = [o for o in found if _loc_ok(caller, o, bits)] if bits else []
            found = narrowed or found
        if len(found) == 1:
            pins[key] = found[0]
        elif found:
            _which_print(caller, key, found)
            caller.ndb.pf_orphan = {"cmd": raw, "noun": key, "cands": found,
                                    "pins": pins}
            return None
    return pins


def orphan_merge(caller, reply: str) -> bool:
    """ZIL ORPHAN-MERGE — finish a "Which X do you mean?" command.

    A reply that picks out exactly one of the offered objects ("chemical",
    "the red one") reruns the original command with that object. Anything
    else is a new command, and the question is forgotten. A reply whose first
    word is also a verb still counts when that word describes one of the
    objects (ZIL retrofix #30, e.g. "lower" for the lower elevator card).
    """
    orphan = getattr(caller.ndb, "pf_orphan", None)
    caller.ndb.pf_orphan = None
    if not orphan:
        return False
    words = [w for w in _strip_articles(reply).split() if w != "one"]
    if not words:
        return False
    cands = orphan["cands"]
    vocabs = [_obj_vocab(o) for o in cands]
    if not any(words[0] in v for v in vocabs):
        return False
    typed = _vocab_tokens([" ".join(words)])
    scores = [len(typed & v) for v in vocabs]
    top = max(scores)
    winners = [o for o, s in zip(cands, scores) if s == top]
    if len(winners) != 1:
        return False
    caller.ndb.pf_preset = {**orphan["pins"], orphan["noun"]: winners[0]}
    caller.execute_cmd(orphan["cmd"])
    return True


def make_command(verb: str, entries: list, aliases: list, timeless: bool = False) -> type:
    """Return a PlanetfallCommand subclass for the given verb."""
    # Most specific (longest) pattern tried first
    sorted_entries = sorted(entries, key=lambda e: len(e[0]), reverse=True)

    def func(self):
        args = self.args.strip()
        caller = self.caller
        # ZIL GWIM: "get up" / "get out" leave off an object found by
        # (FIND RMUNGBIT) — the room itself — so they mean STAND/DISEMBARK.
        # Checked first, or plain "take OBJECT" would claim "up" as a noun.
        for pattern, handler_name, _ in self._pf_entries:
            if (handler_name in _ROOM_OBJECT_HANDLERS and pattern[-1:] == ["OBJECT"]
                    and _match_pattern(args, pattern[:-1]) == []):
                caller.ndb.last_command = self.raw_string.strip()
                HANDLERS.get(handler_name, _h_stub)(caller, [])
                return
        for pattern, handler_name, filters in self._pf_entries:
            nouns = _match_pattern(args, pattern)
            if nouns is not None:
                raw = self.raw_string.strip()
                many = (_expand_many(caller, handler_name, nouns[0])
                        if nouns and handler_name in _MANY_HANDLERS else None)
                pins = _settle_nouns(caller, raw, handler_name, nouns, filters,
                                     skip_first=many is not None)
                if pins is None:
                    # Asking "Which ...?" is a parser failure: no time passes.
                    caller.ndb.pf_clock_handled = True
                    return
                # Phase 9: remember the last real command so AGAIN/G can replay
                # it. AGAIN itself is never recorded, so "g g" still repeats the
                # original verb rather than chasing its own tail.
                if handler_name != "V-AGAIN":
                    caller.ndb.last_command = raw
                    caller.ndb.last_pins = pins
                outer = getattr(caller.ndb, "pf_pins", None)
                caller.ndb.pf_pins = pins
                try:
                    if many is None:
                        _dispatch(caller, handler_name, nouns)
                    else:
                        _dispatch_many(caller, handler_name, nouns, many)
                finally:
                    caller.ndb.pf_pins = outer
                return
        caller.msg("I don't understand what you mean.")

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
        entries_by_verb.setdefault(verb, []).append(
            (pattern, entry["handler"], entry.get("slot_filters") or []))

    return [
        make_command(
            verb,
            entries,
            aliases_by_verb.get(verb, []),
            timeless=(verb in _TIMELESS_VERBS),
        )
        for verb, entries in entries_by_verb.items()
    ]


class CmdOrderFloyd(PlanetfallCommand):
    """
    Give Floyd an order: FLOYD, <command>  (e.g. "floyd, go north").
    """
    key = "floyd,"
    aliases = ["floyd", "robot,"]

    def func(self):
        from world.action_handlers import order_floyd
        self.caller.ndb.last_command = self.raw_string.strip()
        order_floyd(self.caller, self.args.strip().lstrip(",").strip())


_NOUN_WORDS = None


def _noun_words() -> set:
    """Every object's vocabulary (cached: game data never changes)."""
    global _NOUN_WORDS
    if _NOUN_WORDS is None:
        from typeclasses.objects import PlanetfallObject
        _NOUN_WORDS = set()
        for o in PlanetfallObject.objects.all():
            _NOUN_WORDS |= _obj_vocab(o)
    return _NOUN_WORDS


class CmdNoMatch(PlanetfallCommand):
    """Input that starts with no known verb (Evennia's no-match hook).

    Usually the answer to "Which ... do you mean?", picked up by orphan_merge
    in at_pre_cmd. Otherwise the ZIL parser's complaint: a known noun with no
    verb, or a word it doesn't know at all.
    """
    key = _CMD_NOMATCH
    timeless = True

    def func(self):
        words = _strip_articles(self.raw_string).split()
        if not words:
            return
        if words[0] in _noun_words():
            self.caller.msg("There was no verb in that sentence!")
        else:
            self.caller.msg(f'I don\'t know the word "{words[0]}".')


# Built once at import time; registered by CharacterCmdSet. The direction
# fallbacks are appended so a directionless room reports "You can't go that
# way." rather than Evennia's command-not-found suggestions.
ALL_COMMANDS: list = build_all_commands() + [
    _make_direction_command(d, a) for d, a in _DIRECTION_COMMANDS.items()
] + [CmdOrderFloyd, CmdNoMatch]
