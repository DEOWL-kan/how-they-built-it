#!/usr/bin/env python3
"""Probe a static image for how it was actually made.

Answers the questions you need before you can reproduce a competitor's
background/visual: is it a flat gradient or does it carry grain? where is the
light centred? what is the colour ramp? does text still pass contrast on it?

Requires ffmpeg/ffprobe on PATH. Deliberately stdlib-only otherwise — Pillow is
not installed on many machines and this has to work everywhere.

Usage:
    image_probe.py bg.png
    image_probe.py bg.png --grid 9x20          # gradient structure readout
    image_probe.py bg.png --contrast '#1E2C28,#5C756F'
    image_probe.py bg.png --hue                # HSL of sampled colours
"""
import argparse
import colorsys
import os
import statistics
import subprocess
import sys
import tempfile


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout.strip()


def require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            sys.exit(f"{tool} not found on PATH. Install ffmpeg first.")


def dimensions(path):
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,pix_fmt",
                "-of", "csv=p=0", path])
    w, h, pix = out.split(",")[:3]
    return int(w), int(h), pix


def raw_rgb(path, w=None, h=None):
    """Decode to raw RGB24, optionally rescaled. Returns (bytes, w, h)."""
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        tmp = f.name
    vf = f"scale={w}:{h}" if w and h else None
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", tmp, "-y"]
    _run(cmd)
    data = open(tmp, "rb").read()
    os.unlink(tmp)
    return data


def hexof(r, g, b):
    return "#%02X%02X%02X" % (r, g, b)


