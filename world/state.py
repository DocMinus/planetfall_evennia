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
        self.dead: bool = False        # ZIL FINISH state — game over via JIGS-UP
        self.death_reason: str | None = None

        # Named game flags (ZIL globals). Door-open conditional exits read the
        # door object directly; this dict holds non-object globals such as
        # NO-MICROBE (comptwo.zil:2474, starts T). See world/exit_handlers.py.
        #
        # Transport globals (Phase 11) seeded at their ZIL defaults — the PER
        # exit routines read these to compute dynamic destinations. The deeper
        # subsystems that *set* them (shuttle driving, pod-sink opening) are
        # later work; the traversal layer is faithful given these values.
        self.flags: dict = {
            "NO-MICROBE": True,             # comptwo.zil:2474
            "UPPER-ELEVATOR-UP": True,      # compone.zil:2599  <GLOBAL ... T>
            "LOWER-ELEVATOR-UP": False,     # compone.zil:2597  <GLOBAL ... <>>
            "ALFIE-AT-KALAMONTEE": True,    # globals.zil       <GLOBAL ... T>
            "BETTY-AT-KALAMONTEE": False,   # globals.zil       <GLOBAL ... <>>
            # Shuttle driving sim (globals.zil) — Subsystem 4.
            "SHUTTLE-MOVING": False,
            "SHUTTLE-VELOCITY": 0,
            "SHUTTLE-COUNTER": 0,
            "LEVER-SETTING": 0,             # 0 centre, 1 up (+), -1 down (-)
            "ALFIE-BROKEN": False,
            "BETTY-BROKEN": False,
            "BLOWUP-COUNTER": 0,            # globals.zil:1160
            "TRIP-COUNTER": 0,              # globals.zil:1270 — pod descent stage
            "SINK-COUNTER": 0,              # globals.zil — pod sinking stage
            "BRIGS-UP": 0,                  # Blather brig-drag counter
            "IN-WEB": False,                # ADVENTURER in SAFETY-WEB (vehicle-as-flag)
            "IN-BED": False,                # ADVENTURER in a BED (vehicle-as-flag)
            "LADDER-FLAG": False,           # ladder spans the rift (compone.zil:647)
            "LADDER-EXTENDED": False,        # ladder extended to full length
            "PADLOCK-REMOVED": False,        # padlock taken off the brig door
            # Card-access enable flags (globals.zil SLOT-F) — set by sliding the
            # matching card, cleared by an I-TURNOFF-* interrupt after a delay.
            "UPPER-ELEVATOR-ON": False,
            "LOWER-ELEVATOR-ON": False,
            "TELEPORTATION-ON": False,
            "MINI-ACTIVATED": False,
            "SHUTTLE-ON": False,
            "CARD-REVEALED": False,          # Floyd has shown his card
            "CARD-STOLEN": False,            # his card has been taken
            "ELEVATOR-IN-TRANSIT": False,    # an elevator car is moving
            # Survival levels (globals.zil) — shared by the warning interrupts,
            # the cures (eat/sleep/medicine), and V-DIAGNOSE.
            "SLEEPY-LEVEL": 0,
            "HUNGER-LEVEL": 0,
            "SICKNESS-LEVEL": 0,
            "SICKNESS-WARNING-FLAG": False,
            "FLOYD-INTRODUCED": False,
            # Reactor repair (compone/comptwo.zil) — Subsystem 5 win flags.
            "COMM-FIXED": False,
            "DEFENSE-FIXED": False,
            "COURSE-CONTROL-FIXED": False,
            "COMPUTER-FIXED": False,
            "ACCESS-PANEL-FULL": True,       # the bad board starts in the panel
            "ITS-CRACKED": False,
            "COMM-SHUTDOWN": False,
            "HOLE-TRIP-FLAG": False,         # Floyd has been through the robot hole
            "MURAL-FLAG": False,             # Floyd has commented on the projcon mural
            "CRYO-SCORE-FLAG": False,       # comptwo.zil
        }

    def advance_clock(self) -> bool:
        """
        Called after each player action. Mirrors the post-action block in
        MAIN-LOOP (misc.zil:248-261).
        Returns True if a fatal interrupt fired.
        """
        # While the shuttle is rolling, a turn costs 600/velocity ticks
        # (misc.zil:240) — faster car, less game time per move.
        vel = self.flags.get("SHUTTLE-VELOCITY", 0)
        if vel > 0:
            self.c_elapsed = 600 // vel

        if self.c_elapsed == 0:
            return False

        self.internal_moves += self.c_elapsed
        self._update_display()
        return self.scheduler.tick(self.c_elapsed)

    def die(self, reason: str = "") -> bool:
        """ZIL JIGS-UP (verbs.zil:364) — the uniform death path.

        Prints the death cause, the "**** You have died ****" banner, and the
        end-of-game score summary, then marks the game over (`dead`). Mirrors
        JIGS-UP's `<TELL .DESC>` + banner + `<FINISH T>`. Returns True so it
        composes with the fatal bool the clock/scheduler already propagate —
        a fatal interrupt or object handler just `return gs.die(cause)`.
        """
        self.dead = True
        self.death_reason = reason or None
        if self.player:
            self.player.msg(self._death_banner(reason))
        return True

    def victory(self, text: str) -> bool:
        """ZIL FINISH (a winning end) — print the ending, then end the game.

        Like die() it sets `dead` (the game is over and only RESTORE/QUIT work),
        but the banner celebrates rather than mourns.
        """
        self.dead = True
        self.death_reason = None
        if self.player:
            banner = (
                f"{text}\n\n    ****  You have won  ****\n\n"
                f"In that game you scored {self.score} out of a possible "
                f"{self.MAX_SCORE} points, in {self.day} "
                f"day{'s' if self.day != 1 else ''}, giving you the rank of "
                f"{self.rank_for(self.score)}.\n\n"
                "Would you like to RESTORE a saved game, or QUIT?"
            )
            self.player.msg(banner)
        return True

    def _death_banner(self, reason: str) -> str:
        lines = []
        if reason:
            lines.append(reason)
        lines.append("\n    ****  You have died  ****\n")
        lines.append(
            f"In that game you scored {self.score} out of a possible "
            f"{self.MAX_SCORE} points, in {self.day} "
            f"day{'s' if self.day != 1 else ''}, giving you the rank of "
            f"{self.rank_for(self.score)}."
        )
        lines.append("\nWould you like to RESTORE a saved game, or QUIT?")
        return "\n".join(lines)

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
