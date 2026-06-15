"""
Command sets

All commands in the game must be grouped in a cmdset.  A given command
can be part of any number of cmdsets and cmdsets can be added/removed
and merged onto entities at runtime.

To create new commands to populate the cmdset, see
`commands/command.py`.

This module wraps the default command sets of Evennia; overloads them
to add/remove commands from the default lineup. You can create your
own cmdsets by inheriting from them or directly from `evennia.CmdSet`.

"""

from evennia import default_cmds
from world.verb_commands import ALL_COMMANDS


def _strip_default_evennia_commands(cmdset):
    """Remove every stock Evennia command from a player-facing cmdset.

    Single-player front-end (Phase 15): Planetfall should expose ONLY its own
    verbs (movement, look, take, SAVE/RESTORE/RESTART/QUIT, …), not Evennia's
    multiplayer/admin chrome — channels, `who`, `page`, OOC, and the whole
    `@`-building/system suite all break the interactive-fiction illusion.

    Game verbs that share a key with a default (e.g. `look`, `get`, `drop`,
    `quit`) were added on top and have already replaced their default
    counterparts, so this only removes the genuinely non-game commands.

    The admin path for development is intentionally *not* in-game: use the
    Django admin site (`/admin/`) or `make shell`, both of which are unaffected.
    """
    for cmd in list(cmdset.commands):
        if type(cmd).__module__.startswith("evennia.commands.default"):
            cmdset.remove(cmd)


class CharacterCmdSet(default_cmds.CharacterCmdSet):
    """
    The `CharacterCmdSet` contains general in-game commands like `look`,
    `get`, etc available on in-game Character objects. It is merged with
    the `AccountCmdSet` when an Account puppets a Character.
    """

    key = "DefaultCharacter"

    def at_cmdset_creation(self):
        super().at_cmdset_creation()
        for cmd_cls in ALL_COMMANDS:
            self.add(cmd_cls())
        _strip_default_evennia_commands(self)


class AccountCmdSet(default_cmds.AccountCmdSet):
    """
    This is the cmdset available to the Account at all times. It is
    combined with the `CharacterCmdSet` when the Account puppets a
    Character. It holds game-account-specific commands, channel
    commands, etc.
    """

    key = "DefaultAccount"

    def at_cmdset_creation(self):
        """
        Populates the cmdset
        """
        super().at_cmdset_creation()
        # Strip account-level multiplayer/OOC commands (channels, who, page,
        # ic/ooc, charcreate, …). With auto-puppet the player is always in-world
        # via the CharacterCmdSet's Planetfall verbs, so none of these are needed
        # and they'd otherwise leak into the in-game prompt and break immersion.
        _strip_default_evennia_commands(self)


class UnloggedinCmdSet(default_cmds.UnloggedinCmdSet):
    """
    Command set available to the Session before being logged in.  This
    holds commands like creating a new account, logging in, etc.
    """

    key = "DefaultUnloggedin"

    def at_cmdset_creation(self):
        """
        Populates the cmdset
        """
        super().at_cmdset_creation()
        #
        # any commands you add below will overload the default ones.
        #


class SessionCmdSet(default_cmds.SessionCmdSet):
    """
    This cmdset is made available on Session level once logged in. It
    is empty by default.
    """

    key = "DefaultSession"

    def at_cmdset_creation(self):
        """
        This is the only method defined in a cmdset, called during
        its creation. It should populate the set with command instances.

        As and example we just add the empty base `Command` object.
        It prints some info.
        """
        super().at_cmdset_creation()
        # Strip the session-level `sessions` command (multi-session management);
        # single-player has exactly one session.
        _strip_default_evennia_commands(self)
