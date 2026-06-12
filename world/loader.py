"""
World loader — reads JSON data and creates Evennia objects in the DB.

Run once (server must be running or Django must be set up):
    make load
or interactively:
    make shell
    >>> from world.loader import load_world; load_world()
"""
import json
from pathlib import Path

from django.db import transaction

from evennia import create_object, search_object
from evennia.utils import logger

from typeclasses.exits import PlanetfallExit
from typeclasses.objects import PlanetfallObject
from typeclasses.characters import PlanetfallNPC
from typeclasses.rooms import PlanetfallRoom

DATA_DIR = Path(__file__).parent / "data"

_DIRECTION_ALIASES = {
    "north": ["n"], "south": ["s"], "east": ["e"], "west": ["w"],
    "northeast": ["ne"], "northwest": ["nw"], "southeast": ["se"], "southwest": ["sw"],
    "up": ["u"], "down": ["d"], "out": ["o"],
}

# Object locations that mean "no physical room" — kept at location=None
_FLOATING = {None, "GLOBAL-OBJECTS", "LOCAL-GLOBALS"}


def load_world(data_dir=DATA_DIR):
    """Create all rooms, objects, NPCs, and exits from JSON source data."""
    if _is_loaded():
        print("World already loaded. To reload, delete existing objects first.")
        return

    data_dir = Path(data_dir)
    rooms   = _json(data_dir / "rooms.json")
    objects = _json(data_dir / "objects.json")
    npcs    = _json(data_dir / "npcs.json")
    c_move  = _json(data_dir / "c_move.json")     # per-room directional move costs

    # All-or-nothing: a failure partway through must not leave a half-built
    # world behind, since _is_loaded() would then skip all future loads.
    with transaction.atomic():
        print(f"  Creating {len(rooms)} rooms...")
        room_reg = _create_rooms(rooms, c_move)

        print(f"  Creating {len(objects)} objects...")
        obj_reg = _create_objects(objects)

        print(f"  Creating {len(npcs)} NPCs...")
        npc_reg = _create_npcs(npcs)

        all_reg = {**room_reg, **obj_reg, **npc_reg}

        print("  Placing objects and NPCs...")
        _place_items(objects, all_reg)
        _place_items(npcs, all_reg)

        print("  Wiring exits...")
        _wire_exits(rooms, room_reg)

        print(f"Done: {len(room_reg)} rooms, {len(obj_reg)} objects, {len(npc_reg)} NPCs loaded.")

        from world.scheduler_script import get_or_create_game_state_script
        get_or_create_game_state_script()
        print("  Scheduler initialised.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_loaded():
    """Guard against running load_world twice."""
    from evennia.objects.models import ObjectDB
    return ObjectDB.objects.filter(db_typeclass_path=PlanetfallRoom.path).exists()


def _json(path):
    with open(path) as f:
        return json.load(f)


def _create_rooms(rooms, c_move=None):
    c_move = c_move or {}
    reg = {}
    for r in rooms:
        zil_id = r["id"]
        room = create_object(PlanetfallRoom, key=r["desc"] or zil_id, aliases=[zil_id])
        room.db.zil_id      = zil_id
        room.db.room_flags  = r.get("flags", [])
        room.db.globals     = r.get("globals", [])
        room.db.pseudo      = {p["word"]: p["object"] for p in r.get("pseudo", [])}
        room.db.action      = r.get("action")
        # ZIL C-MOVE: non-default per-direction move costs (V-WALK), e.g. stairs
        # cost more. Absent directions fall back to DEFAULT-MOVE (20).
        room.db.c_move      = c_move.get(zil_id)
        room.db.first_visit = True
        if r.get("ldesc"):
            room.db.desc = r["ldesc"]
        if r.get("value") is not None:
            room.db.value = r["value"]
        reg[r["id"]] = room
    return reg


def _create_objects(objects):
    reg = {}
    for o in objects:
        obj = create_object(PlanetfallObject, key=o["desc"] or o["id"])
        _apply_attrs(obj, o)
        reg[o["id"]] = obj
    return reg


def _create_npcs(npcs):
    reg = {}
    for n in npcs:
        npc = create_object(PlanetfallNPC, key=n["desc"] or n["id"])
        _apply_attrs(npc, n)
        reg[n["id"]] = npc
    return reg


def _apply_attrs(obj, data):
    obj.db.zil_id      = data["id"]
    obj.db.obj_flags   = data.get("flags", [])
    obj.db.action      = data.get("action")
    obj.db.size        = data.get("size")
    obj.db.capacity    = data.get("capacity")
    obj.db.value       = data.get("value")
    obj.db.obj_text    = data.get("text")
    obj.db.fdesc       = data.get("fdesc")
    obj.db.fdesc_seen  = False
    obj.db.synonyms    = data.get("synonyms", [])
    obj.db.adjectives  = data.get("adjectives", [])
    flags = data.get("flags", [])
    obj.db.is_open     = "OPENBIT" in flags
    obj.db.is_locked   = "LOCKBIT" in flags
    if data.get("ldesc"):
        obj.db.desc = data["ldesc"]


def _place_items(items, all_reg):
    """Set .location on each object/NPC after all objects are created."""
    for item in items:
        loc_id = item.get("location")
        obj = all_reg.get(item["id"])
        if obj is None:
            continue

        if loc_id in _FLOATING:
            obj.location = None
            if loc_id == "GLOBAL-OBJECTS":
                flags = list(obj.db.obj_flags or [])
                if "GLOBAL-OBJECT" not in flags:
                    obj.db.obj_flags = flags + ["GLOBAL-OBJECT"]
            continue

        if loc_id == "ADVENTURER":
            # Starter inventory — mark for Phase 5 to hand to player at login
            obj.db.starter_item = True
            obj.location = None
            continue

        target = all_reg.get(loc_id)
        if target is not None:
            obj.location = target
        else:
            logger.log_warn(f"loader: {item['id']} — location '{loc_id}' not in registry")


def _wire_exits(rooms, room_reg):
    for r in rooms:
        src = room_reg.get(r["id"])
        if src is None:
            continue

        for direction, ex in r.get("exits", {}).items():
            exit_type = ex.get("type", "to")
            dest_id   = ex.get("destination")
            dest      = room_reg.get(dest_id) if dest_id else None

            if exit_type == "to" and dest is None:
                logger.log_warn(f"loader: room {r['id']} exit '{direction}' → unknown dest '{dest_id}'")
                continue

            # Blocked/conditional/per exits without a destination use src as
            # placeholder (at_traverse prevents actual travel).
            if dest is None:
                dest = src

            exit_obj = create_object(
                PlanetfallExit,
                key=direction,
                location=src,
                destination=dest,
                aliases=_DIRECTION_ALIASES.get(direction, []),
            )
            exit_obj.db.exit_type = exit_type

            if exit_type == "blocked":
                exit_obj.db.block_message = ex.get("message", "You can't go that way.")
            elif exit_type == "conditional":
                exit_obj.db.flag_key      = ex.get("flag")
                exit_obj.db.required_state = ex.get("state")
            elif exit_type == "per":
                exit_obj.db.routine = ex.get("routine")
