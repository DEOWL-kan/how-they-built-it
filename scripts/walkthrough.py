#!/usr/bin/env python3
"""Turn a feature run into numbered steps the user can follow: 1, 2, 3, 4 —
each with what you did, what appeared, a screenshot and the recording segment.

A teardown that ends in prose ("tap the menu, then Ask") makes the reader replay
it in their head. This puts the picture and the clip next to every step.

steps.json (paths relative to the file):
    {
      "title": "Ask: from a recording to a streamed answer",
      "video": "rec.mp4",
      "steps": [
        {"action": "Tap Ask on the recording page", "result": "Panel slides up",
         "at": 2.4, "clip": [1.5, 4.0], "evidence": "e3"},
        {"action": "Send a question", "result": "Answer streams in", "image": "shot2.png"}
      ]
    }
Every step needs `action`, and a picture: `at` (seconds into the video) or `image`.
`clip` is optional. A step may name its own `video` (screenrecord caps one recording,
often at 180s); otherwise the top-level one is used. `evidence` is the id of the
report.json evidence that records this observation — `check_report.py
--walkthrough steps.json` checks it exists, is a device/browser observation, and
cites the same recording. Times count from the first frame, the way ffmpeg and
frame_diff.py count (pitfall 36).

Output goes OUTSIDE this repo: walkthrough.html (plays the clips),
walkthrough.md (paste into the report), stepNN.png, and a copy of the video.
Requires ffmpeg only when `at` is used. stdlib otherwise.

    walkthrough.py steps.json --out /tmp/ask-walkthrough
"""
import argparse
import html
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def frame_at(video, t, dest):
    """What was ON SCREEN at t — not the next frame after t.

    `ffmpeg -ss t -frames:v 1` is the obvious command and it is wrong here:
    screenrecord emits no frames while the screen is still, accurate seeking
    drops the frame that was showing, and you get the NEXT change. Measured: on
    a clip that turns white at 3.00, `-ss 2.9` returned the white frame. Keep the
    last frame at or before t instead.
    """
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", video,
                    "-vf", f"select='lte(t\\,{t})'", "-fps_mode", "passthrough",
                    "-update", "1", dest], check=True)
    if not os.path.exists(dest):
        raise ValueError(f"no frame at or before {t}s")


def clock(t):
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def check(spec):
    errors = []
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["steps: expected a non-empty list"]
    for n, s in enumerate(steps, 1):
        where = f"step {n}"
        if not isinstance(s, dict) or not str(s.get("action", "")).strip():
            errors.append(f"{where}: needs an action")
            continue
        if "at" not in s and "image" not in s:
            errors.append(f"{where}: needs a picture — `at` (seconds into the video) or `image`")
        video = s.get("video") or spec.get("video")
        if "at" in s and not video:
            errors.append(f"{where}: `at` needs a video (top-level or on the step)")
        if "at" in s and (not isinstance(s["at"], (int, float)) or s["at"] < 0):
            errors.append(f"{where}: `at` must be seconds >= 0")
        clip = s.get("clip")
        if clip is not None:
            if not video:
                errors.append(f"{where}: `clip` needs a video (top-level or on the step)")
            elif (not isinstance(clip, list) or len(clip) != 2
                  or not all(isinstance(v, (int, float)) for v in clip) or not 0 <= clip[0] < clip[1]):
                errors.append(f"{where}: `clip` must be [start, end] with 0 <= start < end")
    return errors


def build(spec_path, out):
    out = os.path.abspath(out)
    # Screens of someone else's product are analysis scratch: never let them
    # land in a repo where `git add -A` will pick them up.
    if os.path.commonpath([out, REPO]) == REPO:
        raise SystemExit(f"refusing to write inside the repo ({REPO}) — pick a directory outside it")
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    errors = check(spec)
    if errors:
        raise SystemExit("\n".join(errors))
    base = os.path.dirname(os.path.abspath(spec_path))
    os.makedirs(out, exist_ok=True)

    copied = {}

    def local_video(name):
        # One copy per recording; two different files with one basename must not collide.
        src = os.path.abspath(os.path.join(base, name))
        if src not in copied:
            stem, ext = os.path.splitext(os.path.basename(src))
            dest = os.path.basename(src)
            if dest in copied.values():
                dest = f"{stem}-{len(copied)}{ext}"
            shutil.copy2(src, os.path.join(out, dest))
            copied[src] = dest
        return copied[src]

    rows = []
    for n, s in enumerate(spec["steps"], 1):
        name = s.get("video") or spec.get("video")
        video = local_video(name) if name and ("at" in s or s.get("clip")) else None
        img = f"step{n:02d}.png"
        if "image" in s:
            shutil.copy2(os.path.join(base, s["image"]), os.path.join(out, img))
            source = os.path.basename(s["image"])
        else:
            frame_at(os.path.join(out, video), s["at"], os.path.join(out, img))
            source = f"{video} @ {clock(s['at'])}"
        rows.append((n, s, img, source, video))

    title = spec.get("title") or "Walkthrough"
    md = [f"## 操作路径：{title}", "", "> 竞品分析用。截图与录屏来自真机实测，仅用于分析。", ""]
    page = []
    for n, s, img, source, video in rows:
        tag = "[device]" + (f" {s['evidence']}" if s.get("evidence") else "")
        clip = s.get("clip")
        md.append(f"{n}. **{s['action']}**")
        if s.get("result"):
            md.append(f"   → {s['result']}")
        md.append(f"   `{tag}` 截图：{source}")
        md.append(f"   ![步骤 {n}]({img})")
        if clip:
            md.append(f"   [录屏 {clock(clip[0])}–{clock(clip[1])}]({video}#t={clip[0]},{clip[1]})")
        md.append("")
        e = html.escape
        clip_html = (f'<video controls preload="metadata" src="{e(video)}#t={clip[0]},{clip[1]}"></video>'
                     f'<p class="m">录屏 {clock(clip[0])}–{clock(clip[1])}</p>') if clip else ""
        page.append(f'<li><h2>{e(str(s["action"]))}</h2>'
                    + (f'<p>→ {e(str(s["result"]))}</p>' if s.get("result") else "")
                    + f'<p class="m">{e(tag)} · 截图：{e(source)}</p>'
                    f'<div class="media"><img src="{e(img)}" alt="步骤 {n}">{clip_html}</div></li>')

    with open(os.path.join(out, "walkthrough.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    with open(os.path.join(out, "walkthrough.html"), "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:16px;background:#fff;color:#1b1b1b}}
@media (prefers-color-scheme:dark){{body{{background:#161616;color:#e8e8e8}}}}
ol{{padding-left:1.4em}} li{{margin:0 0 32px}} h2{{font-size:1.1em;margin:0 0 4px}}
.m{{opacity:.65;font-size:.9em;margin:4px 0}}
.media{{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}}
.media img,.media video{{max-height:560px;max-width:100%;border:1px solid #8884;border-radius:6px}}
</style>
<h1>{html.escape(title)}</h1>
<p class="m">竞品分析用。截图与录屏来自真机实测，仅用于分析。</p>
<ol>{"".join(page)}</ol>
""")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("steps", help="steps.json")
    ap.add_argument("--out", required=True, help="output directory, outside this repo")
    a = ap.parse_args()
    try:
        out = build(a.steps, a.out)
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        sys.exit(f"walkthrough failed: {e}")
    print(f"{out}/walkthrough.html  (open in a browser: steps, screenshots, clips)")
    print(f"{out}/walkthrough.md    (paste into the report)")


if __name__ == "__main__":
    main()
