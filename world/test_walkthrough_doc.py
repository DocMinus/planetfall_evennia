"""Replay the player-facing walkthrough against the game.

The compact walkthrough in planetfall_resources/WALKTHROUGH_this_port.txt is meant to be typed in by a person,
so this test types it in too: every command line exactly as written. The
``<< >>`` notes are the places where a player has to react to the game (wait
for an event, read the flashing colour, repeat a shot), and each one the test
understands is mapped to that reaction below. A command whose reply shows the
parser or world rejecting it fails the test, so the document can't silently
drift out of date.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from evennia.utils.test_resources import EvenniaTestCase

from world.walkthrough import Driver, _by_zil, _door_open, _floyd_with, fresh_game

DOC = (Path(__file__).resolve().parent.parent
       / "planetfall_resources" / "WALKTHROUGH_this_port.txt")

_COLOURS = {1: "red", 2: "blue", 3: "green", 4: "yellow",
            5: "gray", 6: "brown", 7: "black"}

# Replies that mean a walkthrough command didn't do what the text intends.
_REJECTED = ("I don't understand", "You see no ", "You can't see any",
             "You don't see any", "You're not carrying any",
             "You can't go that way", "Put it where?")


def parse(text: str) -> list[tuple[str, object]]:
    """Split the document into ("note", text) and ("cmds", [command, ...])."""
    body = text.split("-" * 79, 2)[-1]   # skip the title and tips
    segments: list[tuple[str, object]] = []
    for line in body.splitlines():
        if line.startswith("<<"):
            note = line.strip("<> ").strip()
            if segments and segments[-1][0] == "note" and not segments[-1][1].endswith(":"):
                segments[-1] = ("note", f"{segments[-1][1]} {note}")
            else:
                segments.append(("note", note))
        elif re.match(r"^ {4}\S", line):
            cmds = [c.strip() for c in line.strip().rstrip(".").split(". ") if c.strip()]
            if segments and segments[-1][0] == "cmds":
                segments[-1][1].extend(cmds)
            else:
                segments.append(("cmds", cmds))
    return segments


class WalkthroughDocTest(EvenniaTestCase):
    """WALKTHROUGH_this_port.txt, typed in verbatim, wins the game."""

    def test_walkthrough_doc_wins(self):
        char, gs = fresh_game(char_key="WalkthroughTester", reload=True)
        drv = Driver(char, gs, verbose=False)

        def say(cmd):
            if gs.dead:
                self.fail(f"died before '{cmd}': {gs.death_reason!r}")
            drv._eat_if_hungry()   # the tips: eat when the game says you're hungry
            colour = _COLOURS.get(gs.flags.get("CHEMICAL-REQUIRED"), "?")
            cmd = cmd.replace("[COLOUR]", colour)
            out = drv._emit(cmd)
            bad = next((r for r in _REJECTED if r.lower() in out.lower()), None)
            if bad and not gs.dead:
                self.fail(f"'{cmd}' was rejected ({bad!r}):\n{out}\n"
                          f"--- last commands ---\n" + "\n".join(drv.log[-6:]))
            return out

        def wait_until(pred, what, n=40):
            for _ in range(n):
                if pred():
                    return
                say("wait")
            self.assertTrue(pred(), f"never happened: {what}")

        def repeat(cmd, pred, what, n=40):
            for _ in range(n):
                if pred():
                    return
                say(cmd)
            self.assertTrue(pred(), f"'{cmd}' never achieved: {what}")

        # Set PF_WALKTHROUGH_LOG=<path> to keep the full transcript for debugging.
        log_path = os.environ.get("PF_WALKTHROUGH_LOG")
        if log_path:
            self.addCleanup(lambda: Path(log_path).write_text("\n".join(drv.log)))

        segments = parse(DOC.read_text())
        self.assertGreater(len(segments), 10, "walkthrough failed to parse")
        repeat_next = False    # "REPEAT this round ... until the panel goes dark"
        for kind, payload in segments:
            if kind == "cmds":
                for _ in range(3 if repeat_next else 1):
                    for cmd in payload:
                        say(cmd)
                    if repeat_next and gs.flags.get("COMM-FIXED"):
                        break
                repeat_next = False
                continue
            note = payload.lower()
            if note.startswith("wait until the ship explodes"):
                wait_until(lambda: _door_open("POD-DOOR"), "the pod door opening")
            elif note.startswith("wait until you fall asleep"):
                mark = len(drv.log)
                wait_until(lambda: any("You wake" in e for e in drv.log[mark:]),
                           "sleeping through the night", n=60)
            elif note.startswith("wait until floyd is in the room"):
                wait_until(lambda: _floyd_with(gs), "Floyd joining you")
            elif note.startswith("wait until floyd comes in"):
                wait_until(lambda: gs.flags.get("COMPUTER-FLAG"), "Floyd spotting the computer")
            elif note.startswith("repeat this round"):
                repeat_next = True
            elif note.startswith("repeat fire laser at speck"):
                repeat("fire laser at speck",
                       lambda: gs.flags.get("COMPUTER-FIXED"), "the speck vaporizing")
            elif note.startswith("repeat fire laser at microbe"):
                repeat("fire laser at microbe",
                       lambda: gs.flags.get("WARMTH-FLAG", 0) > 7, "a very warm laser",
                       n=15)
            elif note.startswith(("note the colour", "then on to")):
                pass
            else:
                self.fail(f"walkthrough note the test doesn't understand: {payload!r}")

        self.assertTrue(gs.flags.get("COMM-FIXED"), "comm system never repaired")
        self.assertTrue(gs.dead and gs.death_reason is None,
                        f"game didn't end in victory (dead={gs.dead}, "
                        f"reason={gs.death_reason!r})")
        self.assertIn("You have won", "\n".join(drv.log[-3:]))