def luminance(hexv):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    l1, l2 = sorted((luminance(a), luminance(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def grain_report(data, w, h):
    """Horizontal neighbour deltas on the green channel.

    Why this matters: a PNG can be large for two very different reasons — it
    carries grain/texture, or it is a smooth diagonal gradient (which defeats
    PNG row filters). File size alone cannot tell you which. Neighbour deltas
    can: a flat gradient has almost no delta >= 3, real grain has plenty.
    """
    diffs = []
    step = max(1, h // 300)
    for y in range(0, h, step):
        base = y * w * 3
        for x in range(w - 1):
            diffs.append(abs(data[base + x * 3 + 1] - data[base + (x + 1) * 3 + 1]))
    if not diffs:
        return None
    mean = sum(diffs) / len(diffs)
    sd = statistics.pstdev(diffs)
    ge3 = sum(1 for v in diffs if v >= 3) / len(diffs)
    nonzero = sum(1 for v in diffs if v) / len(diffs)
    if ge3 < 0.02:
        verdict = "FLAT GRADIENT — no grain. Reproducible with pure CSS/Flutter gradients."
    elif ge3 < 0.25:
        verdict = "light grain / dithering present"
    else:
        verdict = "HEAVY GRAIN — this is a texture layer, not a gradient"
    return mean, sd, nonzero, ge3, verdict


def grid_report(path, gw, gh):
    """Downsample to a tiny grid and print it — reveals gradient geometry.

    Reading a 9x20 grid of hex values tells you instantly whether the light is
    centred or hugging an edge, and whether the bottom shifts neutral (a common
    trick: baking the button-area scrim straight into the background gradient).
    """
    data = raw_rgb(path, gw, gh)
    rows = []
    for y in range(gh):
        row = []
        for x in range(gw):
            i = (y * gw + x) * 3
            row.append(hexof(data[i], data[i + 1], data[i + 2]))
        rows.append(row)
    return rows, data


def saturation_peak(data, gw, gh):
    """Locate the cell with the strongest colour — the chroma peak.

    On a dark background that cell IS the light source. On a light background it
    is the deepest tint, which is the opposite end of the ramp — measured on a
    white-to-pale-blue sign-in background, the peak sat on the darkest blue.
    So report it as "chroma peak" and let the caller say which it is.

    Uses chroma (max channel - min channel), NOT HLS saturation. HLS saturation
    blows up near white: a near-white pixel with a faint tint reports ~79%
    saturation and hijacks the result, pointing you at the brightest corner
    instead of the actual colour centre. Chroma measures how far the pixel is
    from grey, which is what "where is the tint strongest" actually means.
    """
    best = None
    for y in range(gh):
        for x in range(gw):
            i = (y * gw + x) * 3
            r, g, b = data[i], data[i + 1], data[i + 2]
            chroma = max(r, g, b) - min(r, g, b)
            if best is None or chroma > best[0]:
                best = (chroma, x, y, hexof(r, g, b))
    return best


def background_extremes(cells):
    """Darkest/lightest of the background, ignoring foreground marks.

    p10/p90, not min/max. On a screenshot the darkest cell is the body text
    itself: measured on a real sign-in screen, min was the black headline
    (L=0.009) while p10 was L=0.236 — so #111111 text got scored against
    #16181A text and "FAILED" contrast against itself. Returns
    (darkest, lightest, absolute_darkest, absolute_lightest).
    """
    cells = sorted(cells, key=luminance)
    return cells[len(cells) // 10], cells[len(cells) * 9 // 10], cells[0], cells[-1]


def peak_role(peak_hex, cells):
    """Is the chroma peak the light source, or the far end of the ramp?

    On a dark background the most colourful cell IS the glow. On a light
    background it is the deepest tint — the opposite end. Decided by comparing
    it to the image average, not assumed.
    """
    mean_l = statistics.mean(luminance(c) for c in cells)
    if luminance(peak_hex) > mean_l:
        return "light source / glow — brighter than the image average"
    return ("deepest tint, darker than the image average — on a light "
            "background this is the FAR END of the ramp, not the light")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--grid", default="9x20", help="downsample grid, e.g. 9x20 (default) or 0 to skip")
    ap.add_argument("--contrast", help="comma-separated foreground hex colours to test against extremes")
    ap.add_argument("--hue", action="store_true", help="print HSL for sampled colours")
    a = ap.parse_args()

    require_ffmpeg()
    w, h, pix = dimensions(a.image)
    size = os.path.getsize(a.image)
    raw_bytes = w * h * (4 if "a" in pix else 3)
    print(f"# {os.path.basename(a.image)}")
    print(f"  {w}x{h}  {pix}  {size/1024:.0f} KB  (compression {raw_bytes/size:.1f}:1)")
    print("  NOTE: compression ratio alone does NOT prove grain — check the delta stats below.")

    full = raw_rgb(a.image)
    g = grain_report(full, w, h)
    if g:
        mean, sd, nonzero, ge3, verdict = g
        print(f"\n## Grain")
        print(f"  neighbour delta  mean {mean:.2f}  sd {sd:.2f}")
        print(f"  non-zero {nonzero*100:.1f}%   >=3 {ge3*100:.1f}%")
        print(f"  => {verdict}")

    if a.grid and a.grid != "0":
        gw, gh = (int(v) for v in a.grid.lower().split("x"))
        rows, small = grid_report(a.image, gw, gh)
        print(f"\n## Gradient structure ({gw}x{gh} downsample)")
        for row in rows:
            print("  " + "  ".join(row))
        peak = saturation_peak(small, gw, gh)
        if peak:
            c, x, y, hx = peak
            role = peak_role(hx, [hexof(small[i], small[i+1], small[i+2])
                                  for i in range(0, gw * gh * 3, 3)])
            print(f"\n  chroma peak: x={x}/{gw-1} ({x/(gw-1)*100:.0f}%), "
                  f"y={y}/{gh-1} ({y/(gh-1)*100:.0f}%)  {hx}  chroma={c}")
            print(f"  => {role}")
            print("  (edge-hugging + vertically offset reads as 'light'; centred reads as 'CSS')")
        corners = {"top-left": (0, 0), "top-right": (gw-1, 0),
                   "bottom-left": (0, gh-1), "bottom-right": (gw-1, gh-1),
                   "centre": (gw//2, gh//2)}
        print()
        for nm, (x, y) in corners.items():
            i = (y * gw + x) * 3
            print(f"  {nm:<13} {hexof(small[i], small[i+1], small[i+2])}")
        if a.hue:
            print()
            for nm, (x, y) in corners.items():
                i = (y * gw + x) * 3
                r, gg, b = small[i] / 255, small[i+1] / 255, small[i+2] / 255
                hh, ll, ss = colorsys.rgb_to_hls(r, gg, b)
                print(f"  {nm:<13} H{hh*360:6.1f}  S{ss*100:5.1f}  L{ll*100:5.1f}")

        if a.contrast:
            cells = [hexof(small[(y*gw+x)*3], small[(y*gw+x)*3+1], small[(y*gw+x)*3+2])
                     for y in range(gh) for x in range(gw)]
            darkest, lightest, abs_d, abs_l = background_extremes(cells)
            print(f"\n## Contrast (WCAG; body text needs >= 4.5, large/non-text >= 3.0)")
            print(f"  background extremes (p10/p90): darkest {darkest}  lightest {lightest}")
            print(f"  absolute extremes (incl. text/logo pixels): {abs_d} .. {abs_l}")
            for fg in [c.strip() for c in a.contrast.split(",")]:
                cd, cl = contrast(fg, darkest), contrast(fg, lightest)
                worst = min(cd, cl)
                mark = "AA ok" if worst >= 4.5 else ("3:1 only" if worst >= 3.0 else "FAIL")
                print(f"  {fg}  on darkest {cd:5.2f} | on lightest {cl:5.2f} | worst {worst:5.2f}  {mark}")
            print("  Reminder: if any overlay/scrim sits between text and this background,")
            print("  these numbers are NOT the shipping values — recompute on composited pixels.")
            print("  Best input is the extracted background asset, not a screenshot with text on it.")


if __name__ == "__main__":
    main()
