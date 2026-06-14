EVENNIA := .venv/bin/evennia

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
