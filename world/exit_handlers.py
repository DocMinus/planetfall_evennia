"""
"per" exit routine registry (Phase 7 framework, Phase 11 real logic).

ZIL supports three kinds of conditional movement:

  IF <door> IS OPEN   → handled directly in PlanetfallExit.at_traverse by
                        reading the door object's open state.
  IF <global> ELSE m  → handled in at_traverse by reading GameState.flags.
  PER <routine>       → a named ZIL routine computes the *destination* (or
                        refuses) — dispatched here via PER_HANDLERS.

A "per" exit's static Evennia destination is just a placeholder (the source
room); the routine returns the real destination, mirroring ZIL where the GO
verb uses the room object the PER routine evaluates to.

Handler contract (Phase 11):

    handler(traverser, exit_obj, gs) -> destination_room | None

  * Return an Evennia room object → at_traverse moves the traverser there.
  * Return None → refuse; the handler has already messaged the player.
  * A routine may set ``gs.c_elapsed`` to model timed transit (ZIL SETG
    C-ELAPSED); at_traverse advances the clock by that amount after the move.

The transport globals these routines read (elevator car positions, shuttle car
locations, the day counter, …) are seeded at their ZIL defaults in
GameState.__init__. The deeper subsystems that *mutate* them (driving the
shuttle, the opening pod-sink sequence) are later work; the traversal layer
itself is a faithful port given those values.
"""
from __future__ import annotations

CANT_GO = "You can't go that way."


def _by_zil_id(zil_id: str):
    """Return the first Evennia object whose db.zil_id matches, or None."""
    from evennia.utils.search import search_object_attribute
    results = search_object_attribute(key="zil_id", strvalue=zil_id)
    return results[0] if results else None


def _gs():
    """Return the live GameState, or None if the script doesn't exist yet."""
    from world.scheduler_script import get_game_state_script
    script = get_game_state_script()
    return script.get_gs() if script else None


def _dir(exit_obj) -> str:
    """The direction the player moved (ZIL PRSO for a GO), e.g. 'north'."""
    return (exit_obj.key or "").lower()


def _here(traverser):
    """ZIL HERE — the zil_id of the traverser's current room."""
    loc = traverser.location
    return loc.db.zil_id if loc else None


def _door_open(zil_id: str) -> bool:
    """ZIL <FSET? door OPENBIT> for a door object looked up by zil_id."""
    door = _by_zil_id(zil_id)
    return bool(door and door.db.is_open)


def _f(gs, name: str, default=False):
    """Read a GameState flag, tolerant of gs being None."""
    return gs.flags.get(name, default) if gs else default


def _door_closed(traverser):
    """ZIL <DOOR-CLOSED> (compone.zil:2529)."""
    traverser.msg("The door is closed.")


# ---------------------------------------------------------------------------
# PER routines — faithful ports. Each returns a destination room or None.
# ---------------------------------------------------------------------------

def per_pod_exit(traverser, exit_obj, gs):
    """POD-EXIT-F (globals.zil:929)."""
    d = _dir(exit_obj)
    if _f(gs, "BLOWUP-COUNTER", 0) > 4:
        # Post-blowup: the pod has come to rest underwater.
        if d == "east":
            traverser.msg(CANT_GO)
            return None
        if not _door_open("POD-DOOR"):
            traverser.msg("The pod door is closed.")
            return None
        gs.c_elapsed = 30
        return _by_zil_id("UNDERWATER")
    # Pre-blowup: the pod is docked on Deck Nine.
    if d == "up":
        traverser.msg(CANT_GO)
        return None
    if not _door_open("POD-DOOR"):
        traverser.msg("The pod door is closed.")
        return None
    return _by_zil_id("DECK-NINE")


