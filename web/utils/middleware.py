"""Single-player front-end: auto-authenticate the local player.

Planetfall is served locally to a single player (see Phase 15 / settings).
Rather than show a login screen, this middleware silently authenticates the
browser session as the one persistent player account — the first superuser,
which owns the DECK-NINE character. Evennia's ``SharedLoginMiddleware`` (which
must run *after* this one) then propagates that authenticated Django session to
the webclient, so the served page opens already in-world with no connect prompt.

LOCAL SINGLE-PLAYER ONLY. Every visitor to this server is treated as THE
player. Do not enable on a public or multi-user deployment.
"""
from __future__ import annotations

from django.contrib.auth import authenticate, login

from evennia.accounts.models import AccountDB
from evennia.utils import logger


class SinglePlayerAutoLoginMiddleware:
    """Log every unauthenticated request in as the single player account."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        # AuthenticationMiddleware runs first and sets request.user; only act
        # when nobody is logged in yet.
        if user is not None and not user.is_authenticated:
            account = (
                AccountDB.objects.filter(is_superuser=True).order_by("id").first()
            )
            if account is not None:
                try:
                    # authenticate(autologin=...) sets the .backend attribute
                    # Django requires before login() will accept the account.
                    account = authenticate(autologin=account)
                    login(request, account)
                except Exception:
                    logger.log_trace()
        return self.get_response(request)
