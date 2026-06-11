from evennia.objects.objects import DefaultCharacter

from .objects import ObjectParent


class PlanetfallNPC(ObjectParent, DefaultCharacter):
    """
    NPC typeclass. In ZIL all actors are objects with ACTORBIT, so this mirrors
    PlanetfallObject's db schema. Real behaviour lives in handler routines
    (e.g. FLOYD-F, BLATHER-F) dispatched by the action field.
    """

    def at_object_creation(self):
        super().at_object_creation()
        self.db.zil_id = None
        self.db.obj_flags = []          # includes ACTORBIT, VOWELBIT, etc.
        self.db.action = None           # handler routine name e.g. "FLOYD-F"
        self.db.size = None
        self.db.capacity = None
        self.db.value = None
        self.db.obj_text = None
        self.db.fdesc = None
        self.db.fdesc_seen = False
        self.db.synonyms = []
        self.db.adjectives = []
        self.db.is_open = False
        self.db.is_locked = False


class Character(PlanetfallNPC):
    """Player character. Inherits NPC schema; puppeted by an Account."""

    def at_post_puppet(self, **kwargs) -> None:
        from world.scheduler_script import get_or_create_game_state_script
        script = get_or_create_game_state_script()
        script.get_gs().player = self
        if not self.db.starter_items_given:
            self._give_starter_items()
            self.db.starter_items_given = True

    def at_post_unpuppet(self, account, session=None, **kwargs) -> None:
        from world.scheduler_script import get_game_state_script
        script = get_game_state_script()
        if script and script.ndb.game_state:
            script.ndb.game_state.player = None

    def _give_starter_items(self) -> None:
        """Move all starter-inventory objects (db.starter_item=True) to self."""
        from evennia.utils.search import search_object_attribute
        for obj in search_object_attribute(key="starter_item", value=True):
            if obj.location is None:
                obj.move_to(self, quiet=True)