def per_long_hall(traverser, exit_obj, gs):
    """LONG-HALL-F (compone.zil:907) — the long featureless corridor."""
    gs.c_elapsed = 160
    if _here(traverser) == "CORRIDOR-JUNCTION":
        traverser.msg(
            "You walk down the long, featureless hallway for a long time. "
            "Finally, you see some doorways ahead...\n"
        )
        return _by_zil_id("DORM-CORRIDOR")
    traverser.msg(
        "You walk down the long, featureless hallway for a long time. "
        "Finally, you see an intersection ahead...\n"
    )
    return _by_zil_id("CORRIDOR-JUNCTION")


def per_elevator_enter(traverser, exit_obj, gs):
    """ELEVATOR-ENTER-F (compone.zil:2441)."""
    d = _dir(exit_obj)
    if d == "north":
        if _door_open("UPPER-ELEVATOR-DOOR") and not _f(gs, "UPPER-ELEVATOR-UP"):
            return _by_zil_id("UPPER-ELEVATOR")
        _door_closed(traverser)
        return None
    if d == "south":
        if _door_open("LOWER-ELEVATOR-DOOR") and _f(gs, "LOWER-ELEVATOR-UP"):
            return _by_zil_id("LOWER-ELEVATOR")
        _door_closed(traverser)
        return None
    traverser.msg(CANT_GO)
    return None


def per_elevator_exit(traverser, exit_obj, gs):
    """ELEVATOR-EXIT-F (compone.zil:2457)."""
    here = _here(traverser)
    if here == "UPPER-ELEVATOR":
        if _door_open("UPPER-ELEVATOR-DOOR"):
            return _by_zil_id("TOWER-CORE" if _f(gs, "UPPER-ELEVATOR-UP") else "ELEVATOR-LOBBY")
        _door_closed(traverser)
        return None
    if here == "LOWER-ELEVATOR":
        if _door_open("LOWER-ELEVATOR-DOOR"):
            return _by_zil_id("ELEVATOR-LOBBY" if _f(gs, "LOWER-ELEVATOR-UP") else "WAITING-AREA")
        _door_closed(traverser)
        return None
    traverser.msg(CANT_GO)
    return None


def per_other_elevator_enter(traverser, exit_obj, gs):
    """OTHER-ELEVATOR-ENTER-F (compone.zil:3169)."""
    if _door_open("LOWER-ELEVATOR-DOOR") and not _f(gs, "LOWER-ELEVATOR-UP"):
        return _by_zil_id("LOWER-ELEVATOR")
    _door_closed(traverser)
    return None


def per_shuttle_enter(traverser, exit_obj, gs):
    """SHUTTLE-ENTER-F (globals.zil:1728) — board a parked shuttle car."""
    here = _here(traverser)
    d = _dir(exit_obj)
    if here == "KALAMONTEE-PLATFORM":
        if d == "north":
            if _f(gs, "BETTY-AT-KALAMONTEE"):
                return _by_zil_id("SHUTTLE-CAR-BETTY")
        elif d == "south":
            if _f(gs, "ALFIE-AT-KALAMONTEE"):
                return _by_zil_id("SHUTTLE-CAR-ALFIE")
        traverser.msg(CANT_GO)
        return None
    if here == "LAWANDA-PLATFORM":
        if d == "north":
            if not _f(gs, "BETTY-AT-KALAMONTEE"):
                return _by_zil_id("SHUTTLE-CAR-BETTY")
        elif d == "south":
            if not _f(gs, "ALFIE-AT-KALAMONTEE"):
                return _by_zil_id("SHUTTLE-CAR-ALFIE")
        traverser.msg(CANT_GO)
        return None
    traverser.msg(CANT_GO)
    return None


