"""
Evennia Script that owns the Planetfall GameState singleton.

One script (key='game_state') exists per running game.  Numeric state is
written to db after every advance() call so it survives server reloads.
The Scheduler's closures and FloydNPC instance are transient (ndb) and
rebuilt from db at_start().
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
    make_i_pod_trip,
    make_i_sink_pod,
    make_i_fall_asleep,
    make_i_upper_elevator_arrive,
    make_i_lower_elevator_arrive,
    make_i_upper_elevator_trip,
    make_i_lower_elevator_trip,
    make_i_turnoff_upper_elevator,
    make_i_turnoff_lower_elevator,
    make_i_turnoff_teleportation,
    make_i_turnoff_mini,
    make_i_turnoff_shuttle,
    make_i_kitchen_door_closes,
    make_i_shuttle,
    make_i_magnet,
    make_i_clear_floyd_peer,
    make_i_cryo_elevator_arrive,
    make_i_floyd_foray,
    make_i_chase_scene,
    make_i_microbe,
    make_i_unflood,
    make_i_nuked_blue,
    make_i_warmth,
    make_i_fry,
    make_i_announcement,
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
    # Pod escape sequence (queued mid-blowup / on standing out of the web).
    "i_pod_trip": make_i_pod_trip,
    "i_sink_pod": make_i_sink_pod,
    "i_fall_asleep": make_i_fall_asleep,
    # On-demand one-shots (queued by the elevator call-buttons); listed here so
    # a press that hasn't yet "arrived" survives a reload.
    "i_upper_elevator_arrive": make_i_upper_elevator_arrive,
    "i_lower_elevator_arrive": make_i_lower_elevator_arrive,
    "i_upper_elevator_trip": make_i_upper_elevator_trip,
    "i_lower_elevator_trip": make_i_lower_elevator_trip,
    # Card-access turnoff / close demons (queued on demand by SLOT-F).
    "i_turnoff_upper_elevator": make_i_turnoff_upper_elevator,
    "i_turnoff_lower_elevator": make_i_turnoff_lower_elevator,
    "i_turnoff_teleportation": make_i_turnoff_teleportation,
    "i_turnoff_mini": make_i_turnoff_mini,
    "i_turnoff_shuttle": make_i_turnoff_shuttle,
    "i_kitchen_door_closes": make_i_kitchen_door_closes,
    "i_shuttle": make_i_shuttle,
    "i_magnet": make_i_magnet,
    "i_clear_floyd_peer": make_i_clear_floyd_peer,
    "i_cryo_elevator_arrive": make_i_cryo_elevator_arrive,
    "i_floyd_foray": make_i_floyd_foray,
    "i_chase_scene": make_i_chase_scene,
    "i_microbe": make_i_microbe,
    "i_unflood": make_i_unflood,
    "i_nuked_blue": make_i_nuked_blue,
    "i_warmth": make_i_warmth,
    "i_fry": make_i_fry,
    "i_announcement": make_i_announcement,
}

_DEFAULT_TICKS = {
    "i_blather": -1,
    "i_ambassador": -1,
    "i_random_interrupts": 1,
    "i_sleep_warnings": 3600,
    "i_hunger_warnings": 2000,
    "i_sickness_warnings": 1000,
    "i_blowup_feinstein": -1,
    "i_pod_trip": -1,
    "i_sink_pod": -1,
    "i_fall_asleep": 16,
    "i_upper_elevator_arrive": 50,
    "i_lower_elevator_arrive": 100,
    "i_upper_elevator_trip": 50,
    "i_lower_elevator_trip": 100,
    "i_turnoff_upper_elevator": 180,
    "i_turnoff_lower_elevator": 200,
    "i_turnoff_teleportation": 30,
    "i_turnoff_mini": 30,
    "i_turnoff_shuttle": 100,
    "i_kitchen_door_closes": 50,
    "i_shuttle": -1,
    "i_magnet": -1,
    "i_clear_floyd_peer": 40,
    "i_cryo_elevator_arrive": 100,
    "i_floyd_foray": -1,
    "i_chase_scene": -1,
    "i_microbe": -1,
    "i_unflood": 1000,
    "i_nuked_blue": 50,
    "i_warmth": -1,
    "i_fry": 200,
    "i_announcement": 130,
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
        interrupt_states (dict: name -> {ticks_remaining, enabled, is_demon, state}),
        floyd_state (dict: Floyd's boolean flags + current_room + foray_counter +
                    i_floyd_active + i_floyd_foray_active)

    ndb attributes (in-process only, rebuilt at_start):
        ndb.game_state  — live GameState object
        ndb.floyd       — live FloydNPC instance
    """

    def at_script_creation(self) -> None:
        self.key = "game_state"
        self.persistent = True
        self.interval = -1   # not time-based; driven by advance()
        self.db.internal_moves = 0
        self.db.day = 1
        self.db.score = 0
        self.db.dead = False
        self.db.chronometer_present = False
        self.db.chronometer_munged = False
        self.db.flags = {}
        self.db.interrupt_states = {}
        self.db.floyd_state = {}

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
        # An object handler (e.g. attacking Blather) may have killed the player
        # during func(); the clock stops the moment the game is over (ZIL FINISH).
        if gs.dead:
            self._persist(gs)
            return
        fatal = gs.advance_clock()
        # A fatal interrupt routes its own faithful cause through gs.die();
        # this fallback covers any fatal path that didn't (so the banner and
        # the `dead` halt-state still fire exactly once).
        if fatal and not gs.dead:
            gs.die("Your adventure ends here.")
        # ZIL end-of-turn: the player's room ACTION runs with M-END each turn
        # (corridor hints, chase scenes). Skipped once the game is over.
        if not gs.dead:
            loc = getattr(caller, "location", None)
            if loc is not None and hasattr(loc, "db"):
                from world.action_handlers import dispatch_room, M_END
                dispatch_room(loc, caller, M_END)
        self._persist(gs)

    def get_floyd(self) -> "FloydNPC":
        """Return the live FloydNPC, rebuilding from db if needed."""
        if not self.ndb.floyd:
            self._rebuild_floyd(self.get_gs())
        return self.ndb.floyd

    def set_timeless(self) -> None:
        """Mark the current action as consuming no game time (c_elapsed = 0)."""
        self.get_gs().set_timeless()

    def reset_elapsed(self) -> None:
        """Restore default tick cost (c_elapsed = 7) for the next action."""
        self.get_gs().reset_elapsed()

    def set_move_elapsed(self, cost: int | None = None) -> None:
        """Set a successful step's cost (room C-MOVE, else DEFAULT-MOVE = 20)."""
        self.get_gs().set_move_elapsed(cost)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Names handled by _persist_floyd / _rebuild_floyd; excluded from generic loop.
    _FLOYD_INTERRUPT_NAMES = frozenset({"i_floyd", "i_floyd_foray"})

    def _rebuild(self) -> None:
        """Reconstruct GameState and Floyd from db; re-register all interrupts."""
        gs = GameState()
        gs.internal_moves = self.db.internal_moves or 0
        gs.day = self.db.day or 1
        gs.score = self.db.score or 0
        gs.dead = bool(self.db.dead)
        gs.chronometer_present = bool(self.db.chronometer_present)
        gs.chronometer_munged = bool(self.db.chronometer_munged)
        # Overlay saved flags onto GameState's default seed (keeps NO-MICROBE
        # default if it was never persisted).
        if self.db.flags:
            gs.flags.update(self.db.flags)
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

        self._rebuild_floyd(gs)

    def _rebuild_floyd(self, gs: GameState) -> None:
        """Restore FloydNPC state from db.floyd_state and re-register his interrupts."""
        from world.floyd import FloydNPC
        saved: dict = self.db.floyd_state or {}

        floyd = FloydNPC(gs)
        floyd.score_flag    = saved.get("score_flag", False)
        floyd.spoke         = saved.get("spoke", False)
        floyd.follow        = saved.get("follow", False)
        floyd.reactivated   = saved.get("reactivated", False)
        floyd.introduced    = saved.get("introduced", False)
        floyd.waiting       = saved.get("waiting", False)
        floyd.gave_up       = saved.get("gave_up", False)
        floyd.forayed       = saved.get("forayed", False)
        floyd.foray_counter = saved.get("foray_counter", 0)
        floyd.card_revealed = saved.get("card_revealed", False)
        floyd.current_room  = saved.get("current_room", None)

        if saved.get("i_floyd_active", False):
            intr = gs.scheduler.queue(floyd.i_floyd, -1)
            gs.scheduler.enable(intr)

        if saved.get("i_floyd_foray_active", False):
            intr = gs.scheduler.queue(floyd.i_floyd_foray, -1)
            gs.scheduler.enable(intr)
            floyd._foray_intr = intr

        self.ndb.floyd = floyd

    def _persist(self, gs: GameState) -> None:
        """Write serializable GameState fields back to db."""
        self.db.internal_moves = gs.internal_moves
        self.db.day = gs.day
        self.db.score = gs.score
        self.db.dead = gs.dead
        self.db.chronometer_present = gs.chronometer_present
        self.db.chronometer_munged = gs.chronometer_munged
        self.db.flags = dict(gs.flags)

        states: dict = {}
        for intr in gs.scheduler._interrupts:
            name = getattr(intr.routine, "__name__", None)
            if name is None or name in self._FLOYD_INTERRUPT_NAMES:
                continue
            state_dict = dict(getattr(intr.routine, "_state", {}))
            states[name] = {
                "ticks_remaining": intr.ticks_remaining,
                "enabled": intr.enabled,
                "is_demon": intr.is_demon,
                "state": state_dict,
            }
        self.db.interrupt_states = states
        self._persist_floyd(gs)

    def _persist_floyd(self, gs: GameState) -> None:
        """Write Floyd's state back to db.floyd_state."""
        floyd = self.ndb.floyd
        if floyd is None:
            return
        active_names = {
            getattr(i.routine, "__name__", None)
            for i in gs.scheduler._interrupts
            if i.enabled
        }
        self.db.floyd_state = {
            "score_flag":          floyd.score_flag,
            "spoke":               floyd.spoke,
            "follow":              floyd.follow,
            "reactivated":         floyd.reactivated,
            "introduced":          floyd.introduced,
            "waiting":             floyd.waiting,
            "gave_up":             floyd.gave_up,
            "forayed":             floyd.forayed,
            "foray_counter":       floyd.foray_counter,
            "card_revealed":       floyd.card_revealed,
            "current_room":        floyd.current_room,
            "i_floyd_active":      "i_floyd" in active_names,
            "i_floyd_foray_active": "i_floyd_foray" in active_names,
        }
