"""
Evennia Script that owns the Planetfall GameState singleton.

One script (key='game_state') exists per running game.  Numeric state is
written to db after every advance() call so it survives server reloads.
The Scheduler's closures are transient (ndb) and rebuilt from db at_start().
"""
from __future__ import annotations

import evennia
from evennia.scripts.scripts import DefaultScript
from evennia.utils import search as evennia_search

from world.state import GameState
from world.interrupts import (
    make_i_blather,
    make_i_ambassador,
    make_i_random_interrupts,
    make_i_sleep_warnings,
    make_i_hunger_warnings,
    make_i_sickness_warnings,
    make_i_blowup_feinstein,
)

# Maps routine name -> maker function so we can restore closures after reload.
# Order matters: register_all fires them in this order on a fresh game.
_MAKER_BY_NAME = {
    "i_blather": make_i_blather,
    "i_ambassador": make_i_ambassador,
    "i_random_interrupts": make_i_random_interrupts,
    "i_sleep_warnings": make_i_sleep_warnings,
    "i_hunger_warnings": make_i_hunger_warnings,
    "i_sickness_warnings": make_i_sickness_warnings,
    "i_blowup_feinstein": make_i_blowup_feinstein,
}

_DEFAULT_TICKS = {
    "i_blather": -1,
    "i_ambassador": -1,
    "i_random_interrupts": 1,
    "i_sleep_warnings": 3600,
    "i_hunger_warnings": 2000,
    "i_sickness_warnings": 1000,
    "i_blowup_feinstein": 100,
}


def get_game_state_script() -> "GameStateScript | None":
    """Return the singleton GameStateScript, or None if not yet created."""
    scripts = evennia_search.search_script("game_state")
    return scripts[0] if scripts else None


def get_or_create_game_state_script() -> "GameStateScript":
    """Return the singleton, creating it fresh if absent."""
    result = get_game_state_script()
    if result is None:
        result = evennia.create_script(
            "world.scheduler_script.GameStateScript", key="game_state"
        )
    return result


class GameStateScript(DefaultScript):
    """
    Persistent script holding Planetfall's clock and interrupt scheduler.

    db attributes (survive reload):
        internal_moves, day, chronometer_present, chronometer_munged,
        interrupt_states (dict: name -> {ticks_remaining, enabled, is_demon, state})

    ndb.game_state (in-process only):
        The live GameState object; rebuilt at_start() from db.
    """

    def at_script_creation(self) -> None:
        self.key = "game_state"
        self.persistent = True
        self.interval = -1   # not time-based; driven by advance()
        self.db.internal_moves = 0
        self.db.day = 1
        self.db.chronometer_present = False
        self.db.chronometer_munged = False
        self.db.interrupt_states = {}

    def at_start(self) -> None:
        """Called on every server start/reload.  Rebuild transient state from db."""
        self._rebuild()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_gs(self) -> GameState:
        if not self.ndb.game_state:
            self._rebuild()
        return self.ndb.game_state

    def advance(self, caller) -> None:
        """
        Tick the scheduler after a timed player command.
        caller is the Character who issued the command.
        """
        gs = self.get_gs()
        fatal = gs.advance_clock()
        self._persist(gs)
        if fatal:
            caller.msg(
                "\n*** Your adventure ends here.  Type 'quit' or disconnect. ***"
            )

    def set_timeless(self) -> None:
        """Mark the current action as consuming no game time (c_elapsed = 0)."""
        self.get_gs().set_timeless()

    def reset_elapsed(self) -> None:
        """Restore default tick cost (c_elapsed = 7) for the next action."""
        self.get_gs().reset_elapsed()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        """Reconstruct GameState from db; re-register all known interrupts."""
        gs = GameState()
        gs.internal_moves = self.db.internal_moves or 0
        gs.day = self.db.day or 1
        gs.chronometer_present = bool(self.db.chronometer_present)
        gs.chronometer_munged = bool(self.db.chronometer_munged)
        self.ndb.game_state = gs

        saved_states: dict = self.db.interrupt_states or {}

        if saved_states:
            for name, saved in saved_states.items():
                maker = _MAKER_BY_NAME.get(name)
                if maker is None:
                    continue
                closure = maker(gs, saved.get("state", {}))
                intr = gs.scheduler.queue(
                    closure,
                    saved.get("ticks_remaining", _DEFAULT_TICKS.get(name, 1)),
                    is_demon=saved.get("is_demon", False),
                )
                if saved.get("enabled", True):
                    gs.scheduler.enable(intr)
                else:
                    gs.scheduler.disable(intr)
        else:
            # Fresh game: register default interrupt set
            from world.interrupts import register_all
            register_all(gs)
            self._persist(gs)

    def _persist(self, gs: GameState) -> None:
        """Write serializable GameState fields back to db."""
        self.db.internal_moves = gs.internal_moves
        self.db.day = gs.day
        self.db.chronometer_present = gs.chronometer_present
        self.db.chronometer_munged = gs.chronometer_munged

        states: dict = {}
        for intr in gs.scheduler._interrupts:
            name = getattr(intr.routine, "__name__", None)
            if name is None:
                continue
            state_dict = dict(getattr(intr.routine, "_state", {}))
            states[name] = {
                "ticks_remaining": intr.ticks_remaining,
                "enabled": intr.enabled,
                "is_demon": intr.is_demon,
                "state": state_dict,
            }
        self.db.interrupt_states = states
