EVENNIA := PATH="/Users/a/.local/share/uv/tools/evennia/bin:$$PATH" evennia

.PHONY: start stop restart shell migrate

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
