# -*- coding: utf-8 -*-
"""
Connection screen

This is the text to show the user when they first connect to the game (before
they log in).

To change the login screen in this module, do one of the following:

- Define a function `connection_screen()`, taking no arguments. This will be
  called first and must return the full string to act as the connection screen.
  This can be used to produce more dynamic screens.
- Alternatively, define a string variable in the outermost scope of this module
  with the connection string that should be displayed. If more than one such
  variable is given, Evennia will pick one of them at random.

The commands available to the user when the connection screen is shown
are defined in evennia.default_cmds.UnloggedinCmdSet. The parsing and display
of the screen is done by the unlogged-in "look" command.

"""

from evennia import utils

# Single-player front-end (Phase 15): with auto-login the player normally never
# sees this screen. It is kept minimal and on-theme as a fallback for a manual
# `connect` (e.g. from a raw telnet/MUD client).
CONNECTION_SCREEN = """
|y==============================================================|n
 |yP L A N E T F A L L|n  — an interactive fiction in the Infocom tradition

 An unofficial fan port to Evennia.  Version {}.

 To begin, type:  |wconnect <username> <password>|n
|y==============================================================|n""".format(
    utils.get_evennia_version("short")
)
