from evennia.objects.objects import DefaultExit

from .objects import ObjectParent


class PlanetfallExit(ObjectParent, DefaultExit):
    """
    Extends DefaultExit to handle the four ZIL exit types:

      "to"          — normal traversal (default Evennia behaviour)
      "blocked"     — always refuses with block_message
      "conditional" — `IF <door> IS OPEN` (door object's open state) or
                      `IF <global> ELSE msg` (GameState.flags); refuses if unmet
      "per"         — dispatched to a named routine via world.exit_handlers
    """

    def at_object_creation(self):
        super().at_object_creation()
        self.db.exit_type = "to"        # str: "to" | "blocked" | "conditional" | "per"
        self.db.block_message = None    # str|None: shown when blocked exit is attempted
        self.db.flag_key = None         # str|None: door zil_id or GameState flag name
        self.db.required_state = None   # str|None: "OPEN" (door) or else-message (global)
        self.db.routine = None          # str|None: handler routine name (per)

    def at_traverse(self, traversing_object, target_location, **kwargs):
        exit_type = self.db.exit_type or "to"

        if exit_type == "blocked":
            msg = self.db.block_message or "You can't go that way."
            traversing_object.msg(msg)
            return False

        if exit_type == "per":
            # A "per" routine computes the real destination (the static one is a
            # placeholder). It may set gs.c_elapsed for timed transit; the move
            # then advances the clock by that amount, mirroring ZIL.
            from world.exit_handlers import dispatch_per
            from world.scheduler_script import get_or_create_game_state_script
            script = get_or_create_game_state_script()
            script.reset_elapsed()
            dest = dispatch_per(traversing_object, self)
            if dest is None:
                return False
            traversing_object.move_to(dest, move_type="traverse")
            script.advance(traversing_object)
            return False

        if exit_type == "conditional":
            if not self._conditional_open(traversing_object):
                return False

        moved = super().at_traverse(traversing_object, target_location, **kwargs)
        # ZIL: every successful GO consumes a turn (default C-ELAPSED), so the
        # clock and its interrupts advance as the player walks the ship.
        if moved is not False and traversing_object.location is target_location:
            from world.scheduler_script import get_or_create_game_state_script
            script = get_or_create_game_state_script()
            script.reset_elapsed()
            script.advance(traversing_object)
        return moved

    def _conditional_open(self, traversing_object) -> bool:
        """Return True if a conditional exit may be traversed; else message + False.

        Two ZIL flavours:
          IF <door> IS OPEN   — flag_key is a door object's zil_id; pass when its
                                open state matches required_state ("OPEN").
          IF <global> ELSE m  — flag_key is a GameState flag; pass when truthy,
                                else print required_state (the else-message).
        """
        flag_key = self.db.flag_key
        door = self._by_zil_id(flag_key) if flag_key else None

        if door is not None:
            want_open = (self.db.required_state or "OPEN") == "OPEN"
            if bool(door.db.is_open) == want_open:
                return True
            traversing_object.msg(f"The {door.key} is closed.")
            return False

        # Global-flag flavour (e.g. NO-MICROBE).
        from world.scheduler_script import get_game_state_script
        script = get_game_state_script()
        gs = script.get_gs() if script else None
        if gs and gs.flags.get(flag_key, False):
            return True
        traversing_object.msg(self.db.required_state or "You can't go that way.")
        return False

    @staticmethod
    def _by_zil_id(zil_id):
        from evennia.utils.search import search_object_attribute
        results = search_object_attribute(key="zil_id", value=zil_id)
        return results[0] if results else None


# Keep old name available.
Exit = PlanetfallExit
