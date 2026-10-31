"""Automated regression tests for the Planetfall port (Phase 14c.2).

The headline test replays the canonical end-to-end :data:`world.walkthrough.WALKTHROUGH`
against a fresh, JSON-loaded world inside Evennia's test database and asserts the
game is won. This is the regression net for the whole critical path: parser →
``resolve_obj`` → PRSO/PRSI dispatch → movement → clock/interrupt firing →
victory. If any milestone breaks, this test goes red.

Run::

    make test
    # or directly:
    .venv/bin/evennia test --settings server.conf.settings world

The walkthrough drives ~6000 internal moves (mostly cheap ``wait`` commands in
the survival-clock WaitUntil loops), so this test is slower than a typical unit
test — budget a minute or so. It is the single most valuable test in the suite.
"""
from evennia.utils.test_resources import EvenniaTestCase

from world.walkthrough import (
    WALKTHROUGH, Driver, fresh_game,
    ESCAPE_POD, DESCEND_AND_FLOYD, TOOLS_AND_CARDS, COMM, DAY1_SLEEP,
    DAY2_DESCENT, SHUTTLE, COURSE_CONTROL, DEFENSE, BIO_FORAY,
    COMPUTER_FIX, ESCAPE_AND_CRYO,
)


def _interrupt_fingerprint(gs):
    """Name -> (enabled, is_demon, ticks, state) for every queued interrupt.

    Includes each closure's live ``_state`` dict so a restore is checked to
    reproduce demon counters (blowup/trip/shuttle/chase), not just which
    interrupts are queued.
    """
    fp = {}
    for intr in gs.scheduler._interrupts:
        name = getattr(intr.routine, "__name__", None)
        if name:
            state = dict(getattr(intr.routine, "_state", {}))
            fp[name] = (intr.enabled, intr.is_demon, intr.ticks_remaining, state)
    return fp


class WalkthroughRegressionTest(EvenniaTestCase):
    """Replay the full DECK-NINE → victory walkthrough and assert the win."""

    def test_walkthrough_reaches_victory(self):
        # Build a pristine world from JSON and place the tester at DECK-NINE.
        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)

        # Replay every typed command; Driver raises StepError on any failed
        # milestone (missing expected output, forbidden output, or a failed
        # post-condition check), which fails the test with the offending step.
        drv = Driver(char, gs, verbose=False)
        drv.run(WALKTHROUGH)

        # The end state: the cryo anteroom resolved the game as a win — dead is
        # set (the game is over) but with no death reason (victory, not death).
        self.assertTrue(gs.dead, "walkthrough finished but the game is not over")
        self.assertIsNone(
            gs.death_reason,
            f"game ended in death, not victory: {gs.death_reason!r}",
        )
        # Maximal-victory invariant: the full run repairs every subsystem
        # (course control, defense, comm, computer) for a perfect 80/80 — the
        # Galactic Overlord ending in which Floyd is revived.
        self.assertEqual(
            gs.score, 80, f"victory reached but score regressed to {gs.score}/80"
        )


class HyphenTokenResolutionTest(EvenniaTestCase):
    """Phase 14b — hyphenated compound nouns resolve spaced, hyphenated, or split.

    ZIL stores multi-word adjectives (WIDE-NOSED, PROTEIN-RICH) and compound card
    names as single hyphenated tokens; resolution now splits hyphens on both
    sides so every natural spelling matches.
    """

    def test_hyphenated_nouns_resolve_every_spelling(self):
        from world.walkthrough import _by_zil
        from world.verb_commands import resolve_obj

        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)

        # The wide-nosed pliers: bring them into the tester's room and resolve.
        pliers = _by_zil("PLIERS")
        pliers.move_to(char, quiet=True, move_type="get")
        for spelling in ("wide-nosed pliers", "wide nosed pliers", "pliers"):
            self.assertIs(resolve_obj(char, spelling), pliers,
                          f"pliers should resolve from {spelling!r}")

        # The protein-rich liquid: "high protein" must reach it via PROTEIN-RICH.
        hp = _by_zil("HIGH-PROTEIN")
        canteen = _by_zil("CANTEEN")
        canteen.move_to(char, quiet=True, move_type="get")
        canteen.db.is_open = True
        hp.move_to(canteen, quiet=True, move_type="move")
        for spelling in ("protein-rich liquid", "high protein", "brown liquid"):
            self.assertIs(resolve_obj(char, spelling), hp,
                          f"high-protein should resolve from {spelling!r}")


