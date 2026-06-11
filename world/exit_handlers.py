"""
Phase 7 — "per" exit routine registry.

ZIL supports three kinds of conditional movement:

  IF <door> IS OPEN   → handled directly in PlanetfallExit.at_traverse by
                        reading the door object's open state.
  IF <global> ELSE m  → handled in at_traverse by reading GameState.flags.
  PER <routine>       → a named ZIL routine computes the destination (or refuses)
                        — dispatched here via PER_HANDLERS.

The PER routines are the ship's transport puzzles: elevators that must be called
and have their destination set, shuttle cars, the underwater descent, etc. Their
real logic depends on puzzle/transport state that is built in Phase 11, so for
now each unimplemented routine refuses with a neutral message rather than the old
behaviour of silently passing through to a placeholder destination.

A handler has signature ``handler(traverser, exit_obj) -> bool`` and returns the
value ``at_traverse`` should return: True to allow Evennia's default move to
``exit_obj.destination``, False to abort (the handler having already messaged the
player).
"""
from __future__ import annotations


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


def _per_unimplemented(traverser, exit_obj) -> bool:
    """Default for transport routines whose puzzle logic lands in Phase 11."""
    traverser.msg("You can't go that way right now.")
    return False


# ---------------------------------------------------------------------------
# PER routine registry
#
# Every routine referenced by a "per" exit in the world data is listed here so
# dispatch is total. Phase 11 replaces individual entries with real transport
# logic; until then they share the neutral refusal above.
# ---------------------------------------------------------------------------

PER_HANDLERS: dict = {
    "POD-EXIT-F":             _per_unimplemented,
    "SHUTTLE-EXIT-F":         _per_unimplemented,
    "WATER-LEVEL-F":          _per_unimplemented,
    "LONG-HALL-F":            _per_unimplemented,
    "LADDER-EXIT-F":          _per_unimplemented,
    "ELEVATOR-ENTER-F":       _per_unimplemented,
    "ELEVATOR-EXIT-F":        _per_unimplemented,
    "OTHER-ELEVATOR-ENTER-F": _per_unimplemented,
    "SHUTTLE-ENTER-F":        _per_unimplemented,
    "CRYO-EXIT-F":            _per_unimplemented,
    "RELAY-EXIT-F":           _per_unimplemented,
}


def dispatch_per(traverser, exit_obj) -> bool:
    """Run the exit's "per" routine; return what at_traverse should return.

    Unregistered routines refuse safely rather than passing through.
    """
    handler = PER_HANDLERS.get(exit_obj.db.routine, _per_unimplemented)
    return handler(traverser, exit_obj)
