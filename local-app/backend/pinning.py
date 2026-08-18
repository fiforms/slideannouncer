"""On-device show-selection persistence — which show (see sync.py's
per-show manifest) this kiosk should play, overriding the Main-show
default. Mirrors pairing.py's atomic-write convention (temp file +
os.replace) already used for its single-value status files (device-name,
language, etc.).

There is no per-device server-assigned show — selection is entirely
local to the kiosk, made via the Menu overlay and persisted here. Not
reported back to the server; see MULTI_SHOW_IMPLEMENTATION.md.
"""
import os
from pathlib import Path

PINNED_SHOW_ID_FILE = Path("/data/status/pinned-show-id")


def read_pinned_show_id() -> str | None:
    if not PINNED_SHOW_ID_FILE.exists():
        return None
    return PINNED_SHOW_ID_FILE.read_text().strip() or None


def write_pinned_show_id(show_id: str | None) -> None:
    if show_id is None:
        PINNED_SHOW_ID_FILE.unlink(missing_ok=True)
        return
    PINNED_SHOW_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PINNED_SHOW_ID_FILE.with_suffix(PINNED_SHOW_ID_FILE.suffix + ".tmp")
    tmp.write_text(show_id)
    tmp.chmod(0o644)
    os.replace(tmp, PINNED_SHOW_ID_FILE)