class UnfloodTimerTest(EvenniaTestCase):
    """Phase 14b — the fungicide flood drains on a timer (I-UNFLOOD)."""

    def test_flood_drains_and_chase_turns_lethal(self):
        import world.interrupts as I

        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        gs.flags["LAB-FLOODED"] = True
        intr = gs.scheduler.queue(I.make_i_unflood(gs), 1000)
        gs.scheduler.enable(intr)

        # Not yet: a few moves' worth of ticks leaves the mist (and safety) up.
        gs.scheduler.tick(200)
        self.assertTrue(gs.flags["LAB-FLOODED"], "flood drained far too early")

        # Past the window: the mist clears, so the chase (i_chase_scene) is lethal.
        gs.scheduler.tick(1000)
        self.assertFalse(gs.flags["LAB-FLOODED"], "flood never drained — no timed dash")


class RoomDescriptionTest(EvenniaTestCase):
    """Every room has a description: a static LDESC or an M-LOOK routine.

    33 rooms have no LDESC in the ZIL; their text lives in the room's ACTION
    routine. An unported routine leaves the room rendering as a bare name.
    """

    def test_every_room_describes_itself(self):
        from typeclasses.rooms import PlanetfallRoom
        from world.action_handlers import dispatch_room_look

        char, _gs = fresh_game(char_key="WalkthroughTester", reload=True)
        blank = [room.db.zil_id for room in PlanetfallRoom.objects.all()
                 if not (room.db.desc or dispatch_room_look(room, char))]
        self.assertEqual(blank, [], "rooms with no description")


class RoomDeathTrapTest(EvenniaTestCase):
    """Room demons ported from ZIL: drowning underwater, radiation poisoning."""

    def test_underwater_drowns_on_third_turn(self):
        from world.action_handlers import dispatch_room, M_END
        from world.walkthrough import _by_zil

        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        water = _by_zil("UNDERWATER")
        for _ in range(2):
            dispatch_room(water, char, M_END)
        self.assertFalse(gs.dead, "drowned too soon")
        dispatch_room(water, char, M_END)
        self.assertTrue(gs.dead, "never drowned")

    def test_radiation_lab_is_fatal(self):
        import world.interrupts as I

        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        gs.scheduler.enable(gs.scheduler.queue(I.make_i_nuked_blue(gs), 50))
        self.assertFalse(gs.scheduler.tick(50))     # sick and dizzy
        self.assertFalse(gs.scheduler.tick(7))      # hair falls out
        self.assertTrue(gs.scheduler.tick(7), "radiation never killed")
        self.assertTrue(gs.dead)


class DoorRulesTest(EvenniaTestCase):
    """Doors with ZIL ACTION routines refuse the generic OPEN when they should."""

    def _at(self, room):
        from world.walkthrough import _by_zil
        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        char.location = _by_zil(room)
        return Driver(char, gs, verbose=False), gs

    def test_pod_door_needs_an_emergency(self):
        drv, gs = self._at("DECK-NINE")
        self.assertIn("no emergency", drv._emit("open pod door"))

    def test_storage_door_is_padlocked(self):
        drv, gs = self._at("MESS-CORRIDOR")
        self.assertIn("until the padlock is removed", drv._emit("open storage door"))

    def test_bio_lock_doors_interlock(self):
        from world.walkthrough import _by_zil
        drv, gs = self._at("BIO-LOCK-WEST")
        _by_zil("BIO-DOOR-EAST").db.is_open = True
        self.assertIn("cannot be open simultaneously", drv._emit("open west door"))
        self.assertFalse(_by_zil("BIO-DOOR-WEST").db.is_open)