def per_shuttle_exit(traverser, exit_obj, gs):
    """SHUTTLE-EXIT-F (globals.zil:1758) — step off onto the current platform."""
    here = _here(traverser)
    if here == "SHUTTLE-CAR-ALFIE":
        return _by_zil_id("KALAMONTEE-PLATFORM" if _f(gs, "ALFIE-AT-KALAMONTEE") else "LAWANDA-PLATFORM")
    if here == "SHUTTLE-CAR-BETTY":
        return _by_zil_id("KALAMONTEE-PLATFORM" if _f(gs, "BETTY-AT-KALAMONTEE") else "LAWANDA-PLATFORM")
    traverser.msg(CANT_GO)
    return None


def per_water_level(traverser, exit_obj, gs):
    """WATER-LEVEL-F (compone.zil:151) — the castle floods as the days pass."""
    here = _here(traverser)
    day = gs.day if gs else 1
    if here == "BALCONY":
        return _by_zil_id("CRAG" if day == 1 else "UNDERWATER")
    if here == "WINDING-STAIR":
        return _by_zil_id("BALCONY" if day < 4 else "UNDERWATER")
    if here == "COURTYARD":
        return _by_zil_id("WINDING-STAIR" if day < 6 else "UNDERWATER")
    traverser.msg(CANT_GO)
    return None


def per_ladder_exit(traverser, exit_obj, gs):
    """LADDER-EXIT-F (compone.zil:1053) — cross the rift once the ladder is laid."""
    if _f(gs, "LADDER-FLAG"):
        gs.c_elapsed = 33
        traverser.msg(
            "You slowly make your way across the swaying ladder. You can see "
            "sharp, pointy rocks at the bottom of the rift, far below...\n"
        )
        here = _here(traverser)
        return _by_zil_id("ADMIN-CORRIDOR" if here == "ADMIN-CORRIDOR-N" else "ADMIN-CORRIDOR-N")
    traverser.msg("The rift is too wide to jump across.")
    return None


def per_cryo_exit(traverser, exit_obj, gs):
    """CRYO-EXIT-F (comptwo.zil:1370)."""
    if _door_open("CRYO-ELEVATOR-DOOR"):
        return _by_zil_id("CRYO-ANTEROOM" if _f(gs, "CRYO-SCORE-FLAG") else "PROJCON-OFFICE")
    _door_closed(traverser)
    return None


def per_relay_exit(traverser, exit_obj, gs):
    """RELAY-EXIT-F (comptwo.zil:2531) — always refuses; message depends on state."""
    relay = _by_zil_id("RELAY")
    if relay is not None and relay.location is traverser.location:
        traverser.msg(
            "The relay is sealed. Although you cannot enter it, you could look "
            "into it."
        )
    else:
        traverser.msg("You would slice yourself to ribbons on the shattered relay.")
    return None


# ---------------------------------------------------------------------------
# PER routine registry — total over every "per" routine in the world data.
# ---------------------------------------------------------------------------

PER_HANDLERS: dict = {
    "POD-EXIT-F":             per_pod_exit,
    "SHUTTLE-EXIT-F":         per_shuttle_exit,
    "WATER-LEVEL-F":          per_water_level,
    "LONG-HALL-F":            per_long_hall,
    "LADDER-EXIT-F":          per_ladder_exit,
    "ELEVATOR-ENTER-F":       per_elevator_enter,
    "ELEVATOR-EXIT-F":        per_elevator_exit,
    "OTHER-ELEVATOR-ENTER-F": per_other_elevator_enter,
    "SHUTTLE-ENTER-F":        per_shuttle_enter,
    "CRYO-EXIT-F":            per_cryo_exit,
    "RELAY-EXIT-F":           per_relay_exit,
}


def _per_unimplemented(traverser, exit_obj, gs):
    """Fallback for any unregistered routine — refuse safely."""
    traverser.msg(CANT_GO)
    return None


def dispatch_per(traverser, exit_obj):
    """Run the exit's "per" routine and return its destination room (or None).

    None means the move was refused (the routine already messaged the player).
    """
    handler = PER_HANDLERS.get(exit_obj.db.routine, _per_unimplemented)
    return handler(traverser, exit_obj, _gs())
