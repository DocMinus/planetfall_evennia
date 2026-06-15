"""
This reroutes from an URL to a python view-function/class.

The main web/urls.py includes these routes for all urls (the root of the url)
so it can reroute to all website pages.

"""

from django.urls import path

from evennia.web.website.urls import urlpatterns as evennia_website_urlpatterns
from evennia.web.webclient.views import webclient as webclient_view

# Single-player front-end (Phase 15): serve the game client at the site root so
# opening the URL drops straight into Planetfall — no website landing page in
# between. Listed first, so it wins over Evennia's default website index.
urlpatterns = [
    path("", webclient_view, name="index"),
]

# read by Django
urlpatterns = urlpatterns + evennia_website_urlpatterns
