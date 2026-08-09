#!/usr/bin/env python3
"""Runs `slide-announcer-update check` and records the result as JSON at
/data/status/update-check.json.

This exists as a separate step, rather than having the backend call
`slide-announcer-update` directly, because the backend runs unprivileged
(the `slideannouncer` user) while `slide-announcer-update` needs root. This
script itself runs as root, inside the on-demand
slide-announcer-update-check.service unit — the backend only ever gets
permission (via polkit, see system/polkit/50-slide-announcer-system.rules)
to *start* that unit, never to run arbitrary root commands directly. See
local-app/backend/system_control.py for the other half of this.

Deliberately always exits 0, regardless of whether the check itself
succeeded — the check's own pass/fail (e.g. "not paired yet," today's
expected result) is reported through the status file's content, not the
unit's exit status, so `systemctl start` doesn't report a false "failed."
"""
import json
import subprocess
import sys
from pathlib import Path

STATUS_FILE = Path("/data/status/update-check.json")


def main() -> int:
    result = subprocess.run(
        ["/usr/local/sbin/slide-announcer-update", "check"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps({
        "exit_code": result.returncode,
        "output": (result.stdout + result.stderr).strip(),
    }))
    # Explicit chmod, not just whatever this service's umask happens to
    # produce — the (unprivileged) backend reads this file back, same
    # lesson as local-app-seed.py's directory permissions.
    STATUS_FILE.chmod(0o644)
    return 0


if __name__ == "__main__":
    sys.exit(main())
