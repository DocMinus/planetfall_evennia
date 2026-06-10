from evennia.objects.objects import DefaultRoom

from .objects import ObjectParent


class PlanetfallRoom(ObjectParent, DefaultRoom):
    """
    Extends DefaultRoom with ZIL-sourced fields stored as Evennia db attributes.
    All fields survive server restarts via the Attribute system.
    """

    def at_object_creation(self):
        super().at_object_creation()
        self.db.zil_id = None           # str: original ZIL room id e.g. "BRIG"
        self.db.room_flags = []         # list[str]: RLANDBIT, ONBIT, FLOYDBIT, etc.
        self.db.globals = []            # list[str]: local-global object ids visible here
        self.db.pseudo = {}             # dict[str, str]: word → handler object id
        self.db.action = None           # str|None: room handler routine name
        self.db.first_visit = True      # bool: False after player first enters

    def on_enter(self, character):
        """Called when any character enters this room. Stub — wired in Phase 3."""
        pass

    def at_object_receive(self, moved_obj, source_location, move_type="move", **kwargs):
        super().at_object_receive(moved_obj, source_location, move_type=move_type, **kwargs)
        if moved_obj.has_account:
            self.on_enter(moved_obj)


# Keep old name available so Evennia's default settings still resolve if referenced.
Room = PlanetfallRoom
