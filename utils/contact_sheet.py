#!/usr/bin/env python3
"""contact_sheet.py OUT.png img1 img2 ... [--cols 4 --width 480]  (labels = frame numbers)
Also prints per-image stats: mean luma, % of pixels clipped (>=254 on all channels), alpha coverage."""
import re
import sys

import numpy as np
from PIL import Image, ImageDraw

args = sys.argv[1:]
cols, tw = 4, 480
if "--cols" in args:
    i = args.index("--cols"); cols = int(args[i + 1]); del args[i:i + 2]
if "--width" in args:
    i = args.index("--width"); tw = int(args[i + 1]); del args[i:i + 2]
out, files = args[0], args[1:]
th = int(tw * 9 / 16)
rows = (len(files) + cols - 1) // cols
sheet = Image.new("RGB", (cols * tw, rows * (th + 18)), (40, 40, 40))
checker = Image.new("RGB", (tw, th))
cd = ImageDraw.Draw(checker)
for y in range(0, th, 16):
    for x in range(0, tw, 16):
        cd.rectangle([x, y, x + 15, y + 15], fill=(90, 90, 90) if (x // 16 + y // 16) % 2 else (60, 60, 60))
d = ImageDraw.Draw(sheet)
for n, f in enumerate(files):
    im = Image.open(f)
    a = np.asarray(im.convert("RGBA")).astype(np.float32)
    rgb = a[..., :3]
    luma = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]).mean()
    clip = (rgb.min(axis=2) >= 254).mean() * 100
    cov = (a[..., 3] > 2).mean() * 100
    m = re.search(r"(\d+)\.png$", f)
    label = "f%s  L%.0f  clip%.1f%%  a%.0f%%" % (m.group(1) if m else n, luma, clip, cov)
    print(f, label)
    t = im.convert("RGBA").resize((tw, th), Image.LANCZOS)
    base = checker.copy()
    base.paste(t, (0, 0), t)
    x, y = (n % cols) * tw, (n // cols) * (th + 18)
    sheet.paste(base, (x, y))
    d.text((x + 4, y + th + 3), label, fill=(230, 230, 230))
sheet.save(out)
