# hotfixes/

Source tree for individual hotfix bundles (see `../make-hotfix-bundle.sh`
for what a hotfix actually is and its safety constraints — required-version
gating, root-owned files, live-rootfs bind-mount write-through, etc.).

One subdirectory per hotfix, named after the version it bumps *to*:

```
hotfixes/
  0.1.1/
    build.sh   — required-version/new-version + any prep, then calls
                 ../../make-hotfix-bundle.sh
    files/     — copied verbatim onto the device's root by the hotfix;
                 same layout as make-hotfix-bundle.sh's <files-dir> argument
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
