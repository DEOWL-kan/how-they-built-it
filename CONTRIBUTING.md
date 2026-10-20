# Contributing

The useful contribution here is usually **a pitfall, not a feature**. This repo exists
because measuring beats guessing; the scripts are just the enforcement mechanism.

## Setup

Core analysis: Python 3.8+ and `ffmpeg`/`ffprobe` on PATH. Optional Web capture and Android static-tool integration have separate explicit setup (see below).

```bash
python3 scripts/test_regressions.py     # the whole test suite, ~1s, no framework
```

**Standard library only.** No Pillow, no numpy, no pytest. Someone should be able to
`curl` a single script onto a machine and have it work. A patch that adds a dependency
needs to argue why a few lines of stdlib cannot do it. Optional Web capture dependencies
(Node/Playwright/Chromium) belong in their own tool directory; apkanalyzer/JADX are
optional external analysis tools. Missing advanced tools must not break core scripts.

## Releasing

Plugin installs (Claude Code and Codex) update only when `version` in
`.claude-plugin/plugin.json` changes. For any change users should receive: bump it
(semver), and put the same number at the top of `CHANGELOG.md` as `## <version> · <date>`.
`test_regressions.py` fails if the two disagree. Check the manifest with
`claude plugin validate --strict .`; Codex has no validate command.

⚠️ `"skills": "./."` is deliberate. Codex ignores a root `SKILL.md` unless a `skills`
path is given, and rejects `./` and `.`; `./.` is the one spelling that exposes the
root skill in both clients (pitfall 39). Do not "tidy" it.

## Fixing a bug

Three things, not one:

1. **The fix.**
2. **A sample in `scripts/test_regressions.py`** — one function, plain `assert`s, named
   after the failure.
3. **The story in `references/pitfalls.md`** — what you believed, what the numbers
   actually said, and the criterion that would have caught it earlier. Include the real
   values. "The regex was too loose" teaches nothing; "`ble` matched `drawable`, which
   is 1976 of 2886 assets in one app" teaches the class of mistake.

### Prove the sample fails without the fix

Revert your fix, run the suite, watch **that specific test** go red, then restore it.
A test that passes both ways is measuring nothing, and this is the exact failure mode
the repo is about — see pitfalls 3, 8 and 11.

While doing that, confirm your mutation actually landed on the line you meant. One
mutation here silently missed its target and the suite stayed green, which looked
exactly like "the fix wasn't needed".

## The README figures

`docs/*.svg` are real script output, never hand-edited. The inputs are synthetic
fixtures built on the fly — a fake APK, a clip with a crossfade of known duration, an
image with the light at a known point — so each figure can be checked against a ground
truth, and so no competitor's pixels ever enter this repository.

```bash
./tools/make_figures.sh        # rebuilds fixtures + all three SVGs
```

Needs ImageMagick 7 (`magick`) on top of ffmpeg. Contributors only — running the skill
needs neither. If you change a script's output format, re-run this; a figure that no
longer matches what the tool prints is worse than no figure.

⛔ Do not hand-edit the SVGs, and do not screenshot a real app for a figure. If you need
a new fixture, add it to `make_figures.sh` with its ground truth written down.

## Adding to the workflow

`SKILL.md` and `AGENTS.md` are two views of the same workflow. If you change the
procedure, change both, and keep `README.md`'s limitations honest — understating what
the tool can do is fine, overstating it is the one thing this project cannot afford.

## What will get turned down

- New dependencies for something stdlib already covers.
- Anything that downloads a package on its own, drives a store account, or touches DRM.
- Competitor assets in the repository, in any form, for any reason.
- Softening a documented limitation without measurements that justify it.

## Optional upgrade checks

- `npm test --prefix tools/web-capture`: real local HTTP server and dedicated Chromium. Install only with authorization; package/browser setup in that directory's README.
- `npm run test:cross --prefix tools/web-capture`: optional Firefox/WebKit HTTP/WS and page truth-fixture checks; install engines explicitly first.
- `python3 scripts/preflight.py --web`: checks browser startup/CDP, never touches adb.
- `python3 scripts/preflight.py --android-static`: lists optional static tools without requiring a device.
- Build the self-authored APK fixture, then run `tools/test_android_integration.py`; see examples/android-fixture. No competitor packages go in the repo.
- `python3 scripts/check_report.py report.json --capture /scratch/capture`: HTTP and stream-event attachment checks; does not certify truth or causal attribution.

Missing advanced runtimes do not block core Python regression checks. No automatic downloads, package installation, device actions or model-evaluation claims.
