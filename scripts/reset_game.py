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

The reset logic lives in world/devtools.reset_game() (shared with the fresh-DB
`make rebuild` path); this file is just the shell entry point.
"""
from world.devtools import reset_game

reset_game()
print("reset complete — `make start`, then log in and `ic`.")
