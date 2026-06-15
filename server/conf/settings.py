r"""
Evennia settings file.

The available options are found in the default settings file found
here:

https://www.evennia.com/docs/latest/Setup/Settings-Default.html

Remember:

Don't copy more from the default file than you actually intend to
change; this will make sure that you don't overload upstream updates
unnecessarily.

When changing a setting requiring a file system path (like
path/to/actual/file.py), use GAME_DIR and EVENNIA_DIR to reference
your game folder and the Evennia library folders respectively. Python
paths (path.to.module) should be given relative to the game's root
folder (typeclasses.foo) whereas paths within the Evennia library
needs to be given explicitly (evennia.foo).

If you want to share your game dir, including its settings, you can
put secret game- or server-specific settings in secret_settings.py.

"""

# Use the defaults from Evennia unless explicitly overridden
from evennia.settings_default import *

######################################################################
# Evennia base server config
######################################################################

# This is the name of your game. Make it catchy!
# Used for the browser tab title, MSSP, and the connect banner.
SERVERNAME = "Planetfall"

# Local timezone for log/chronometer display (Evennia defaults to UTC).
TIME_ZONE = "Europe/Stockholm"


######################################################################
# Single-player front-end (Phase 15)
######################################################################
# Planetfall is a single-player game served locally: exactly one player
# inhabits the single shared game world. These values are Evennia's own
# defaults, but we set them explicitly so the single-player contract is
# stated in one place and cannot drift with upstream changes.

# One session == one character; no character-select menu.
MULTISESSION_MODE = 0
# A new account immediately owns one auto-created character...
AUTO_CREATE_CHARACTER_WITH_ACCOUNT = True
# ...which is auto-puppeted on login, dropping the player straight in-world.
AUTO_PUPPET_ON_LOGIN = True
# Never more than the one protagonist.
MAX_NR_CHARACTERS = 1
# No throwaway guest logins: the fixed player character must persist so that
# in-game SAVE/RESTORE and live world state survive across browser sessions.
GUEST_ENABLED = False

# Auto-authenticate the browser session as the single player so the served page
# opens straight in-world (no login screen). Must run *before* Evennia's
# SharedLoginMiddleware, which propagates the authenticated session to the
# webclient. LOCAL SINGLE-PLAYER ONLY — never on a public deployment.
MIDDLEWARE = list(MIDDLEWARE)
_pf_autologin = "web.utils.middleware.SinglePlayerAutoLoginMiddleware"
_pf_sharedlogin = "evennia.web.utils.middleware.SharedLoginMiddleware"
if _pf_autologin not in MIDDLEWARE:
    if _pf_sharedlogin in MIDDLEWARE:
        MIDDLEWARE.insert(MIDDLEWARE.index(_pf_sharedlogin), _pf_autologin)
    else:
        MIDDLEWARE.append(_pf_autologin)


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *
except ImportError:
    print("secret_settings.py file not found or failed to import.")
