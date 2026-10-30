#!/usr/bin/env python3
"""Reverts slideadmin's password to the image's own when the boot-yaml
`slideadmin_password_hash` key has been removed.

Run by slide-announcer-shadow-revert.service, before /etc's overlay is
mounted (Before=etc.mount) — that's the only moment it's safe to touch the
overlay's upper layer directly: the kernel documents modifying an overlay's
upperdir while the overlay is mounted as undefined behavior (stale dentries
serving the old copied-up file). With the overlay not yet mounted, deleting
the upper copy of /etc/shadow simply makes the image's original show through
on the mount that follows.

provisioning/firstboot.py's sync_slideadmin_password() applies the hash later
in boot and drops MARKER once it has. We only ever delete the upper shadow
when that marker exists, so a device where nobody used this feature is never
touched (a plain `passwd` run at the console, for instance, survives).

Fails safe: yaml missing/unreadable/unparseable (boot partition not mounted,
mid-edit file) -> do nothing. Only a successfully parsed yaml that lacks the
key counts as "removed".
"""
import sys
from pathlib import Path

import yaml

BOOT_YAML = Path("/boot/firmware/slideannouncer.yaml")
MARKER = Path("/data/.slideadmin-password-managed")
UPPER_ETC = Path("/data/overlay/etc/upper")


def main() -> int:
    if not MARKER.exists():
        return 0

    try:
        data = yaml.safe_load(BOOT_YAML.read_text())
    except (OSError, yaml.YAMLError) as e:
        print(f"shadow-revert: can't read {BOOT_YAML} ({e}) — leaving shadow alone")
        return 0
    if not isinstance(data, dict):
        print("shadow-revert: boot yaml isn't a mapping — leaving shadow alone")
        return 0

    if data.get("slideadmin_password_hash"):
        return 0

    # shadow- is the backup copy usermod/chpasswd leave behind; it would
    # still carry the old hash.
    for name in ("shadow", "shadow-"):
        (UPPER_ETC / name).unlink(missing_ok=True)
    MARKER.unlink(missing_ok=True)
    print("shadow-revert: slideadmin_password_hash removed — image's /etc/shadow restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