class TakeAllTest(EvenniaTestCase):
    """Phase 14b — "take all" grabs every takeable item on the ground."""

    def test_take_all(self):
        from world.walkthrough import _by_zil

        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        char.location = _by_zil("TOOL-ROOM")   # holds magnet, pliers, laser, flask
        drv = Driver(char, gs, verbose=False)
        out = drv._emit("take all")

        self.assertIn("Taken.", out)
        held = {o.db.zil_id for o in char.contents}
        for zil in ("MAGNET", "PLIERS", "LASER", "FLASK"):
            self.assertIn(zil, held, f"take all missed {zil}")

    def test_all_except_and_lists(self):
        """ZIL MANY: "drop all except X", "drop X and Y"; worn things stay on."""
        from world.walkthrough import _by_zil

        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        char.location = _by_zil("TOOL-ROOM")
        drv = Driver(char, gs, verbose=False)
        drv._emit("take all")
        drv._emit("drop all except laser")
        held = {o.db.zil_id for o in char.contents}
        self.assertIn("LASER", held)
        self.assertIn("PATROL-UNIFORM", held, "a worn uniform can't be dropped")
        self.assertNotIn("PLIERS", held)
        drv._emit("take pliers and flask")
        held = {o.db.zil_id for o in char.contents}
        self.assertTrue({"PLIERS", "FLASK"} <= held)


class ClockFidelityTest(EvenniaTestCase):
    """ZIL time costs and one-slot-per-routine QUEUE semantics."""

    def test_wait_costs_forty_ticks(self):
        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        drv = Driver(char, gs, verbose=False)
        before = gs.internal_moves
        drv._emit("wait")
        self.assertEqual(gs.internal_moves - before, 40)   # verbs.zil V-WAIT

    def test_requeue_resets_existing_slot(self):
        from world.state import Scheduler

        def i_demo():
            return False

        sched = Scheduler()
        sched.queue(i_demo, 5)
        sched.tick(4)
        sched.queue(i_demo, 180)   # re-slide the card: restart, don't duplicate
        self.assertEqual(len(sched._interrupts), 1)
        self.assertEqual(sched._interrupts[0].ticks_remaining, 180)


