"""
Game state and clock/interrupt system.

Mirrors ZIL architecture:
  INTERNAL-MOVES  -> GameState.internal_moves
  C-ELAPSED       -> GameState.c_elapsed
  C-TABLE/CLOCKER -> Scheduler
  QUEUE/ENABLE    -> Scheduler.queue / Scheduler.enable
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Interrupt:
    routine: Callable[[], bool]
    ticks_remaining: int   # -1 = fire every turn (ZIL convention)
    enabled: bool = True
    is_demon: bool = False  # survives game-end (ZIL "demon" slots)

    @property
    def fires_every_turn(self) -> bool:
        return self.ticks_remaining == -1


class Scheduler:
    """Mirrors C-TABLE + CLOCKER + QUEUE in misc.zil:415-480."""

    def __init__(self) -> None:
        self._interrupts: list[Interrupt] = []

    def queue(self, routine: Callable[[], bool], ticks: int, is_demon: bool = False) -> Interrupt:
        """Register an interrupt to fire in `ticks` ticks (-1 = every turn)."""
        intr = Interrupt(routine=routine, ticks_remaining=ticks, is_demon=is_demon)
        self._interrupts.append(intr)
        return intr

    def enable(self, intr: Interrupt) -> None:
        intr.enabled = True

    def disable(self, intr: Interrupt) -> None:
        intr.enabled = False

    def remove(self, routine: Callable[[], bool]) -> None:
        self._interrupts = [i for i in self._interrupts if i.routine is not routine]

    def tick(self, elapsed: int) -> bool:
        """
        Decrement counters and fire due interrupts. Mirrors CLOCKER (misc.zil:459-480).
        Returns True if any interrupt signalled a fatal condition.
        """
        fatal = False
        for intr in list(self._interrupts):
            if not intr.enabled:
                continue
            if intr.fires_every_turn:
                if intr.routine():
                    fatal = True
            else:
                intr.ticks_remaining -= elapsed
                if intr.ticks_remaining <= 0:
                    if intr.routine():
                        fatal = True
                    self._interrupts.remove(intr)
        return fatal


class GameState:
    """
    Central mutable game state. Owns the scheduler and the clock.

    Time model (misc.zil:114-261):
      - internal_moves: cumulative ticks, never shown directly
      - moves_display:  shown to player; adjusted when chronometer is present/working
      - c_elapsed:      ticks this action costs; set to 0 for timeless verbs
                        (SAVE, RESTORE, SCORE, VERSION, TELL, etc.)
    """

    def __init__(self) -> None:
        self.internal_moves: int = 0
        self.day: int = 1
        self.score: int = 0            # SCORE global (verbs.zil:212); max 80
        self.c_elapsed: int = 7        # default ticks per action
        self.moves_display: int = 0    # shown to player
        self.chronometer_present: bool = False
        self.chronometer_munged: bool = False
        self.scheduler: Scheduler = Scheduler()
        self.player = None             # set to Character on login; used by interrupts for output

        # Named game flags (ZIL globals). Door-open conditional exits read the
        # door object directly; this dict holds non-object globals such as
        # NO-MICROBE (comptwo.zil:2474, starts T). See world/exit_handlers.py.
        self.flags: dict = {"NO-MICROBE": True}

    def advance_clock(self) -> bool:
        """
        Called after each player action. Mirrors the post-action block in
        MAIN-LOOP (misc.zil:248-261).
        Returns True if a fatal interrupt fired.
        """
        if self.c_elapsed == 0:
            return False

        self.internal_moves += self.c_elapsed
        self._update_display()
        return self.scheduler.tick(self.c_elapsed)

    def add_score(self, points: int, reason: str | None = None) -> None:
        """Award `points` to the running score (verbs.zil SETG SCORE)."""
        self.score += points

    def score_object(self, obj) -> int:
        """ZIL SCORE-OBJ (verbs.zil:216): award an object's/room's VALUE once.

        Adds db.value to the score, then zeroes it so re-taking or re-entering
        does not score again. Returns the points awarded (0 if none).
        """
        val = obj.db.value or 0
        if val > 0:
            self.score += val
            obj.db.value = 0
        return val

    MAX_SCORE = 80

    @staticmethod
    def rank_for(score: int) -> str:
        """Map a score to its ZIL rank title (verbs.zil:242-251)."""
        if score >= 80:
            return "Galactic Overlord"
        if score > 72:
            return "Cluster Admiral"
        if score > 64:
            return "System Captain"
        if score > 48:
            return "Planetary Commodore"
        if score > 36:
            return "Lieutenant"
        if score > 24:
            return "Ensign First Class"
        if score > 12:
            return "Space Cadet"
        return "Beginner"

    def set_timeless(self) -> None:
        """Mark current action as consuming no time (c_elapsed = 0)."""
        self.c_elapsed = 0

    def reset_elapsed(self) -> None:
        """Restore default tick cost for next action."""
        self.c_elapsed = 7

    def _update_display(self) -> None:
        if self.chronometer_present and not self.chronometer_munged:
            self.moves_display = self.internal_moves
        else:
            self.moves_display = self.internal_moves
