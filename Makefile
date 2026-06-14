# Put the venv's bin on PATH so the Portal can find `twistd` when it forks
# (`make start`/`restart` fail with "No such file or directory: 'twistd'" otherwise).
VENV_BIN := $(CURDIR)/.venv/bin
EVENNIA := PATH="$(VENV_BIN):$$PATH" $(VENV_BIN)/evennia

# Dev superuser used by the non-interactive `rebuild` target (override on the
# CLI, e.g. `make rebuild DEV_PASSWORD=secret`). Throwaway dev credentials.
# Evennia's check_database() auto-creates Account #1 from these EVENNIA_SUPERUSER_*
# env vars; without them a non-TTY first-run recurses trying to prompt for one.
DEV_USER ?= superuser
DEV_EMAIL ?= admin@example.com
DEV_PASSWORD ?= planetfall123
SU_ENV := EVENNIA_SUPERUSER_USERNAME="$(DEV_USER)" EVENNIA_SUPERUSER_EMAIL="$(DEV_EMAIL)" EVENNIA_SUPERUSER_PASSWORD="$(DEV_PASSWORD)"

.PHONY: start stop restart shell migrate load superuser test test-walkthrough reset-game rebuild

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

# Recover a fresh/wiped/drifted dev DB in one command:
#   1. back up any existing DB, then start from an empty one
#   2. migrate — check_database() auto-creates Account #1 from EVENNIA_SUPERUSER_*
#   3. one shell pass (scripts/rebuild_db.py): run initial_setup headlessly, then
#      load the Planetfall world from JSON and bind the player at Deck Nine
#   4. start the server
# Destructive: the current server/evennia.db3 is moved aside to evennia.db3.bak.
rebuild:
	-$(EVENNIA) stop
	@if [ -f server/evennia.db3 ]; then mv -f server/evennia.db3 server/evennia.db3.bak; echo "Backed up existing DB -> server/evennia.db3.bak"; fi
	rm -f server/evennia.db3-wal server/evennia.db3-shm
	$(SU_ENV) $(EVENNIA) migrate
	$(SU_ENV) $(EVENNIA) shell < scripts/rebuild_db.py
	$(EVENNIA) start
	@echo "Rebuild complete. Log in as '$(DEV_USER)' (password '$(DEV_PASSWORD)') and 'ic' to begin at Deck Nine."

# Full test suite (creates a throwaway test DB, loads the world from JSON).
test:
	$(EVENNIA) test --settings settings.py world

# Just the end-to-end walkthrough regression (DECK-NINE -> victory).
test-walkthrough:
	$(EVENNIA) test --settings settings.py world.tests.WalkthroughRegressionTest
