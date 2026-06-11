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
        # ZIL: every GO attempt — success OR failure — costs a turn and ticks
        # the clock/interrupts. The main loop seeds C-ELAPSED with the default
        # (7) each turn and only overrides it on a *successful* move; the failed
        # branches of V-WALK return via RFATAL without zeroing it, and
        # INTERNAL-MOVES += C-ELAPSED / CLOCKER still run (misc.zil MAIN-LOOP).
        # So we advance the clock on refusal as well as on a completed move.
        exit_type = self.db.exit_type or "to"
        from world.scheduler_script import get_or_create_game_state_script
        script = get_or_create_game_state_script()

        if exit_type == "blocked":
            traversing_object.msg(self.db.block_message or "You can't go that way.")
            script.reset_elapsed()
            script.advance(traversing_object)
            return False

        if exit_type == "per":
            # A "per" routine computes the real destination (the static one is a
            # placeholder) and may set gs.c_elapsed for timed transit. Resetting
            # first means a refusal still ticks the default turn cost.
            from world.exit_handlers import dispatch_per
            script.reset_elapsed()
            dest = dispatch_per(traversing_object, self)
            if dest is not None:
                traversing_object.move_to(dest, move_type="traverse")
            script.advance(traversing_object)
            return False

        if exit_type == "conditional":
            if not self._conditional_open(traversing_object):
                script.reset_elapsed()
                script.advance(traversing_object)
                return False

        moved = super().at_traverse(traversing_object, target_location, **kwargs)
        if moved is not False and traversing_object.location is target_location:
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
