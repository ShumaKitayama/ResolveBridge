#!/usr/bin/env python3
"""Automatic checks on the rendered sequences.

  verify_frames.py renders/frames renders/alpha_png [--mp4 renders/FCS_preview.mp4] [--mov renders/FCS_alpha_prores4444.mov]

Checks: frame count / gaps, black frames, clipping (white-out) per frame, frame-to-frame jumps,
alpha edge consistency (premultiplied RGB must not exceed alpha), residual light on the last frame,
and (optionally) that the encoded videos decode to the same number of frames and match the PNGs.
"""
import glob
import json
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image

FF = None
try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    pass


def frames(d):
    fs = sorted(glob.glob(os.path.join(d, "*.png")))
    nums = [int(re.search(r"(\d+)\.png$", f).group(1)) for f in fs]
    return fs, nums


def load(f, mode="RGBA"):
    return np.asarray(Image.open(f).convert(mode)).astype(np.float32) / 255.0


def luma(rgb):
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def decode(path, n_expected):
    """Decode a video with the bundled ffmpeg into raw RGB(A) frames (downscaled 1/4 for speed)."""
    pix = "rgba"
    cmd = [FF, "-v", "error", "-i", path, "-vf", "scale=480:270", "-f", "rawvideo", "-pix_fmt", pix, "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    arr = np.frombuffer(raw, np.uint8).reshape(-1, 270, 480, 4).astype(np.float32) / 255.0
    return arr


def small(a):
    return np.asarray(Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8)).resize((480, 270), Image.BILINEAR)).astype(np.float32) / 255.0


def main(argv):
    args = argv[1:]
    opts = {}
    for k in ("--mp4", "--mov", "--bg"):
        if k in args:
            i = args.index(k); opts[k] = args[i + 1]; del args[i:i + 2]
    prev_dir, alpha_dir = args[0], args[1]
    rep = {}
    pf, pn = frames(prev_dir)
    af, an = frames(alpha_dir)
    rep["preview_frames"] = len(pf)
    rep["alpha_frames"] = len(af)
    N = max(pn) + 1 if pn else 0
    rep["preview_gaps"] = sorted(set(range(0, N)) - set(pn))
    rep["alpha_gaps"] = sorted(set(range(0, N)) - set(an))

    L, clip, diff, prev_small = [], [], [], None
    small_prev = []
    for f in pf:
        a = load(f, "RGB")
        L.append(float(luma(a).mean()))
        clip.append(float((a.min(axis=2) >= 0.995).mean() * 100))
        s = small(a)
        small_prev.append(s)
        if prev_small is not None:
            diff.append(float(np.abs(s - prev_small).mean()))
        prev_small = s
    rep["black_frames"] = [pn[i] for i, v in enumerate(L) if v < 0.005]
    rep["max_clip_pct"] = round(max(clip), 3)
    rep["max_clip_frame"] = pn[int(np.argmax(clip))]
    rep["frames_clip_over_5pct"] = [pn[i] for i, v in enumerate(clip) if v > 5]
    d = np.array(diff)
    med = float(np.median(d))
    rep["frame_diff_median"] = round(med, 5)
    # a "jump" = change far above its neighbours (not explained by gradual motion)
    jumps = []
    for i in range(len(d)):
        nb = np.concatenate([d[max(0, i - 3):i], d[i + 1:i + 4]])
        if len(nb) and d[i] > 4 * max(float(np.median(nb)), 1e-4) and d[i] > 0.01:
            jumps.append({"between": [pn[i], pn[i + 1]], "diff": round(float(d[i]), 4)})
    rep["jumps"] = jumps
    if N == 120:
        rep["mean_luma_by_phase"] = {
            name: round(float(np.mean(L[a:b + 1])), 4) for name, (a, b) in
            {"0-15": (0, 15), "16-31": (16, 31), "32-47": (32, 47), "48-95": (48, 95), "96-119": (96, 119)}.items()}

    # alpha checks (PNG = straight alpha): composite over the preview background and compare with the preview
    bgf = opts.get("--bg") or "none"
    bg = load(bgf, "RGB") if (bgf != "none" and os.path.exists(bgf)) else None
    comp_err, cov, fringe = [], [], []
    for i, f in enumerate(af):
        a = load(f)
        rgb, al = a[..., :3], a[..., 3:4]
        cov.append(float((al > 1 / 65535).mean() * 100))
        # dark fringe: semi-transparent edge pixels whose straight colour is much darker than the core colour
        edge = (al[..., 0] > 0.02) & (al[..., 0] < 0.3)
        fringe.append(float((rgb.max(axis=2)[edge] < 0.2).mean() * 100) if edge.any() else 0.0)
        if bg is not None and i % 10 == 0:
            over = rgb * al + bg * (1 - al)
            prev = load(pf[i], "RGB")
            comp_err.append(float(np.abs(over - prev).mean()))
    rep["alpha_edge_dark_fringe_max_pct"] = round(max(fringe), 4)
    if comp_err:
        rep["alpha_over_bg_vs_preview_mean_abs_err_max"] = round(max(comp_err), 5)
    rep["alpha_coverage_first_last"] = [round(cov[0], 3), round(cov[-1], 3)]
    last = load(af[-1])
    rep["last_frame_max_alpha"] = round(float(last[..., 3].max()), 5)

    if FF:
        if "--mp4" in opts:
            v = decode(opts["--mp4"], 120)
            rep["mp4_frames"] = int(v.shape[0])
            n = min(len(v), len(small_prev))
            err = [float(np.abs(v[i, ..., :3] - small_prev[i]).mean()) for i in range(n)]
            rep["mp4_vs_png_mean_abs_err_max"] = round(max(err), 4)
            rep["mp4_black_frames"] = [i for i in range(len(v)) if luma(v[i, ..., :3]).mean() < 0.005]
        if "--mov" in opts:
            v = decode(opts["--mov"], 120)
            rep["mov_frames"] = int(v.shape[0])
            rep["mov_alpha_max_first_mid_last"] = [round(float(v[i, ..., 3].max()), 3) for i in (0, 60, len(v) - 1)]
            rep["mov_alpha_min_mid"] = round(float(v[60, ..., 3].min()), 3)
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main(sys.argv)
