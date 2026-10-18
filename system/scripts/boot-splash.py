#!/usr/bin/env python3
"""Paint the boot splash image onto the framebuffer (/dev/fb0).

Runs from slide-announcer-splash.service, very early in boot (sysinit), so
the screen shows a logo instead of black until labwc/Chromium take over
(labwc's DRM modeset overwrites the framebuffer, which is what removes it).

The image is a pre-scaled raw file made at build time by
image-builder/splash-convert.py (no image library on the device); it is
centred on black at its own size, cropped if the screen is smaller.
Everything here is best-effort: any failure just leaves the screen black,
and the unit's own failure never blocks boot.

Something wipes the framebuffer after it is first painted (the firmware's
simpledrm fb being replaced by vc4's, the HDMI mode change, console resets)
— on real hardware the logo was gone within a second or so, ~10s before the
kiosk started. So this keeps repainting the (cached) frame every POLL seconds,
re-rendering if the fb's driver/mode changes, until the kiosk unit is active
(labwc then owns the display) or TIMEOUT passes.
"""
import struct
import subprocess
import sys
import time
from array import array
from pathlib import Path

IMAGE = Path(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/slide-announcer/splash.raw")
FB = Path("/sys/class/graphics/fb0")
DEV = "/dev/fb0"
# Upper bound on how long to keep repainting if the kiosk never starts.
TIMEOUT = 90.0
POLL = 0.5


def read(name):
    return (FB / name).read_text().strip()


def fb_state():
    """(name, width, height, bits_per_pixel, stride) or None if not ready."""
    try:
        w, h = (int(v) for v in read("virtual_size").split(","))
        return read("name"), w, h, int(read("bits_per_pixel")), int(read("stride"))
    except (OSError, ValueError):
        return None


def load_image():
    data = IMAGE.read_bytes()
    if data[:4] != b"SPL1":
        raise ValueError(f"{IMAGE} is not a splash raw file")
    w, h = struct.unpack_from("<II", data, 4)
    return w, h, data[12:12 + w * h * 3]


def to_rgb565(bgr):
    """B,G,R bytes -> little-endian RGB565 (what vc4's fb uses at 4K on a Pi 4)."""
    px = array("H", [((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                     for b, g, r in zip(bgr[0::3], bgr[1::3], bgr[2::3])])
    if sys.byteorder == "big":
        px.byteswap()
    return px.tobytes()


def render(state, image):
    """The full framebuffer contents: the image centred on black."""
    _name, fw, fh, bpp, stride = state
    if bpp not in (16, 24, 32):
        return None
    iw, ih, bgr = image
    # Source pixels as the framebuffer wants them, except 32bpp which is
    # expanded per row below.
    sp, data = (2, to_rgb565(bgr)) if bpp == 16 else (3, bgr)
    bytespp = bpp // 8
    # Crop to the screen, then centre.
    cw, ch = min(iw, fw), min(ih, fh)
    sx, sy = (iw - cw) // 2, (ih - ch) // 2   # source offset when cropping
    dx, dy = (fw - cw) // 2, (fh - ch) // 2   # destination offset
    black = b"\0" * stride
    rows = []
    for y in range(fh):
        iy = y - dy
        if not 0 <= iy < ch:
            rows.append(black)
            continue
        src = data[((sy + iy) * iw + sx) * sp:((sy + iy) * iw + sx + cw) * sp]
        if bpp == 32:
            px = bytearray(cw * 4)
            px[0::4], px[1::4], px[2::4] = src[0::3], src[1::3], src[2::3]
            src = bytes(px)
        row = bytearray(stride)
        row[dx * bytespp:(dx + cw) * bytespp] = src
        rows.append(bytes(row))
    return b"".join(rows)


def kiosk_started():
    """True once the kiosk unit is up: labwc owns the display from here."""
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", "slide-announcer-kiosk.service"], check=False
    ).returncode == 0


def main():
    image = load_image()
    deadline = time.monotonic() + TIMEOUT
    painted = None   # fb state the cached frame was rendered for
    frame = None
    unblanked = False
    while time.monotonic() < deadline:
        state = fb_state()
        if state and state != painted:
            frame = render(state, image)
            painted = state
        if frame:
            if not unblanked:
                # vc4drmfb has been seen at blank=4 (powerdown) with the splash painted.
                try:
                    (FB / "blank").write_text("0")
                except OSError:
                    pass
                unblanked = True
            try:
                with open(DEV, "wb") as fb:
                    fb.write(frame)
            except OSError:
                pass  # device not openable yet — try again next poll
        if kiosk_started():
            return
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never let the splash be the reason boot is unhappy
        print(f"boot-splash: {exc}", file=sys.stderr)
