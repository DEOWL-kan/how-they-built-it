#!/usr/bin/env python3
"""Render captured script output to an SVG terminal card for the README.

The input is the real stdout of one of the scripts, unedited — colour comes
from rules applied to the line, not from markup you have to add by hand. That
matters: a figure you have to hand-annotate is a figure that drifts away from
what the tool actually prints. Regenerating is always:

    python3 scripts/apk_assets.py demo.apk | python3 tools/render_svg.py docs/x.svg --title "..."

Dark on both GitHub themes, by convention — a terminal is dark everywhere, so
there is no light variant to keep in sync.

stdlib-only, like everything else here.
"""
import argparse
import re
import sys

BG, FG = "#0d1117", "#c9d1d9"
RULES = [                                   # first match wins
    (re.compile(r"^\s*##+ "),        "#79c0ff"),   # section heading
    (re.compile(r"^\s*# "),          "#8b949e"),   # file/echo header
    (re.compile(r"=>"),              "#3fb950"),   # the conclusion line
    (re.compile(r"⚠️|^\s*!|WARNING"), "#d29922"),   # caveat
    (re.compile(r"\bFAIL\b|NOT A LOOP"), "#f85149"),
    (re.compile(r"^\s*(TIP|NOTE|Reminder)"), "#8b949e"),
]
CW, LH, PAD, TOP = 7.6, 17.0, 18, 40        # char advance (0.6em at 12.5px, + slack)


def colour(line):
    for pat, c in RULES:
        if pat.search(line):
            return c
    return FG


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(lines, title):
    cols = max([len(l) for l in lines] + [len(title) + 8, 40])
    w = int(cols * CW + PAD * 2)
    h = int(TOP + len(lines) * LH + PAD)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'viewBox="0 0 {w} {h}" font-family="ui-monospace,SFMono-Regular,'
           f'Menlo,Consolas,monospace" font-size="12.5">',
           f'<rect width="{w}" height="{h}" rx="8" fill="{BG}"/>',
           f'<rect width="{w}" height="{TOP-12}" rx="8" fill="#161b22"/>',
           f'<rect y="{TOP-20}" width="{w}" height="8" fill="#161b22"/>']
    for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        out.append(f'<circle cx="{PAD+6+i*15}" cy="14" r="5.5" fill="{c}"/>')
    out.append(f'<text x="{w/2}" y="18.5" fill="#8b949e" font-size="11.5" '
               f'text-anchor="middle">{esc(title)}</text>')
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        y = TOP + i * LH + 12
        out.append(f'<text x="{PAD}" y="{y:.1f}" fill="{colour(line)}" '
                   f'xml:space="preserve">{esc(line)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    lines, prev_blank = [], False
    for raw in sys.stdin:
        line = raw.rstrip("\n").replace("\t", "    ")
        blank = not line.strip()
        if blank and prev_blank:          # a filtered-out block should read as one
            continue                      # gap, not as however many lines it was
        lines.append("" if blank else line)
        prev_blank = blank
    while lines and not lines[-1].strip():
        lines.pop()
    open(a.out, "w").write(render(lines, a.title))
    print(f"{a.out}  {len(lines)} lines", file=sys.stderr)


if __name__ == "__main__":
    main()
