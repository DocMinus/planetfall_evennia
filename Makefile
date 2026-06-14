# Put the venv's bin on PATH so the Portal can find `twistd` when it forks
# (`make start`/`restart` fail with "No such file or directory: 'twistd'" otherwise).
VENV_BIN := $(CURDIR)/.venv/bin
EVENNIA := PATH="$(VENV_BIN):$$PATH" $(VENV_BIN)/evennia

.PHONY: start stop restart shell migrate load superuser test test-walkthrough reset-game

start:
	$(EVENNIA) start

stop:
	$(EVENNIA) stop

restart:
	$(EVENNIA) restart

shell:
	$(EVENNIA) shell

migrate:
	$(EVENNIA) migrate

load:
	$(EVENNIA) shell -c "from world.loader import load_world; load_world()"

superuser:
	$(EVENNIA) createsuperuser

# Dev helper: reset the persistent game to a pristine Deck Nine start.
# Stops the server (so it doesn't hold stale cached objects), wipes+reloads the
# world and game-state, re-places the superuser character, then restarts.
reset-game:
	-$(EVENNIA) stop
	$(EVENNIA) shell < scripts/reset_game.py
	$(EVENNIA) start

# Full test suite (creates a throwaway test DB, loads the world from JSON).
test:
	$(EVENNIA) test --settings settings.py world

# Just the end-to-end walkthrough regression (DECK-NINE -> victory).
test-walkthrough:
	$(EVENNIA) test --settings settings.py world.tests.WalkthroughRegressionTest
