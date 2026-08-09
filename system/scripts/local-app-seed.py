#!/usr/bin/env python3
"""Seed or upgrade /data/local-app from the release tarball baked into this
OS image at /opt/slide-announcer/local-app-release/ — never downgrades.
See local-app/README.md, "Installation on the device," for the full design.

Runs every boot (slide-announcer-local-app-seed.service), before the
backend/kiosk services start:
- No local-app on /data at all (fresh card, or /data was wiped) -> extract
  the embedded release, unconditionally.
- Installed version older than the embedded one (only ever true right after
  a RAUC OS update ships a newer local-app than what's on /data) -> extract
  and switch over.
- Installed version >= embedded -> leave it alone. This is what makes a
  RAUC OS update never clobber a newer app version a live device already
  picked up from the (not yet built) OTA app updater.

Only local-app's own X.Y.Z (local-app/VERSION) is ever compared — not the
git-hash suffix — so rebuilding the image without bumping that file is
correctly treated as "not newer," not re-seeded on every single boot.
"""
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

RELEASE_TARBALL = Path("/opt/slide-announcer/local-app-release/local-app.tar.gz")
RELEASE_VERSION_FILE = Path("/opt/slide-announcer/local-app-release/VERSION")
DATA_LOCAL_APP = Path("/data/local-app")
CURRENT_LINK = DATA_LOCAL_APP / "current"
RELEASES_DIR = DATA_LOCAL_APP / "releases"


def log(msg: str) -> None:
    print(f"local-app-seed: {msg}", flush=True)


def version_core(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def installed_version() -> str | None:
    version_file = CURRENT_LINK / "VERSION"
    if not version_file.exists():
        return None
    return version_file.read_text().strip()


def extract_release(version: str) -> None:
    target_dir = RELEASES_DIR / version
    if target_dir.exists():
        # A previous boot extracted this release but was interrupted before
        # the symlink swap below — reuse it instead of re-extracting.
        log(f"release {version} already extracted at {target_dir}")
    else:
        tmp_dir = RELEASES_DIR / f".{version}.tmp"
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True)
        # No `filter=` kwarg: this tarball is our own build output, baked
        # into our own rootfs at image-build time — not third-party input,
        # so path-traversal filtering isn't defending against anything here.
        with tarfile.open(RELEASE_TARBALL) as tar:
            tar.extractall(tmp_dir)
        # This service runs as root (it has to, to write under /data before
        # the backend/kiosk services — which run as `slideannouncer` — ever
        # start) — hand the extracted tree over so the app runs with
        # consistent ownership, matching the venv's own chown in 00-run.sh.
        subprocess.run(["chown", "-R", "slideannouncer:slideannouncer", str(tmp_dir)], check=True)
        tmp_dir.rename(target_dir)
        log(f"extracted embedded release {version} to {target_dir}")

    # ln -sfn-style atomic swap: build the new link next to the old one,
    # then rename over it — readers never see a half-updated symlink.
    tmp_link = DATA_LOCAL_APP / ".current.tmp"
    tmp_link.unlink(missing_ok=True)
    tmp_link.symlink_to(target_dir, target_is_directory=True)
    tmp_link.replace(CURRENT_LINK)
    log(f"switched current -> releases/{version}")


def main() -> int:
    if not RELEASE_TARBALL.exists():
        log(f"no embedded release at {RELEASE_TARBALL} — nothing to do")
        return 0

    embedded_version = RELEASE_VERSION_FILE.read_text().strip()
    embedded_core = version_core(embedded_version)
    if embedded_core is None:
        log(f"embedded VERSION '{embedded_version}' doesn't parse as X.Y.Z[-...] — refusing to seed")
        return 1

    RELEASES_DIR.mkdir(parents=True, exist_ok=True)

    current = installed_version()
    if current is None:
        log(f"no local-app installed on /data — seeding embedded release {embedded_version}")
        extract_release(embedded_version)
        return 0

    current_core = version_core(current)
    if current_core is not None and current_core >= embedded_core:
        log(f"installed local-app {current} is already >= embedded {embedded_version} — leaving it")
        return 0

    log(f"installed local-app {current} is older than embedded {embedded_version} — upgrading")
    extract_release(embedded_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
