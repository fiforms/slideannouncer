"""The escape hatch's "stay on Settings this once" flag.

system/scripts/escape-hatch.py normally redirects the kiosk's browser straight to
Settings. If the browser doesn't answer it restarts the kiosk instead, and the
fresh browser opens the local app's main screen — which, for a product whose main
screen is a remote site (docs/PRODUCTS.md), would send it right back to the very
page the person just escaped from. So before restarting, the script drops a flag
file; the product's launcher asks POST /api/local/escape-hold/consume whether to
stay local, and gets true exactly once.

The flag lives in /run (tmpfs: gone after a reboot) and is ignored once it is
older than MAX_AGE_SECONDS, so a restart nobody consumed can't hold the launcher
back later. See docs/ESCAPE_HATCH.md.
"""
import time
from pathlib import Path

HOLD_FLAG = Path("/run/slide-announcer/escape-hatch-hold")
MAX_AGE_SECONDS = 300


def consume_hold() -> bool:
    """True if the escape hatch left a fresh flag; clears it either way."""
    try:
        age = time.time() - HOLD_FLAG.stat().st_mtime
    except OSError:
        return False
    try:
        HOLD_FLAG.unlink()
    except OSError:
        pass
    return age <= MAX_AGE_SECONDS
