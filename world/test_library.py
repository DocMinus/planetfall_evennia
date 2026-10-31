"""Tests for the library computer terminal (world/library.py, comptwo.zil:679)."""
from evennia.utils.test_resources import EvenniaTestCase

from world.walkthrough import fresh_game, _by_zil


class LibraryTerminalTest(EvenniaTestCase):
    """TERMINAL-F on/off/read plus LIBRARY-TYPE menu navigation."""

    def setUp(self):
        super().setUp()
        self.char, self.gs = fresh_game(char_key="WalkthroughTester", reload=True)
        self.term = _by_zil("TERMINAL")
        self.char.location = _by_zil("LIBRARY-LOBBY")
        self.out = []

        def _capture(text=None, **kwargs):
            if text is not None:
                self.out.append(str(text[0] if isinstance(text, tuple) else text))
        self.char.msg = _capture

    def _act(self, verb):
        from world.action_handlers import ObjAction
        from world.library import handle_terminal
        self.out.clear()
        consumed = handle_terminal(ObjAction(obj=self.term, caller=self.char,
                                             verb=verb, nouns=["terminal"]))
        self.assertTrue(consumed)
        return "\n".join(self.out)

    def _type(self, num):
        from world.library import library_type
        self.out.clear()
        library_type(self.char, num)
        return "\n".join(self.out)

    def test_terminal_menus(self):
        from world.library import can_type_here
        self.assertIsNotNone(self.term)
        self.assertTrue(can_type_here(self.char))
        self.assertIn("The screen is dark.", self._act("V-EXAMINE"))
        self.assertIn("The screen is blank.", self._act("V-READ"))
        self.assertIn("It isn't on!", self._act("V-LAMP-OFF"))

        # Turn on -> main menu.
        out = self._act("V-LAMP-ON")
        self.assertIn("green flash", out)
        self.assertIn("1. Histooree", out)
        self.assertIn("ONBIT", self.term.db.obj_flags)
        self.assertIn("It's already on.", self._act("V-LAMP-ON"))

        # Words and out-of-range numbers.
        self.assertIn("only has numeric keys", self._type(None))
        self.assertIn("feeps", self._type(0))
        self.assertIn("feeps", self._type(7))
        self.assertEqual(self.gs.flags.get("MENU-LEVEL", 0), 0)

        # Into technology, then a leaf, then back up twice.
        out = self._type(3)
        self.assertIn("different menu appears", out)
        self.assertIn("5. Planateree Sistumz", out)
        self.assertEqual(self.gs.flags["MENU-LEVEL"], 3)
        self.assertIn("feeps", self._type(6))
        out = self._type(4)
        self.assertIn("B-19 seereez", out)
        self.assertIn("Foor moor deetaald", out)
        self.assertEqual(self.gs.flags["MENU-LEVEL"], 34)
        self.assertIn("B-19 seereez", self._act("V-READ"))
        self.assertIn("Foor moor deetaald", self._act("V-EXAMINE"))
        self.assertIn("loowist levul", self._type(2))
        self.assertIn("4. Roobotiks", self._type(0))
        self.assertEqual(self.gs.flags["MENU-LEVEL"], 3)
        self.assertIn("1. Histooree", self._type(0))
        self.assertEqual(self.gs.flags["MENU-LEVEL"], 0)

        # ZIL quirk: culture menu tests <G? 4>, so typing 4 prints nothing.
        self._type(2)
        self.assertEqual(self._type(4), "")

        # Turning off resets MENU-LEVEL.
        self.assertIn("The screen goes dark.", self._act("V-LAMP-OFF"))
        self.assertNotIn("ONBIT", self.term.db.obj_flags)
        self.assertEqual(self.gs.flags["MENU-LEVEL"], 0)