class MicrobeBattleTest(EvenniaTestCase):
    """Phase 14b — the strip microbe (comptwo.zil) blocks the way back.

    The walkthrough test already drives the happy path end to end; these pin the
    individual mechanics: the spawn-and-block, the laser-setting gate, the eaten
    death, and the throw-it-off-the-strip dispatch.
    """

    def _at_strip(self):
        from world.walkthrough import _by_zil
        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        gs.player = char
        char.location = _by_zil("MIDDLE-OF-STRIP")
        gs.flags["COMPUTER-FIXED"] = True
        return char, gs

    def test_microbe_spawns_and_blocks_the_strip(self):
        from world.action_handlers import dispatch_room, M_ENTER
        from world.walkthrough import _by_zil

        char, gs = self._at_strip()
        self.assertTrue(gs.flags["NO-MICROBE"], "strip starts clear")
        dispatch_room(char.location, char, M_ENTER)
        self.assertIs(_by_zil("MICROBE").location, char.location,
                      "the microbe should drop onto the strip")
        self.assertFalse(gs.flags["NO-MICROBE"], "the south exit is now blocked")
        self.assertTrue(
            any(getattr(i.routine, "__name__", None) == "i_microbe" and i.enabled
                for i in gs.scheduler._interrupts),
            "I-MICROBE should be running once the microbe appears")

    def test_red_setting_is_too_fine_to_hit(self):
        from world.action_handlers import _shoot_microbe

        char, gs = self._at_strip()
        gs.flags["LASER-SETTING"] = 1
        _shoot_microbe(gs, char)
        self.assertFalse(gs.flags["MICROBE-HIT"], "red passes harmlessly through")
        gs.flags["LASER-SETTING"] = 2
        _shoot_microbe(gs, char)
        self.assertTrue(gs.flags["MICROBE-HIT"], "a wider beam registers a hit")

    def test_unlasered_microbe_eats_you(self):
        import world.interrupts as I

        char, gs = self._at_strip()
        i_microbe = I.make_i_microbe(gs)
        gs.flags["MICROBE-COUNTER"] = 2     # it has already closed in twice
        gs.flags["MICROBE-HIT"] = False
        i_microbe()
        self.assertTrue(gs.dead, "the microbe should feed if not held off")
        self.assertIsNotNone(gs.death_reason, "death, not victory")

    def test_throwing_the_hot_laser_off_the_strip_dispatches_it(self):
        from world.action_handlers import (
            dispatch_room, handle_strip, ObjAction, M_ENTER,
        )
        from world.walkthrough import _by_zil

        char, gs = self._at_strip()
        dispatch_room(char.location, char, M_ENTER)     # spawn it
        _by_zil("LASER").move_to(char, quiet=True, move_type="get")
        gs.flags["WARMTH-FLAG"] = 8                      # hot enough to lure it
        consumed = handle_strip(ObjAction(
            obj=_by_zil("STRIP"), caller=char,
            verb="V-THROW-OFF", nouns=["laser", "strip"]))
        self.assertTrue(consumed, "STRIP-F should consume the throw")
        self.assertTrue(gs.flags["MICROBE-DISPATCHED"])
        self.assertTrue(gs.flags["NO-MICROBE"], "the way back is clear again")
        self.assertIsNone(_by_zil("MICROBE").location,
                          "the microbe followed the laser into the void")
        self.assertFalse(
            any(getattr(i.routine, "__name__", None) == "i_microbe" and i.enabled
                for i in gs.scheduler._interrupts),
            "I-MICROBE should be disabled after the dispatch")


