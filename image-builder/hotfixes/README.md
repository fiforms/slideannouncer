# hotfixes/

Source tree for individual hotfix bundles (see `../make-hotfix-bundle.sh`
for what a hotfix actually is and its safety constraints — required-version
gating, root-owned files, live-rootfs bind-mount write-through, etc.).

## Versions are pairs

A device's OS version is `<platform>_<product>` (e.g. `0.4.1_0.1.0`: this
repo's `image-builder/VERSION` plus the product's `image/VERSION`, see
`docs/PRODUCTS.md`), and a hotfix gates on, and bumps, that pair exactly. A
platform change is `0.4.1_0.1.0` -> `0.4.2_0.1.0`; a product-only change is
`0.4.1_0.1.0` -> `0.4.1_0.1.1` (and lives in the product, see its
`hotfixes/`). Devices from before pairs report a plain `0.4.0`, so the
`0.4.1` hotfix below is built from `0.4.0`. Build platform hotfixes with
`PRODUCT_ROOT` set (they read the product's `image/VERSION` for the pair), and
name the directory after the platform part of the version it bumps *to*.

One subdirectory per hotfix, named after the version it bumps *to*:

```
hotfixes/
  0.1.1/
    build.sh   — required-version/new-version + any prep, then calls
                 ../../make-hotfix-bundle.sh
    files/     — copied verbatim onto the device's root by the hotfix;
                 same layout as make-hotfix-bundle.sh's <files-dir> argument
    script.sh  — optional; passed as make-hotfix-bundle.sh's 4th argument.
                 Runs once on-device after files/ is extracted and before
                 VERSION is bumped, for anything a file copy alone can't do
                 (systemctl enable/disable a unit, delete a file the hotfix
                 is retiring). Not chrooted — runs on the live device shell
                 against $ROOT (the patched rootfs's bind-mount), so target
                 it explicitly: `systemctl --root="$ROOT" enable foo.service`,
                 `rm -f "$ROOT/etc/..."`, never the live /etc directly.
```

To build a hotfix, run its `build.sh` (needs `RAUC_CERT_PATH`/
`RAUC_KEY_PATH` set via `../.env`, same as `make-hotfix-bundle.sh` and
`build.sh` at the image-builder root). Output lands in `../deploy/` as
`slideannouncer-<new-version>.hotfix.from.<required-version>.raucb`.

Keep each hotfix's `files/` limited to what it's actually patching — a
hotfix is a surgical, un-A/B-tested fix (see `make-hotfix-bundle.sh`'s
header), not a place to accumulate unrelated changes. If a hotfix needs a
directory that must land empty on the device, create it in `build.sh`
right before calling `make-hotfix-bundle.sh` rather than committing a
placeholder file into `files/` — git can't track empty directories, and a
placeholder would defeat the "empty" part.

Every file under a hotfix's `files/` lands on-device owned by `root:root`
regardless of who built the bundle — `make-hotfix-bundle.sh` forces this at
tar time (`--owner=0 --group=0 --numeric-owner`), since the on-device hook
always extracts as root onto a root-owned rootfs.

## Never change `/`'s mode (the `./` tar entry)

The bundle's `files.tar.gz` is built from `<files-dir>` as `tar -C … .`, so it
contains a `./` entry, and the on-device hook extracts it onto `/`. Whatever
mode that entry has becomes the **device's root directory mode**. If it is
`0700` (what `mktemp -d` creates, and what `cp -a dir/. stage/` copies onto
the stage dir), every non-root service — dbus-daemon, timesyncd, avahi,
bluetooth — can no longer traverse `/`. The device then boots to a wall of
`[FAILED] Failed to start dbus.service`, `systemd-timesyncd`, `logind`,
`rauc`, and all the slide-announcer units with `[DEPEND]` failures, even though
`e2fsck` reports the slot clean. The first 0.4.1 hotfix did exactly this.

Rules:
- `make-hotfix-bundle.sh` now does `chmod 755` on its staging copy and aborts
  if the tarball's `./` entry isn't `drwxr-xr-x`. Don't remove either.
- If a `build.sh` stages files in a `mktemp -d`, `chmod 755` it first.
- Don't ship any other top-level directory entry with an unusual mode
  (`/usr`, `/opt`, `/etc` ...): create intermediate dirs with `install -D` or
  `mkdir -p` (0755), and check `tar -tvzf` on the built bundle's files.tar.gz
  before deploying.
- Recovery if it happens: mount the affected slot's rootfs on another machine
  and `chmod 755` its top directory. The other A/B slot is untouched, since a
  hotfix only patches the booted slot.
