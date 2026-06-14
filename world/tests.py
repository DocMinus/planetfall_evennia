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

from world.walkthrough import WALKTHROUGH, Driver, fresh_game


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
        # Minimal-victory invariant: the stranded ending scores 60/80.
        self.assertGreaterEqual(
            gs.score, 60, f"victory reached but score regressed to {gs.score}"
        )
