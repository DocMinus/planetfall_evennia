# Put the venv's bin on PATH so the Portal can find `twistd` when it forks
# (`make start`/`restart` fail with "No such file or directory: 'twistd'" otherwise).
VENV_BIN := $(CURDIR)/.venv/bin
EVENNIA := PATH="$(VENV_BIN):$$PATH" $(VENV_BIN)/evennia

.PHONY: start stop restart shell migrate load superuser test test-walkthrough

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

# Full test suite (creates a throwaway test DB, loads the world from JSON).
test:
	$(EVENNIA) test --settings settings.py world

# Just the end-to-end walkthrough regression (DECK-NINE -> victory).
test-walkthrough:
	$(EVENNIA) test --settings settings.py world.tests.WalkthroughRegressionTest
