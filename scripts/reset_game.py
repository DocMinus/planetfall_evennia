"""Dev convenience: reset the persistent game to a pristine start.

This is a throwaway development helper, NOT part of the game. The dev DB is a
single shared world + a single GameState singleton, so a "new playthrough" means
wiping that world/state and re-placing the player — not making a new character.

Run it against a *stopped* server (the running server caches objects, so reset
while it's up and you'll get stale state):

    make stop
    .venv/bin/evennia shell < scripts/reset_game.py
    make start

Or just use the one-shot wrapper, which stops/resets/starts for you:

    make reset-game

What it does:
  1. Wipes every Planetfall room/object/NPC (and all Character objects) and
     reloads the world fresh from world/data/*.json.
  2. Deletes + recreates the GameState singleton (score/day/flags/interrupts
     back to defaults).
  3. Recreates the superuser's player Character at a clean Deck Nine, hands it
     the ZIL starter items, and re-binds it to the account so `ic` puppets it.
"""
from django.conf import settings
from evennia import create_object
from evennia.accounts.models import AccountDB

from world.walkthrough import reload_world, _by_zil
from world.scheduler_script import (
    get_game_state_script,
    get_or_create_game_state_script,
)

# The account to re-attach the fresh character to: the first superuser (the dev
# account). Adjust if you play as a different account.
acct = AccountDB.objects.filter(is_superuser=True).order_by("id").first()
if acct is None:
    raise SystemExit("reset_game: no superuser account found — create one first.")

# Detach the account's puppet references first: reload_world() deletes the old
# Character, and a lingering _last_puppet/_playable_characters pointing at the
# deleted row raises ObjectDB.DoesNotExist when next dereferenced.
acct.db._last_puppet = None
acct.db._playable_characters = []

# 1. Wipe + reload the world from JSON (also deletes all Character objects).
reload_world()
print("world reloaded from JSON")

# 2. Reset the GameState singleton.
script = get_game_state_script()
if script is not None:
    script.delete()
gs = get_or_create_game_state_script().get_gs()
print(f"game-state reset: score={gs.score} day={gs.day} moves={gs.internal_moves}")

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

print(
    f"character #{char.id} '{char.key}' for account '{acct.username}' "
    f"at {char.location}; carrying {[o.key for o in char.contents]}"
)
# Sanity: Deck Nine doors back to ZIL defaults (pod closed, corridor+gangway open).
for zid in ("POD-DOOR", "CORRIDOR-DOOR", "GANGWAY-DOOR"):
    d = _by_zil(zid)
    print(f"  {zid:14} is_open={d.db.is_open}")
print("reset complete — `make start`, then log in and `ic`.")
