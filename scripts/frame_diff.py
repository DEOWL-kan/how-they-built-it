#!/usr/bin/env python3
"""Measure the actual rhythm of an animated screen from a screen recording.

Eyeballing a few sampled frames gives you the wrong answer — you will read a
2.7s ease-in-out crossfade as "changes every 2.5s with a 0.6s fade". This
computes per-frame difference over a region of interest and reports where
motion actually starts, peaks and stops.

Shape of the curve matters as much as the timing:
  - bell curve (slow -> fast -> slow)  = eased crossfade or eased camera move
  - plateau                            = linear transition
  - spike                              = hard cut
  - low but never zero                 = continuous motion (Ken Burns, video)
  - dead flat at zero                  = genuinely static

Requires ffmpeg/ffprobe. stdlib-only otherwise.

Usage:
    frame_diff.py rec.mp4
    frame_diff.py rec.mp4 --crop 1080x1200+0+200   # WxH+X+Y, exclude fixed UI
    frame_diff.py rec.mp4 --fps 20 --quiet-threshold 0.1
"""
import argparse
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


def probe(path):
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration,nb_frames,r_frame_rate,avg_frame_rate",
                "-of", "default=nw=1", path])
    d = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        d[k] = v
    return d


def sample(path, fps, crop, sw, sh):
    filters = []
    if crop:
        wh, _, xy = crop.partition("+")
        w, h = wh.lower().split("x")
        x, y = (xy.split("+") + ["0", "0"])[:2] if xy else ("0", "0")
        filters.append(f"crop={w}:{h}:{x}:{y}")
    filters += [f"fps={fps}", f"scale={sw}:{sh}", "format=gray"]
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        tmp = f.name
    _run(["ffmpeg", "-loglevel", "error", "-i", path, "-vf", ",".join(filters),
          "-f", "rawvideo", "-pix_fmt", "gray", tmp, "-y"])
    data = open(tmp, "rb").read()
    os.unlink(tmp)
    n = sw * sh
    return [data[i * n:(i + 1) * n] for i in range(len(data) // n)]


def _rate(s):
    """ffprobe writes rationals: '30/1', '3255/214', and '0/0' for "no idea"."""
    num, _, den = (s or "").partition("/")
    try:
        n, d = float(num), float(den or 1)
    except ValueError:
        return None
    return n / d if n and d else None


def source_rate(info):
    """The rate worth dividing into.

    avg_frame_rate first, because `adb shell screenrecord` emits a frame only
    when the screen changes and then labels the file r_frame_rate=30/1 anyway --
    measured: a clip averaging 15.2 fps claimed 30. Same header dishonesty that
    makes nb_frames useless (pitfall 13), so treat r_frame_rate the same way.
    """
    return _rate(info.get("avg_frame_rate")) or _rate(info.get("r_frame_rate"))


def divisor_advice(src, fps, tol=0.02):
    """Largest sampling rate <= fps that divides `src` evenly, or None if fine.

    ffmpeg's fps filter bridges a non-integer ratio by duplicating and dropping
    frames unevenly. On a textured screen that is harmless noise. On a flat
    surface -- a gradient with no grain, which is exactly the kind of background
    this tool gets pointed at -- every pixel crosses its 8-bit boundary in
    lockstep, so the duplicated samples read delta 0 and the signal strobes
    across quiet_threshold. One transition then splits into move/quiet/move,
    which invents a static hold, and the loop verdict flips to NOT A LOOP on a
    clip that loops perfectly. Measured on a 4.2s synthetic loop: 30 fps source
    sampled at 20 gave "NOT A LOOP, 80% spread"; at 15 it gave cycle 4.16s.
    """
    if not src or fps <= 0:
        return None
    if abs(src / fps - round(src / fps)) <= tol:
        return None
    for k in range(max(1, round(src / fps)), int(src) + 2):
        cand = src / k
        if cand <= fps:
            # Floor to a whole number: the caller has to type this back in, and
            # on a variable-rate recording `src` is an average anyway, so a rate
            # like 15.2103 is false precision as well as unusable.
            return float(int(cand)) or None
    return None


def is_vfr(info, tol=0.05):
    """Does the container's declared rate disagree with what it actually holds?

    `adb shell screenrecord` emits a frame only when the screen changes, so a
    clip of a still screen holds one frame for its whole duration. It labels the
    file r_frame_rate=30/1 regardless. Measured on a real device: 2s of a static
    screen produced a single frame, and a decimated 14s clip averaging 15.2 fps
    still declared 30. There is no cadence to divide into on such a file.
    """
    r, avg = _rate(info.get("r_frame_rate")), _rate(info.get("avg_frame_rate"))
    return bool(r and avg and abs(r - avg) / r > tol)


def segments(diffs, fps, quiet, min_run=None):
    """Split the timeline into quiet and moving runs.

    Runs shorter than `min_run` are absorbed into their neighbour. Without this,
    a single sample grazing the threshold splits one 2.9s transition into
    move/quiet/move and the cycle estimate collapses to nonsense (a real run
    produced a bogus 0.15s "cycle" this way).

    `min_run` defaults to two sampling intervals, not a fixed duration. That
    matters: real static holds can be as short as 0.10s (measured on a shipping
    app), so a hard-coded 0.2s floor eats the very segments you are trying to
    find and reports the whole clip as one continuous move.
    """
    if min_run is None:
        min_run = 2.0 / fps
    runs, cur, start = [], None, 0.0
    for t, v in diffs:
        state = "quiet" if v <= quiet else "move"
        if cur is None:
            cur, start = state, t
        elif state != cur:
            runs.append((cur, start, t))
            cur, start = state, t
    if cur is not None:
        runs.append((cur, start, diffs[-1][0]))

    merged = []
    for run in runs:
        kind, s, e = run
        if merged and (e - s) < min_run:
            pk, ps, _ = merged[-1]
            merged[-1] = (pk, ps, e)          # absorb into the previous run
        elif merged and merged[-1][0] == kind:
            pk, ps, _ = merged[-1]
            merged[-1] = (pk, ps, e)          # same state, join them up
        else:
            merged.append(run)
    return merged


def suggest_crop(frames, sw, sh, src_w, src_h):
    """Find the region that actually moves, so you don't have to measure it by hand.

    Per cell, the range (max - min) over the whole clip. Cells that never change
    are fixed UI — status bar, buttons, the card behind everything. The bounding
    box of the cells that do change is the region worth analysing.

    Returns (w, h, x, y) in SOURCE pixels, or None if nothing moved.

    ponytail: a bounding box, not a mask. Two separate animated corners give one
    box covering both (and the dead middle with them). Good enough to replace
    eyeballing coordinates off a screenshot; split it by hand if that bites.
    """
    if len(frames) < 2:
        return None
    n = sw * sh
    ranges = []
    for i in range(n):
        lo = hi = frames[0][i]
        for f in frames[1:]:
            v = f[i]
            if v < lo:
                lo = v
            elif v > hi:
                hi = v
        ranges.append(hi - lo)

    peak = max(ranges)
    if peak < 8:                       # 8/255 — below this it is encoder noise
        return None
    # Relative floor: keeps a faint-but-real animation, drops compression fizz
    # around high-contrast text. Absolute floor stops a dead-still clip from
    # promoting its own noise to "the animated region".
    floor = max(8, peak * 0.15)
    xs = [i % sw for i, r in enumerate(ranges) if r >= floor]
    ys = [i // sw for i, r in enumerate(ranges) if r >= floor]
    if not xs:
        return None

    cw, ch = src_w / sw, src_h / sh
    x0, x1 = int(min(xs) * cw), int((max(xs) + 1) * cw)
    y0, y1 = int(min(ys) * ch), int((max(ys) + 1) * ch)
    return x1 - x0, y1 - y0, x0, y0


def loop_verdict(cycles):
    """Do the gaps between static holds actually repeat?

    Printing a "cycle length" whenever there are two static segments turned a
    one-shot launch sequence (splash -> hold -> cut -> settle) into
    "cycle length: mean 1.60s ['2.05','1.15']" — a confident number describing
    a loop that does not exist. Returns "loop", "one-gap" or "not-a-loop".
    """
    if len(cycles) < 2:
        return "one-gap"
    mean_c = statistics.mean(cycles)
    spread = (max(cycles) - min(cycles)) / mean_c if mean_c else 1.0
    return "not-a-loop" if spread > 0.35 else "loop"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=20, help="sampling rate (default 20)")
    ap.add_argument("--crop", help="WxH+X+Y — crop to the animated region, excluding fixed UI")
    ap.add_argument("--quiet-threshold", type=float, default=0.1,
                    help="delta at or below this counts as static (default 0.1)")
    ap.add_argument("--no-plot", action="store_true", help="skip the ascii bars")
    ap.add_argument("--suggest-crop", action="store_true",
                    help="report the region that actually moves, as a --crop argument")
    ap.add_argument("--res", default="108x120",
                    help="analysis resolution (default 108x120). Raise it for subtle motion — "
                         "see the warning printed when a run looks static.")
    a = ap.parse_args()

    require_ffmpeg()
    info = probe(a.video)
    print(f"# {os.path.basename(a.video)}")
    # nb_frames is NOT trustworthy here: `adb shell screenrecord` writes an mp4
    # whose header claims 5 frames for a 75-frame clip. Report what we sampled.
    print(f"  {info.get('width')}x{info.get('height')}  "
          f"{float(info.get('duration', 0)):.2f}s")
    if not a.crop:
        print("  TIP: pass --crop to exclude fixed UI (buttons/status bar) —")
        print("       otherwise a blinking clock shows up as 'motion'.")

    sw, sh = (int(v) for v in a.res.lower().split("x"))
    frames = sample(a.video, a.fps, a.crop, sw, sh)
    print(f"  {len(frames)} frames sampled at {a.fps} fps, analysed at {sw}x{sh}")
    src_fps = source_rate(info)
    better = divisor_advice(src_fps, a.fps)
    if better:
        if is_vfr(info):
            print(f"  ⚠️  variable frame rate: the header says {_rate(info.get('r_frame_rate')):g} fps, "
                  f"the file averages {src_fps:.3g}.")
            print( "      screenrecord emits a frame only when the screen changes, so there is")
            print( "      no cadence to sample against.")
        else:
            print(f"  ⚠️  {a.fps:g} fps does not divide this source's {src_fps:g} fps evenly.")
        print( "      ffmpeg duplicates and drops frames to bridge the gap. On a flat")
        print( "      surface (a gradient with no grain) the duplicates read as delta 0")
        print( "      and one transition can split into move/quiet/move.")
        print(f"      Re-run with --fps {better:g} before trusting the segments below.")
    n = sw * sh
    diffs = []
    for i in range(1, len(frames)):
        prev, cur = frames[i - 1], frames[i]
        s = sum(abs(cur[j] - prev[j]) for j in range(n)) / n
        diffs.append((i / a.fps, s))
    if not diffs:
        sys.exit("not enough frames sampled")

    if a.suggest_crop:
        box = suggest_crop(frames, sw, sh, int(info.get("width") or 0),
                           int(info.get("height") or 0))
        print("\n## Suggested crop")
        if box:
            w, h, x, y = box
            src_w, src_h = int(info.get("width") or 0), int(info.get("height") or 0)
            print(f"  --crop {w}x{h}+{x}+{y}")
            if src_h and y + h <= src_h * 0.06 and w * h < src_w * src_h * 0.02:
                # Measured: a 16.8s recording of a completely static screen
                # suggested 30x20+850+60 — the charging indicator.
                print("  ⚠️  that box is a sliver in the status bar — almost certainly the")
                print("      clock/battery, not the app. Treat this screen as STATIC, and")
                print("      crop the status bar OUT rather than cropping to this.")
            elif src_w and w * h > src_w * src_h * 0.9:
                print("  ⚠️  that is essentially the whole frame — the clip probably contains a")
                print("      screen transition. Cut to one screen first, then re-run.")
            else:
                print("  (bounding box of every cell that changed; widen it if the motion you")
                print("   care about is faint, and confirm against a frame before trusting it)")
        else:
            print("  nothing moved — no crop to suggest.")

    peak = max(v for _, v in diffs)
    if not a.no_plot:
        print(f"\n## Per-frame delta (sampled at {a.fps} fps)")
        for t, v in diffs:
            bar = "#" * int(v / max(peak, 0.001) * 46)
            print(f"  {t:6.2f}  {v:6.2f}  {bar}")

    runs = segments(diffs, a.fps, a.quiet_threshold)
    quiet = [(s, e) for k, s, e in runs if k == "quiet"]
    moves = [(s, e) for k, s, e in runs if k == "move"]
    print(f"\n## Segments (quiet = delta <= {a.quiet_threshold})")
    for kind, s, e in runs:
        print(f"  {kind:<5} {s:6.2f} -> {e:6.2f}   ({e - s:.2f}s)")

    if quiet:
        qd = [e - s for s, e in quiet]
        print(f"\n  static segments: {len(quiet)}, mean {statistics.mean(qd):.2f}s")
    if moves:
        md = [e - s for s, e in moves]
        print(f"  moving segments: {len(moves)}, mean {statistics.mean(md):.2f}s")
    if len(quiet) >= 2:
        starts = [s for s, _ in quiet]
        cycles = [b - a_ for a_, b in zip(starts, starts[1:])]
        mean_c = statistics.mean(cycles)
        verdict = loop_verdict(cycles)
        if verdict == "one-gap":
            print(f"  one gap only:    {cycles[0]:.2f}s between the two static holds —")
            print("                   record longer before calling that a cycle.")
        elif verdict == "not-a-loop":
            spread = (max(cycles) - min(cycles)) / mean_c
            print(f"  NOT A LOOP:      gaps between static holds are {['%.2f' % c for c in cycles]} "
                  f"({spread*100:.0f}% spread)")
            print("                   a one-shot sequence (launch, transition, settle), not a cycle.")
            if better:
                # The split this verdict is built on is the exact thing an uneven
                # sampling ratio fabricates, so say it here rather than only in a
                # header the reader has already scrolled past.
                print(f"                   ⚠️  but --fps {a.fps:g} is not a clean rate for this source —")
                print(f"                       re-run at --fps {better:g} before believing it.")
        else:
            print(f"  cycle length:    mean {mean_c:.2f}s  {['%.2f' % c for c in cycles]}")

    print(f"\n  peak delta {peak:.2f}   mean {statistics.mean(v for _, v in diffs):.2f}")

    # Subtle motion is easy to destroy by downsampling: a 3px float on a 1080px
    # wide capture becomes 0.3px at 108px wide and gets interpolated away. If the
    # result looks static, say so rather than letting "no motion" stand unchallenged.
    if peak < 0.5:
        src_w = int(info.get("width") or 0)
        print(f"\n  ⚠️  Peak delta is very low at {sw}x{sh} analysis resolution.")
        if src_w and src_w // sw >= 4:
            print(f"      Source is {src_w}px wide, downsampled {src_w//sw}x — sub-pixel motion")
            print(f"      (a few px of drift, a slow breath) would be smoothed out entirely.")
        print(f"      Before concluding 'static', re-run with --res {sw*2}x{sh*2} (double this run).")
    print("\n## How to read this")
    print("  Sample frames AT the quiet points and AT the peaks, then look at them:")
    print("    quiet point sharp + peak shows two images ghosted  => crossfade")
    print("    every frame sharp, content shifting                => camera move / video")
    print("  Do not report a rhythm you only inferred from the numbers — confirm visually.")


if __name__ == "__main__":
    main()