class SaveRestoreRoundTripTest(EvenniaTestCase):
    """Phase 14a.3 — SAVE/RESTORE survives a live mid-game state and continues.

    Play into the deep complex (Floyd's demon running, ~all puzzle flags set,
    the survival clocks ticking), ``save``, mutate the live game (walk away and
    let several turns tick), then ``restore`` and assert the snapshot came back
    intact: room, score, day, clock, every flag, and the full interrupt set.
    Finally, replay the rest of the walkthrough from the restored state and
    confirm it still reaches the 80/80 victory — proving the restore produced a
    genuinely playable game, not just matching scalars.
    """

    def test_save_restore_midgame_then_finish(self):
        from world.scheduler_script import get_game_state_script

        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        drv = Driver(char, gs, verbose=False)

        prefix = (list(ESCAPE_POD) + list(DESCEND_AND_FLOYD) + list(TOOLS_AND_CARDS)
                  + list(COMM) + list(DAY1_SLEEP) + list(DAY2_DESCENT)
                  + list(SHUTTLE) + list(COURSE_CONTROL))
        suffix = (list(DEFENSE) + list(BIO_FORAY) + list(COMPUTER_FIX)
                  + list(ESCAPE_AND_CRYO))
        drv.run(prefix)

        # Snapshot of what a faithful restore must reproduce.
        want = dict(
            room=char.location.db.zil_id,
            score=gs.score, day=gs.day, moves=gs.internal_moves,
            flags=dict(gs.flags),
            interrupts=_interrupt_fingerprint(gs),
            inventory={o.db.zil_id for o in char.contents},
        )
        self.assertGreater(want["score"], 0)
        self.assertIn("i_floyd", want["interrupts"], "Floyd's demon should be live here")

        drv._emit("save")

        # Mutate the live game: leave the room and let the clocks/interrupts tick.
        drv._emit("south")                       # -> Systems Corridor East (already scored)
        for _ in range(5):
            drv._emit("wait")
        self.assertNotEqual(char.location.db.zil_id, want["room"],
                            "mutation should have moved us away from the save room")

        drv._emit("restore")

        # The live GameState is rebuilt on restore — re-fetch it and re-link the
        # player (mirrors at_post_puppet wiring a real session would do).
        gs = get_game_state_script().get_gs()
        gs.player = char
        drv.gs = gs

        self.assertEqual(char.location.db.zil_id, want["room"], "room not restored")
        self.assertEqual(gs.score, want["score"], "score not restored")
        self.assertEqual(gs.day, want["day"], "day not restored")
        self.assertEqual(gs.internal_moves, want["moves"], "clock not restored")
        self.assertEqual(dict(gs.flags), want["flags"], "flags not restored")
        self.assertEqual(_interrupt_fingerprint(gs), want["interrupts"],
                         "interrupt set not restored")
        self.assertEqual({o.db.zil_id for o in char.contents}, want["inventory"],
                         "inventory not restored")

        # The restored game must still be winnable end to end.
        drv.run(suffix)
        self.assertTrue(gs.dead and gs.death_reason is None, "no victory after restore")
        self.assertEqual(gs.score, 80, f"restored game finished at {gs.score}/80")

    def test_save_restore_mid_blowup(self):
        """Round-trip a live timed demon mid-sequence (the Feinstein blowup).

        Unlike the deep-complex save (a quiet room boundary), this saves while
        ``i_blowup_feinstein`` is mid-count inside the escape pod — a demon whose
        progress lives in its closure ``_state``/the BLOWUP-COUNTER flag. Restore
        must roll the countdown back, and the pod must still eject and land.
        """
        from world.scheduler_script import get_game_state_script

        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        drv = Driver(char, gs, verbose=False)

        # Into the pod and strapped in: look, wait for the door, west, sit.
        drv.run(list(ESCAPE_POD[:4]))
        # Let the blowup count a couple of ticks, but stop well before ejection.
        while gs.flags.get("BLOWUP-COUNTER", 0) < 2:
            drv._emit("wait")
        self.assertLess(gs.flags.get("BLOWUP-COUNTER"), 5, "pod already ejected")

        want_counter = gs.flags.get("BLOWUP-COUNTER")
        want_interrupts = _interrupt_fingerprint(gs)
        want_room = char.location.db.zil_id
        self.assertIn("i_blowup_feinstein", want_interrupts, "blowup demon should be live")

        drv._emit("save")
        for _ in range(2):                       # advance the countdown
            drv._emit("wait")
        self.assertGreater(gs.flags.get("BLOWUP-COUNTER"), want_counter)

        drv._emit("restore")
        gs = get_game_state_script().get_gs()
        gs.player = char
        drv.gs = gs

        self.assertEqual(char.location.db.zil_id, want_room)
        self.assertEqual(gs.flags.get("BLOWUP-COUNTER"), want_counter,
                         "blowup countdown not rolled back on restore")
        self.assertEqual(_interrupt_fingerprint(gs), want_interrupts,
                         "blowup/trip demon state not restored")

        # The restored pod must still complete its ejection and ride down.
        drv.run([
            ESCAPE_POD[4],   # WaitUntil BLOWUP-COUNTER >= 5 (ejection)
            ESCAPE_POD[5],   # WaitUntil TRIP-COUNTER > 14 (it lands)
        ])
        self.assertGreaterEqual(gs.flags.get("BLOWUP-COUNTER"), 5)
        self.assertGreater(gs.flags.get("TRIP-COUNTER"), 14)
        self.assertFalse(gs.dead, "pod sequence killed us after restore")
