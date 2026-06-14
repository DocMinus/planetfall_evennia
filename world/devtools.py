"""Developer helpers for (re)building the single-player dev game.

Not part of the game itself — these back the `make reset-game` and `make
rebuild` targets. The dev DB is a single shared world + one GameState
singleton, so a "new playthrough" means wiping that world/state and re-placing
the player, and a "rebuild" means doing that on top of a freshly migrated DB.

Both entry points run inside `evennia shell` against a *stopped* server (a
running server caches world objects in the idmapper, so resetting underneath it
leaves stale state).
"""
from __future__ import annotations


def ensure_initial_setup() -> bool:
    """Run Evennia's initial_setup once, headlessly, if it hasn't run yet.

    A fresh migrated DB has no Account #1, no Limbo (#2) and no default
    channels; until initial_setup is *marked done* Evennia re-runs it on every
    boot, which would re-create a god character and re-bind the account each
    time — clobbering the player binding we set below.

    We call the DB-building steps directly and mark the sequence done ourselves,
    skipping the final `reset_server` step (it pokes the Portal, which isn't
    running in a `shell` context). Account #1 must already exist — `evennia
    migrate`/`shell` create it from the EVENNIA_SUPERUSER_* env vars before this
    runs. Returns True if setup was performed, False if it was already done.
    """
    from evennia.server.models import ServerConfig
    from evennia.server import initial_setup

    if ServerConfig.objects.conf("last_initial_setup_step") in ("done", -1):
        return False

    initial_setup.create_objects()      # Account #1 profile, god character, Limbo
    initial_setup.at_initial_setup()    # default channels + game hook
    ServerConfig.objects.conf("last_initial_setup_step", "done")
    return True


def reset_game(verbose: bool = True) -> object:
    """Wipe the world + GameState and re-place the superuser's character.

    Reloads every room/object/NPC from world/data/*.json, resets the GameState
    singleton to its defaults, then recreates the player Character at a pristine
    Deck Nine and binds it to Account #1 so `ic` puppets it. Returns the new
    Character.
    """
    from django.conf import settings
    from evennia import create_object
    from evennia.accounts.models import AccountDB

    from world.walkthrough import reload_world, _by_zil
    from world.scheduler_script import (
        get_game_state_script,
        get_or_create_game_state_script,
    )

    def _say(msg: str) -> None:
        if verbose:
            print(msg)

    # Re-attach to the first superuser (the dev account).
    acct = AccountDB.objects.filter(is_superuser=True).order_by("id").first()
    if acct is None:
        raise SystemExit("reset_game: no superuser account found — create one first.")

    # Detach the account's puppet references first: reload_world() deletes the
    # old Character, and a lingering _last_puppet/_playable_characters pointing
    # at the deleted row raises ObjectDB.DoesNotExist when next dereferenced.
    acct.db._last_puppet = None
    acct.db._playable_characters = []

    # 1. Wipe + reload the world from JSON (also deletes all Character objects).
    reload_world()
    _say("world reloaded from JSON")

    # 2. Reset the GameState singleton.
    script = get_game_state_script()
    if script is not None:
        script.delete()
    gs = get_or_create_game_state_script().get_gs()
    _say(f"game-state reset: score={gs.score} day={gs.day} moves={gs.internal_moves}")

    # 3. Recreate the player Character at a fresh Deck Nine, bound to the account.
    deck_nine = _by_zil("DECK-NINE")
    if deck_nine is None:
        raise SystemExit("reset_game: DECK-NINE missing — world failed to load.")

    char = create_object(
        settings.BASE_CHARACTER_TYPECLASS,
        key=acct.username,
        location=deck_nine,
        home=deck_nine,
    )
    char.locks.add(
        f"puppet:id({char.id}) or pid({acct.id}) or perm(Developer) or pperm(Developer)"
    )
    char.db.account = acct
    char.db.starter_items_given = False
    char._give_starter_items()
    char.db.starter_items_given = True
    acct.db._playable_characters = [char]
    acct.db._last_puppet = char
    char.save()
    acct.save()
    gs.player = char

    _say(
        f"character #{char.id} '{char.key}' for account '{acct.username}' "
        f"at {char.location}; carrying {[o.key for o in char.contents]}"
    )
    # Sanity: Deck Nine doors back to ZIL defaults (pod closed, corridor+gangway open).
    for zid in ("POD-DOOR", "CORRIDOR-DOOR", "GANGWAY-DOOR"):
        d = _by_zil(zid)
        _say(f"  {zid:14} is_open={d.db.is_open}")
    return char
