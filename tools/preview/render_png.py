#!/usr/bin/env python3
"""Turn raw OLED frames into the images used in the docs.

Input is the 1024-byte-per-frame format written by frames.cpp and
claude_screens.cpp (and served by the device at GET /api/frame). Output uses
the same look as docs/images/eyes.gif: lit pixels in the page's --pixel
colour (226, 243, 255) on black, scaled 3x.

Usage (from the repository root; needs Pillow, a development-only tool):
    python tools/preview/render_png.py gif  build/claude.bin docs/images/claude.gif --ms 125
    python tools/preview/render_png.py grid build/claude-gallery.bin docs/images/claude-screens.png --cols 3
"""
import argparse
import pathlib
import sys

FRAME = 1024
LIT = (226, 243, 255)
SCALE = 3


def frames(path: pathlib.Path):
    data = path.read_bytes()
    if len(data) % FRAME:
        sys.exit(f"{path}: size {len(data)} is not a multiple of {FRAME}")
    return [data[i:i + FRAME] for i in range(0, len(data), FRAME)]


def to_image(fb: bytes):
    from PIL import Image
    img = Image.new("RGB", (128, 64))
    px = img.load()
    for y in range(64):
        for x in range(128):
            if (fb[(y >> 3) * 128 + x] >> (y & 7)) & 1:
                px[x, y] = LIT
    return img.resize((128 * SCALE, 64 * SCALE), Image.NEAREST)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=("gif", "grid"))
    ap.add_argument("src", type=pathlib.Path)
    ap.add_argument("dst", type=pathlib.Path)
    ap.add_argument("--ms", type=int, default=125, help="frame time for gif")
    ap.add_argument("--cols", type=int, default=3, help="columns for grid")
    ap.add_argument("--gap", type=int, default=12, help="pixels between grid cells")
    args = ap.parse_args()
    try:
        from PIL import Image
    except ImportError:
        sys.exit("needs Pillow: pip install pillow")

    imgs = [to_image(fb) for fb in frames(args.src)]
    if args.kind == "gif":
        imgs[0].save(args.dst, save_all=True, append_images=imgs[1:], duration=args.ms, loop=0, optimize=True)
    else:
        w, h = imgs[0].size
        rows = (len(imgs) + args.cols - 1) // args.cols
        sheet = Image.new("RGB", (args.cols * w + (args.cols - 1) * args.gap, rows * h + (rows - 1) * args.gap),
                          (40, 44, 52))
        for i, im in enumerate(imgs):
            sheet.paste(im, ((i % args.cols) * (w + args.gap), (i // args.cols) * (h + args.gap)))
        sheet.save(args.dst, optimize=True)
    print(f"wrote {args.dst} ({len(imgs)} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
