from evennia.objects.objects import DefaultObject


class ObjectParent:
    """
    Mixin for all entities that have a location (Objects, Exits, Characters, Rooms).
    Add any method from DefaultObject here to override it across all derived types.
    """


class PlanetfallObject(ObjectParent, DefaultObject):
    """
    Extends DefaultObject with ZIL-sourced fields.

    Flags of note:
      TAKEBIT   — player can pick it up
      CONTBIT   — container
      DOORBIT   — acts as a door (open/close/lock state matters)
      NDESCBIT  — not listed in room contents
      OPENBIT   — starts open
      LOCKBIT   — starts locked
      VOWELBIT  — use "an" instead of "a" in messages
    """

    def at_object_creation(self):
        super().at_object_creation()
        self.db.zil_id = None           # str: original ZIL object id
        self.db.obj_flags = []          # list[str]: TAKEBIT, CONTBIT, DOORBIT, etc.
        self.db.action = None           # str|None: handler routine name
        self.db.size = None             # int|None: encumbrance units
        self.db.capacity = None         # int|None: container capacity
        self.db.value = None            # int|None: score value
        self.db.obj_text = None         # str|None: text revealed by READ
        self.db.fdesc = None            # str|None: first-time description
        self.db.fdesc_seen = False      # bool: True after fdesc has fired once
        self.db.synonyms = []           # list[str]: ZIL synonym words
        self.db.adjectives = []         # list[str]: ZIL adjective words
        self.db.is_open = False         # bool: current open/closed state
        self.db.is_locked = False       # bool: current lock state

    def get_display_desc(self, looker, **kwargs):
        """Return fdesc on first look, ldesc thereafter."""
        if self.db.fdesc and not self.db.fdesc_seen:
            self.db.fdesc_seen = True
            return self.db.fdesc
        return super().get_display_desc(looker, **kwargs)


# Keep old name available.
Object = PlanetfallObject
