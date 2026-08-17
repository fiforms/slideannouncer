#!/usr/bin/env python3
"""Gates whether sshd actually starts on `ssh_enabled: true` in
/boot/firmware/slideannouncer.yaml.

ssh.service itself is unconditionally `systemctl enable`d in every image
now (image-builder/config's ENABLE_SSH=1) — this is the only thing that
decides whether it actually starts, via ssh.service.d/slide-announcer-
gate.conf's ExecStartPre=, checked fresh on every start attempt (not just
once at boot). Absent/false/malformed yaml -> refuse to start (exit
non-zero, which systemd reports as ssh.service failing to start rather
than retrying) — the same fail-closed default the old build-time
ENABLE_SSH=0 gave, just decided at runtime instead of at image-build time.
Flip it by editing the yaml and restarting (or rebooting) — no rebuild/
reflash needed, same as ssh_authorized_keys
(provisioning/firstboot.py) and vt_lockswitch (vtlock-apply.py).
"""
import sys
from pathlib import Path

import yaml

BOOT_YAML = Path("/boot/firmware/slideannouncer.yaml")


def main() -> int:
    if not BOOT_YAML.exists():
        print("slide-announcer-ssh-gate: no slideannouncer.yaml on the boot partition — refusing to start sshd")
        return 1

    data = yaml.safe_load(BOOT_YAML.read_text()) or {}
    if data.get("ssh_enabled") is not True:
        print("slide-announcer-ssh-gate: ssh_enabled is not set to true in slideannouncer.yaml — refusing to start sshd")
        return 1

    print("slide-announcer-ssh-gate: ssh_enabled: true — allowing sshd to start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
