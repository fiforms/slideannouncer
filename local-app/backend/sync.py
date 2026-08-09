"""Slide sync daemon — STUB.

See SLIDE_ANNOUNCER.md, Tier 2 "Slide sync daemon": the real version polls
`GET /api/slide-announcers/slides` (now implemented server-side, see that
file's Part 1), diffs against a local manifest, downloads new/changed
media, and writes `sync-status.json` + `active-playlist.json` for the
kiosk frontend to render from. None of that exists yet — this module only
exists so `main.py` has a stable `read_status()` to call (mirrors
heartbeat.py's own read_status(), which *is* real) without the kiosk-facing
API shape changing again once sync is actually built.
"""


def read_status() -> dict:
    return {
        "implemented": False,
        "message": "Slide sync is not implemented yet — this device only reports status, it does not display slides.",
    }
