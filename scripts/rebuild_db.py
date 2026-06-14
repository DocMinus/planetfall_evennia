"""Dev convenience: bootstrap a freshly migrated DB into a playable game.

This is the world-building half of `make rebuild` (which handles the
migrate/superuser/start around it). Run inside `evennia shell` against a
*stopped* server, on a DB that has been migrated and has Account #1 (created
from the EVENNIA_SUPERUSER_* env vars):

    make rebuild

It runs Evennia's initial_setup once (Limbo, default channels, the bookkeeping
that stops setup re-running and clobbering the player binding on each boot),
then loads the Planetfall world and binds the player at Deck Nine — the same
reset logic `make reset-game` uses.
"""
from world.devtools import ensure_initial_setup, reset_game

if ensure_initial_setup():
    print("initial_setup complete (Account #1 profile, Limbo, channels)")
else:
    print("initial_setup already done — skipping")

reset_game()
print("rebuild complete — `make start`, then log in and `ic`.")
