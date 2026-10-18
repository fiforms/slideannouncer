#!/usr/bin/env python3
"""Build-host tool: convert a splash PNG into the raw file boot-splash.py reads.

    splash-convert.py <in.png> <out.raw>

The device has no image library (hotfixes can't install packages), so all
decoding and scaling happens here. The image is fitted inside MAX_W x MAX_H
(never upscaled) and stored as: b"SPL1", width, height (uint32 LE), then
width*height*3 bytes of B,G,R. boot-splash.py centres it on black as-is.
Needs Pillow on the build host.
"""
import struct
import sys

from PIL import Image

MAX_W, MAX_H = 960, 640


def main():
    src, dst = sys.argv[1:3]
    img = Image.open(src).convert("RGBA")
    # Flatten any transparency onto black, matching the screen behind it.
    flat = Image.new("RGB", img.size, (0, 0, 0))
    flat.paste(img, mask=img.getchannel("A"))
    scale = min(1.0, MAX_W / flat.width, MAX_H / flat.height)
    if scale < 1.0:
        flat = flat.resize((max(1, round(flat.width * scale)), max(1, round(flat.height * scale))), Image.LANCZOS)
    with open(dst, "wb") as f:
        f.write(b"SPL1" + struct.pack("<II", *flat.size))
        f.write(flat.tobytes("raw", "BGR"))


if __name__ == "__main__":
    main()
