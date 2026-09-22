#!/usr/bin/env bash
# Regenerate the three README figures in docs/.
#
# Every figure is real script output. The inputs are synthetic fixtures built
# here — a fake APK, a clip with a crossfade of known duration, an image with
# the light placed at a known point — so each figure can be checked against a
# ground truth, and so no competitor's pixels ever enter this repository.
#
# Needs ffmpeg and ImageMagick 7 (`magick`). Contributors only; running the
# skill itself needs neither.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=docs; TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT"

# ---- fixture 1: a loop whose rhythm we know exactly -------------------------
# hold 1.5s + smoothstep-eased crossfade 2.7s  =>  cycle 4.2s, 3 transitions.
# Flat gradients on purpose: that is the case pitfall 30 is about.
for i in 1 2 3 4; do
  c=$(sed -n "${i}p" <<< $'#0B1F2A-#2E6E6B\n#1A1026-#6B3A7A\n#221407-#8A5A2B\n#04121B-#1F5F8A')
  magick -size 1080x1920 gradient:"$c" "$TMP/g$i.png"
done
S='(P*P*(3-2*P))'; E="A*$S+B*(1-$S)"          # P runs 1 -> 0, so A is the start
ffmpeg -loglevel error -y \
  -loop 1 -t 15 -i "$TMP/g1.png" -loop 1 -t 15 -i "$TMP/g2.png" \
  -loop 1 -t 15 -i "$TMP/g3.png" -loop 1 -t 15 -i "$TMP/g4.png" \
  -filter_complex "[0][1]xfade=transition=custom:duration=2.7:offset=1.5:expr='$E'[x1];\
[x1][2]xfade=transition=custom:duration=2.7:offset=5.7:expr='$E'[x2];\
[x2][3]xfade=transition=custom:duration=2.7:offset=9.9:expr='$E'[v]" \
  -map "[v]" -t 14.1 -r 30 -pix_fmt yuv420p -c:v libx264 -crf 20 "$TMP/cfr.mp4"
# screenrecord emits a frame only when the screen changes; mpdecimate reproduces
# that, which is what makes the default sampling rate misfire.
ffmpeg -loglevel error -y -i "$TMP/cfr.mp4" -vf mpdecimate=hi=64:lo=32:frac=0.33 \
  -fps_mode vfr -c:v libx264 -crf 20 "$TMP/rec.mp4"

# ---- fixture 2: a background whose light position we know -------------------
magick -size 1080x1920 gradient:'#0B1F2A-#16404A' "$TMP/base.png"
magick -size 1800x1800 radial-gradient:'#7FD3C7-#000000' -alpha off "$TMP/spot.png"
magick "$TMP/base.png" "$TMP/spot.png" -geometry +-576+-420 \
  -compose Screen -composite "$TMP/flat.png"          # light centre = 30% x 25%
magick "$TMP/flat.png" -attenuate 0.30 +noise Gaussian "$TMP/grainy.png"

# ---- fixture 3: an app that does not exist ----------------------------------
python3 - "$TMP/app.apk" <<'PY'
import os, sys, zipfile
F = {"assets/images/login/bg_loop_1.jpg": 311, "assets/images/login/bg_loop_2.jpg": 307,
     "assets/images/login/bg_loop_3.jpg": 298, "assets/images/login/bg_loop_4.jpg": 284,
     "assets/images/login/bg_loop_5.jpg": 276, "assets/images/login/logo_mark.png": 22,
     "assets/video/onboarding_hero.mp4": 734, "assets/anim/onboarding_confetti.json": 41,
     "assets/images/paywall/premium_badge.webp": 58, "res/font/InterVariable.ttf": 412,
     "res/drawable/ic_settings.xml": 2, "classes.dex": 1840}
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    for name, kb in F.items():          # random bytes: incompressible, so the
        z.writestr(name, os.urandom(kb * 1024))   # listed size is the real size
PY

# ---- render ----------------------------------------------------------------
{ echo "\$ python3 scripts/apk_assets.py app.apk"
  python3 scripts/apk_assets.py "$TMP/app.apk"
} | python3 tools/render_svg.py "$OUT/apk-assets.svg" \
      --title "apk_assets.py — what is this screen made of?"

{ echo "\$ python3 scripts/frame_diff.py rec.mp4 --no-plot"
  python3 scripts/frame_diff.py "$TMP/rec.mp4" --no-plot \
   | grep -vE "^  TIP|otherwise a blinking|^## How to read|^  Sample frames|^    quiet point|^    every frame|^  Do not report|^  peak delta"
  echo
  echo "\$ python3 scripts/frame_diff.py rec.mp4 --fps 15 --no-plot"
  python3 scripts/frame_diff.py "$TMP/rec.mp4" --fps 15 --no-plot \
   | grep -E "^  static segments|^  moving segments|^  cycle length"
  echo
  echo "# ground truth — this clip was generated, so the answer is known:"
  echo "#   hold 1.50s + eased crossfade 2.70s, looping. cycle = 4.20s"
} | python3 tools/render_svg.py "$OUT/frame-diff.svg" \
      --title "frame_diff.py — and when it tells you not to believe it"

{ echo "\$ python3 scripts/image_probe.py bg.png --contrast '#FFFFFF,#8FA3A8'"
  python3 scripts/image_probe.py "$TMP/flat.png" --grid 0 --contrast '#FFFFFF,#8FA3A8' | tail -n +2
  python3 scripts/image_probe.py "$TMP/flat.png" --contrast '#FFFFFF,#8FA3A8' | sed -n '/chroma peak/,/^  centre/p'
  python3 scripts/image_probe.py "$TMP/flat.png" --contrast '#FFFFFF,#8FA3A8' | sed -n '/## Contrast/,/#8FA3A8/p'
  echo
  echo "\$ # the same image with a grain layer added — the verdict flips:"
  python3 scripts/image_probe.py "$TMP/grainy.png" --grid 0 | sed -n '/## Grain/,/=>/p'
  echo
  echo "# ground truth — this image was generated: light at 30% x 25%, grain only in the second."
} | python3 tools/render_svg.py "$OUT/image-probe.svg" \
      --title "image_probe.py — grain, light, and whether your text survives"

echo "done — $OUT/{apk-assets,frame-diff,image-probe}.svg"
