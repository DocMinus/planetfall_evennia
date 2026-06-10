from evennia.objects.objects import DefaultExit

from .objects import ObjectParent


class PlanetfallExit(ObjectParent, DefaultExit):
    """
    Extends DefaultExit to handle the four ZIL exit types:

      "to"          — normal traversal (default Evennia behaviour)
      "blocked"     — always refuses with block_message
      "conditional" — checks GameState flag; passes through until Phase 3 wires it
      "per"         — dispatched to a named handler routine (stub until Phase 5)
    """

    def at_object_creation(self):
        super().at_object_creation()
        self.db.exit_type = "to"        # str: "to" | "blocked" | "conditional" | "per"
        self.db.block_message = None    # str|None: shown when blocked exit is attempted
        self.db.flag_key = None         # str|None: GameState flag name (conditional)
        self.db.required_state = None   # str|None: expected flag value e.g. "OPEN"
        self.db.routine = None          # str|None: handler routine name (per)

    def at_traverse(self, traversing_object, target_location, **kwargs):
        exit_type = self.db.exit_type or "to"

        if exit_type == "blocked":
            msg = self.db.block_message or "You can't go that way."
            traversing_object.msg(msg)
            return False

        if exit_type == "conditional":
            # TODO Phase 3: check GAME_STATE.flags[self.db.flag_key] == self.db.required_state
            pass  # pass through until GameState is wired

        if exit_type == "per":
            # TODO Phase 5: dispatch to handler registry[self.db.routine]
            pass  # pass through until command system is wired

        return super().at_traverse(traversing_object, target_location, **kwargs)


# Keep old name available.
Exit = PlanetfallExit
