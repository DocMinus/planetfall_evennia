"""
At_initial_setup module template

Custom at_initial_setup method. This allows you to hook special
modifications to the initial server startup process. Note that this
will only be run once - when the server starts up for the very first
time! It is called last in the startup process and can thus be used to
overload things that happened before it.

The module must contain a global function at_initial_setup().  This
will be called without arguments. Note that tracebacks in this module
will be QUIETLY ignored, so make sure to check it well to make sure it
does what you expect it to.

"""


def at_initial_setup():
    # Runs exactly once, after Evennia has created Limbo (#2) and the
    # superuser — safe to build the game world here. Tracebacks from this
    # hook are swallowed by Evennia, so report failures explicitly.
    from evennia.utils import logger

    from world.loader import load_world

    try:
        load_world()
    except Exception:
        logger.log_trace(
            "Initial world load failed. The database was left untouched; "
            "fix the problem and run 'make load' to load the world manually."
        )
