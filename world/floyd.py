"""
Floyd NPC — probabilistic autonomous robot companion.

Source: compone.zil:1838-2249, comptwo.zil:1949-2030, globals.zil:1440-1468

Floyd has two interrupt routines registered in the Scheduler:
  i_floyd       — fires every turn; handles following/comments (compone.zil:2158)
  i_floyd_foray — fires every turn during Bio Lab crisis (comptwo.zil:1949)

State flags mirror ZIL globals defined in compone.zil:2124-2132.
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


class FloydNPC:
    """
    Floyd's state and autonomous behavior.

    Call `activate()` when the player first turns Floyd on; this registers
    i_floyd into the scheduler. Call `begin_foray()` when Floyd enters the
    Bio Lab to register i_floyd_foray.
    """

    def __init__(self, game_state: GameState) -> None:
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

        # current room Floyd occupies (None = not in any room)
        self.current_room: str | None = None

        self._foray_intr = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Turn Floyd on; register his every-turn interrupt."""
        if not self.reactivated:
            self.reactivated = True
        intr = self.gs.scheduler.queue(self.i_floyd, -1)
        self.gs.scheduler.enable(intr)

    def begin_foray(self, bio_lab_room: str) -> None:
        """Start Floyd's Bio Lab crisis sequence (comptwo.zil:1949)."""
        self.forayed = True
        self.foray_counter = 0
        self.current_room = bio_lab_room
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
        self.spoke = False

        player_room = self._player_room()

        if self.current_room == player_room:
            self._floyd_present(player_room)
        else:
            self._floyd_absent(player_room)

        self.follow = (self.current_room == player_room)
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
            # Dead Floyd appears if door was opened during foray
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

        # 6% chance Floyd wanders off to explore
        if self.follow and self._room_is_floydbit(room) and random.random() < 0.06:
            self._print("Floyd wanders off to explore on his own.")
            self.current_room = self._pick_adjacent_room(room)
            return

        # 40% chance of idle comment
        if not self.spoke and random.random() < 0.40:
            self.spoke = True
            self._print(f"Floyd {random.choice(FLOYDISMS)}")

    def _floyd_absent(self, player_room: str) -> None:
        """Floyd is elsewhere but was recently with the player (compone.zil:2186-2249)."""
        if not self.follow:
            return

        # 80% chance Floyd follows the player
        if random.random() < 0.80:
            self._follow_player(player_room)

    def _follow_player(self, player_room: str) -> None:
        """Move Floyd to player's room with appropriate description."""
        special_messages = {
            "BOOTH-1": "Floyd scampers in after you.",
            "BOOTH-2": "Floyd scampers in after you.",
            "BOOTH-3": "Floyd scampers in after you.",
            "BIO-LOCK": 'Floyd glides in. "Squash court?"',
            "RAD-LOCK": 'Floyd glides in. "Squash court?"',
            "MESS-HALL": '"Hey, wait for Floyd!" Floyd bounces in.',
            "ELEVATOR":  '"Hey, wait for Floyd!" Floyd bounces in.',
            "MINI-BOOTH": "Floyd squeezes in with a metallic groan.",
        }
        msg = special_messages.get(player_room, "Floyd follows you.")
        self._print(msg)
        self.current_room = player_room

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
    # Stubs — wire these up once Room/World models exist
    # ------------------------------------------------------------------

    def _player_room(self) -> str:
        raise NotImplementedError("wire up to world model")

    def _room_is_floydbit(self, room: str) -> bool:
        raise NotImplementedError("wire up to room flags")

    def _pick_adjacent_room(self, room: str) -> str:
        raise NotImplementedError("wire up to room exits")

    def _bio_lab_door_open(self) -> bool:
        raise NotImplementedError("wire up to door state")

    def _print(self, msg: str) -> None:
        raise NotImplementedError("wire up to output system")
