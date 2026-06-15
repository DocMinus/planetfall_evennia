"""One-command single-player launch helper (backs `make play`).

The Makefile starts the Evennia server (Portal + Server) just before invoking
this script. Evennia daemonises and returns immediately, well before the web
server is actually accepting connections, so here we poll the web-client URL
until it answers, then open it in the default browser — dropping the player
straight onto the Planetfall title screen.
"""
from __future__ import annotations

import sys
import time
import urllib.request
import webbrowser

URL = "http://localhost:4001/"
TIMEOUT_SECONDS = 60


def _is_up() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def main() -> int:
    print("Waiting for Planetfall to come online...")
    for _ in range(TIMEOUT_SECONDS):
        if _is_up():
            print(f"Ready — opening {URL}")
            webbrowser.open(URL)
            return 0
        time.sleep(1)
    print(
        f"Timed out after {TIMEOUT_SECONDS}s waiting for the web server at {URL}.\n"
        "Is the server running? Try `make start` and check server/logs/.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
