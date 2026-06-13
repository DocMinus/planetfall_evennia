"""
Floyd NPC — probabilistic autonomous robot companion.

Source: compone.zil:1838-2249, comptwo.zil:1949-2030, globals.zil:1440-1468

Floyd has two interrupt routines registered in the Scheduler:
  i_floyd       — fires every turn; handles following/comments (compone.zil:2158)
  i_floyd_foray — fires every turn during Bio Lab crisis (comptwo.zil:1949)

State flags mirror ZIL globals defined in compone.zil:2124-2132.

Floyd's Evennia object (PlanetfallObject with db.zil_id="FLOYD") is created by
the world loader.  FloydNPC wraps it and drives its movement.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from world.state import GameState

# compone.zil:2316-2338 — 20 random utterances Floyd makes when idle in room
FLOYDISMS = [
    "paces impatiently.",
    "absent-mindedly recites the first six hundred digits of pi.",
    "lowers his voice and tells you the latest rumors about Dr. Fizpick.",
    "makes a beeping sound.",
    "twiddles his fingers.",
    "hums a few bars of a popular tune.",
    "examines his reflection in a shiny surface.",
    "counts ceiling tiles.",
    "practices his juggling (unsuccessfully).",
    "mumbles something about servo calibration.",
    "performs a brief and inexplicable dance.",
    "sighs in a way that seems almost human.",
    "invents a new word and immediately forgets it.",
    "wonders aloud if robots dream.",
    "pretends to sneeze and then looks embarrassed.",
    "offers you a high-five and then retracts it.",
    "recites a limerick about deuterium.",
    "lists his top ten favorite subroutines.",
    "asks if you want to hear a joke, then forgets the punchline.",
    "watches you with his large, friendly eyes.",
]

# The ZIL id of the eastern Bio Lab door — gate for Floyd's foray resolution.
_BIO_DOOR_ZIL_ID = "BIO-DOOR-EAST"

# Tight rooms Floyd always squeezes into even when his normal follow check
# fails (ZIL I-FLOYD absent branch, compone.zil:2206-2249). Without this he
# can't ride the elevators or make the bio-lock card foray.
_FLOYD_ALWAYS_FOLLOW = frozenset({
    "BOOTH-1", "BOOTH-2", "BOOTH-3",
    "BIO-LOCK-EAST", "BIO-LOCK-WEST",
    "RADIATION-LOCK-EAST", "RADIATION-LOCK-WEST",
    "ALFIE-CONTROL-EAST", "ALFIE-CONTROL-WEST",
    "BETTY-CONTROL-EAST", "BETTY-CONTROL-WEST",
    "UPPER-ELEVATOR", "LOWER-ELEVATOR", "REACTOR-ELEVATOR",
    "MINI-BOOTH",
})


def _by_zil_id(zil_id: str):
    """Return the first Evennia object whose db.zil_id matches, or None."""
    from evennia.utils.search import search_object_attribute
    results = search_object_attribute(key="zil_id", value=zil_id)
    return results[0] if results else None


class FloydNPC:
    """
    Floyd's state and autonomous behavior.

    Call `activate()` when the player first turns Floyd on; this syncs his
    starting room and registers i_floyd into the scheduler.
    Call `begin_foray()` when Floyd enters the Bio Lab.
    """

    def __init__(self, game_state: "GameState") -> None:
        self.gs = game_state

        # compone.zil:2124-2132
        self.score_flag: bool = False
        self.spoke: bool = False        # prevents multiple outputs same turn
        self.follow: bool = False       # was Floyd in player's previous room?
        self.reactivated: bool = False  # has been turned on before
        self.introduced: bool = False   # first-contact done

        # comptwo.zil:1939-1945
        self.waiting: bool = False
        self.gave_up: bool = False
        self.forayed: bool = False
        self.foray_counter: int = 0

        # globals.zil:1438
        self.card_revealed: bool = False

        # current room Floyd occupies (ZIL id string; None = not in any room)
        self.current_room: str | None = None

        self._foray_intr = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Turn Floyd on; sync his position and register his every-turn interrupt."""
        if not self.reactivated:
            self.reactivated = True
        self._sync_current_room()
        intr = self.gs.scheduler.queue(self.i_floyd, -1)
        self.gs.scheduler.enable(intr)

    def begin_foray(self, bio_lab_room: str) -> None:
        """Start Floyd's Bio Lab crisis sequence (comptwo.zil:1949)."""
        self.forayed = True
        self.foray_counter = 0
        self.current_room = bio_lab_room
        self._move_floyd_obj(bio_lab_room)
        self._foray_intr = self.gs.scheduler.queue(self.i_floyd_foray, -1)
        self.gs.scheduler.enable(self._foray_intr)

    # ------------------------------------------------------------------
    # Interrupt routines
    # ------------------------------------------------------------------

    def i_floyd(self) -> bool:
        """
        Every-turn behavior. Mirrors I-FLOYD (compone.zil:2158-2249).
        Returns False always (never fatal).
        """
        if self.gs.player is None:
            return False

        self.spoke = False
        player_room = self._player_room()
        if not player_room:
            return False

        if self.current_room == player_room:
            self._floyd_present(player_room)
        else:
            self._floyd_absent(player_room)

        # NB: self.follow is managed inside _floyd_present / _floyd_absent (ZIL
        # FLOYD-FOLLOW). It must NOT be re-latched to presence here — doing so
        # permanently strands Floyd after a single missed follow.
        return False

    def i_floyd_foray(self) -> bool:
        """
        Bio Lab crisis sequence. Mirrors I-FLOYD-FORAY (comptwo.zil:1949-2030).
        Returns False always.
        """
        self.foray_counter += 1

        if self.foray_counter == 2:
            self._print("You hear growling sounds and the clash of metal from the Bio Lab.")
        elif self.foray_counter == 3:
            self._print("Three sharp knocks ring out, followed by the sound of tearing metal.")
        elif self.foray_counter == 4:
            self._resolve_foray()
        elif self.foray_counter == 5:
            self._print("Floyd lies motionless in the corridor, clutching the mini-booth card.")
            if self._foray_intr:
                self.gs.scheduler.disable(self._foray_intr)

        return False

    def floyd_reveal_card(self) -> bool:
        """
        Probabilistic card reveal, gated on day/moves. Mirrors FLOYD-REVEAL-CARD-F
        (globals.zil:1440-1468).
        Call each turn Floyd is in the player's room (after Day 2 begins).
        Returns True if card was revealed.
        """
        if self.card_revealed:
            return False

        day = self.gs.day
        moves = self.gs.internal_moves
        threshold = 2000  # rough mid-day-2 boundary in ticks

        if day == 2 and moves < threshold:
            chance = 0.05
        elif day == 2:
            chance = 0.10
        elif day == 3 and moves < threshold:
            chance = 0.20
        elif day == 3:
            chance = 0.40
        else:  # day 4+
            chance = 1.0

        if random.random() < chance:
            self.card_revealed = True
            self._print('Floyd rummages through his chest compartment. "Here, Floyd found this. Maybe useful!"')
            self._print("Floyd hands you the Lower Elevator Card.")
            return True

        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _floyd_present(self, room: str) -> None:
        """Floyd is in the same room as the player (compone.zil:2164-2185)."""
        if not self.introduced:
            self.introduced = True
            self._print('A small robot with large, friendly eyes rolls up to you. "Hi! Floyd\'s name is Floyd!"')
            return

        # 6% chance Floyd wanders off to explore (ZIL PROB 6 + FLOYDBIT room).
        if self.follow and self._room_is_floydbit(room) and random.random() < 0.06:
            self._print("Floyd says \"Floyd going exploring. See you later.\" "
                        "He glides out of the room.")
            self.follow = False
            new_room = self._pick_adjacent_room(room)
            self.current_room = new_room
            self._move_floyd_obj(new_room)
            return

        # Present and staying: he's following you (ZIL SETG FLOYD-FOLLOW T).
        self.follow = True

        # 40% chance of idle comment
        if not self.spoke and random.random() < 0.40:
            self.spoke = True
            self._print(f"Floyd {random.choice(FLOYDISMS)}")

    def _floyd_absent(self, player_room: str) -> None:
        """Floyd is elsewhere but was recently with the player (compone.zil:2186-2249).

        Faithful to I-FLOYD's "active but not present" branch: an 80% normal
        follow; otherwise he still reliably squeezes into the tight special
        rooms (elevators, booths, air-locks, control cabins) and otherwise has
        a ~30% chance to catch up — so a single missed follow never strands him.
        """
        # Normal follow.
        if self.follow and random.random() < 0.80:
            self._follow_player(player_room)
            return

        # He lost the trail this turn (ZIL SETG FLOYD-FOLLOW <>), but...
        self.follow = False
        if player_room in _FLOYD_ALWAYS_FOLLOW:
            # Bio-lock is refused once he's given up on the foray.
            if player_room in ("BIO-LOCK-EAST", "BIO-LOCK-WEST") and self.gave_up:
                return
            self._follow_player(player_room)
        elif random.random() < 0.30:
            self._follow_player(player_room)

    def _follow_player(self, player_room: str) -> None:
        """Move Floyd to player's room with appropriate description."""
        special_messages = {
            "BOOTH-1": '"Oooo, this is a tiny room," Floyd remarks, scampering in.',
            "BOOTH-2": '"Oooo, this is a tiny room," Floyd remarks, scampering in.',
            "BOOTH-3": '"Oooo, this is a tiny room," Floyd remarks, scampering in.',
            "BIO-LOCK-WEST": 'Floyd glides after you. "Is this...is this a squash court?" he asks.',
            "BIO-LOCK-EAST": 'Floyd glides after you. "Is this...is this a squash court?" he asks.',
            "RADIATION-LOCK-WEST": 'Floyd glides after you. "Is this...is this a squash court?" he asks.',
            "RADIATION-LOCK-EAST": 'Floyd glides after you. "Is this...is this a squash court?" he asks.',
            "MESS-HALL": '"Hey, wait for Floyd!" Floyd bounces into the room, smiling broadly.',
            "UPPER-ELEVATOR": '"Hey, wait for Floyd!" Floyd bounces into the elevator, smiling broadly.',
            "LOWER-ELEVATOR": '"Hey, wait for Floyd!" Floyd bounces into the elevator, smiling broadly.',
            "REACTOR-ELEVATOR": '"Hey, wait for Floyd!" Floyd bounces into the elevator, smiling broadly.',
            "ALFIE-CONTROL-EAST": '"Hey, wait for Floyd!" Floyd bounces into the cabin, smiling broadly.',
            "ALFIE-CONTROL-WEST": '"Hey, wait for Floyd!" Floyd bounces into the cabin, smiling broadly.',
            "BETTY-CONTROL-EAST": '"Hey, wait for Floyd!" Floyd bounces into the cabin, smiling broadly.',
            "BETTY-CONTROL-WEST": '"Hey, wait for Floyd!" Floyd bounces into the cabin, smiling broadly.',
            "MINI-BOOTH": '"Hi," whispers Floyd, tiptoeing in. "Are we going to '
                          'teleport into the computer like Achilles always used to do?"',
        }
        msg = special_messages.get(player_room, "Floyd follows you.")
        self._print(msg)
        self.current_room = player_room
        self._move_floyd_obj(player_room)

    def _resolve_foray(self) -> None:
        """End of foray — Floyd exits Bio Lab (comptwo.zil:2010-2030)."""
        if self._bio_lab_door_open():
            self._print("Floyd stumbles out clutching a small card. He looks badly damaged.")
            self.card_revealed = True
        else:
            self._print("Floyd is trapped inside. A terrible silence follows.")
            self.forayed = True
            self.gave_up = True

    # ------------------------------------------------------------------
    # Evennia I/O — wired to world model
    # ------------------------------------------------------------------

    def _player_room(self) -> str:
        """Return the ZIL id of the room the player is currently in."""
        player = self.gs.player
        if player is None or player.location is None:
            return ""
        return player.location.db.zil_id or ""

    def _room_is_floydbit(self, room_id: str) -> bool:
        """Return True if the room has the FLOYDBIT flag (Floyd can wander there)."""
        room = _by_zil_id(room_id)
        if room is None:
            return False
        return "FLOYDBIT" in (room.db.room_flags or [])

    def _pick_adjacent_room(self, room_id: str) -> str:
        """Return a random adjacent room's ZIL id (stay put if no exits)."""
        room = _by_zil_id(room_id)
        if room is None:
            return room_id
        destinations = [
            e.destination.db.zil_id
            for e in room.exits
            if e.destination and e.destination.db.zil_id
        ]
        return random.choice(destinations) if destinations else room_id

    def _bio_lab_door_open(self) -> bool:
        """Return True if the eastern Bio Lab door is open."""
        door = _by_zil_id(_BIO_DOOR_ZIL_ID)
        return bool(door and door.db.is_open)

    def _print(self, msg: str) -> None:
        """Send a message to the player."""
        if self.gs.player:
            self.gs.player.msg(msg)

    # ------------------------------------------------------------------
    # Floyd Evennia object helpers
    # ------------------------------------------------------------------

    def _get_floyd_obj(self):
        """Return Floyd's Evennia object, or None if not yet in the DB."""
        return _by_zil_id("FLOYD")

    def _sync_current_room(self) -> None:
        """Initialise current_room from Floyd's actual Evennia object location."""
        obj = self._get_floyd_obj()
        if obj and obj.location:
            self.current_room = obj.location.db.zil_id or None

    def _move_floyd_obj(self, room_zil_id: str) -> None:
        """Physically move Floyd's Evennia object to the named room."""
        obj = self._get_floyd_obj()
        if obj is None:
            return
        room = _by_zil_id(room_zil_id)
        if room is not None:
            obj.location = room
