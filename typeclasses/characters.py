from evennia.objects.objects import DefaultCharacter

from .objects import ObjectParent


class PlanetfallNPC(ObjectParent, DefaultCharacter):
    """
    NPC typeclass. In ZIL all actors are objects with ACTORBIT, so this mirrors
    PlanetfallObject's db schema. Real behaviour lives in handler routines
    (e.g. FLOYD-F, BLATHER-F) dispatched by the action field.
    """

    def at_look(self, target, **kwargs):
        """Render a room view the ZIL way.

        Evennia calls at_look() for the automatic look on movement and on
        connect. For rooms we delegate to build_look() so those views match the
        explicit `look` command (dynamic ACTION descriptions, NDESCBIT objects
        such as the ADVENTURER hidden, ZIL-style exit line) instead of
        Evennia's default appearance. Non-room targets keep default behaviour.
        """
        from typeclasses.rooms import PlanetfallRoom
        if isinstance(target, PlanetfallRoom):
            from world.verb_commands import build_look
            return build_look(self, target)
        return super().at_look(target, **kwargs)

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
        # Set gs.player and hand out starter items first, then let the default
        # puppet flow run — it shows the on-connect room look via at_look(),
        # which is now ZIL-rendered.
        super().at_post_puppet(**kwargs)

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
