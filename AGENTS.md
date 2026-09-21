# AGENTS.md

Instructions for any coding agent working in — or with — this repository.
Follows the [agents.md](https://agents.md) convention, so Codex, Cursor, Windsurf,
Gemini CLI, opencode, Amp and friends pick it up automatically.

Claude Code users: `SKILL.md` is the same thing in skill form, and it is the
fuller document (in Chinese). Everything below still applies.

## What this repo is

Three stdlib-only Python scripts plus a written workflow for **reverse-engineering
how a competitor's screen is actually built**, so you can hand someone a spec
instead of an adjective.

The governing rule: **measure what can be measured, label the rest as inference.**
"It uses a big photo and soft light" is not an output. "2.7s eased crossfade
between five JPEGs, light centred at 58% height" is.

## Using it on a teardown task

Two kinds of request arrive, and they want different outputs:

- **"Tear this down"** — the user has named a product or screen they admire. Output is a
  spec for that one screen (mechanism, measured values, what to borrow, what not to).
- **"Research this feature"** — the user is about to build something and wants to know how
  shipping products do it. Output is a requirements analysis, a feature breakdown (entry
  points, states, limits, failure handling) and optionally a technical one (on-device vs
  cloud, third-party stack, what the API surface implies). **Every claim carries an evidence
  tag**: `[device]` you watched it happen, `[package]` traced to a named file, `[inferred]`
  the evidence does not carry the claim — which includes having both other tags for a
  statement they only weakly support. A string proves those bytes shipped, never that a
  feature is live, so `[package]` alone is never "measured", and architecture conclusions
  stay inferred even with both tags. Run `scripts/feature_probe.py` over **every APK
  `pm path` returned** — native libs are not in base.apk, feature splits can hold whole
  modules, and a Flutter app keeps its logic in `libapp.so`, not in dex. Say in the report
  which splits you actually analysed; anything you did not pull is uncovered, not absent. Driving a competitor's app is
  not free either: it can create real data, burn a free quota, and need the user's own
  account. Say so before you start.
- **"Help me find ideas"** — the user is designing a screen and is stuck. Output is a
  **comparison brief**: three shipping references (mix web and app), each reduced to its
  mechanism in one line, where they agree, where they diverge, and which route fits the
  user's constraints. Only after they pick one do you tear it down in full.
  The most common mistake here is tearing down the first thing you find instead of
  offering a choice.

The full five-step workflow is in `SKILL.md`. The short version:

1. **Pick targets.** Search web and app together — the difference between how the two
   platforms solve the same screen is itself an idea. Finding is free (package lists,
   store screenshots, opening a website); fetching a package is not — see Boundaries. Two or three torn down properly beats ten surveyed. Prefer apps
   already installed on the device you have access to — no download, no authorization.
2. **Observe on a real device first.** The asset list tells you *what exists*; only the
   running app tells you *which screen uses it*. Record entry animations from a cold
   start (`am force-stop`, start the recorder *before* launching) — an app already
   sitting on the screen will look static no matter how long you record.
3. **Get the package.** Installed on the device → `adb pull` it, no download needed.
   Not installed → **ask first**, then a free package from a public source is fine; verify
   its `versionCode` with `aapt dump badging` before trusting anything you read out of it,
   because a mirror can serve a years-old build and nothing about the asset list will look
   wrong. Never sign into the user's store account. For a **visual** teardown `base.apk` is
   normally the whole answer even when `pm path` returns six lines; for a **feature or
   technical** one it never is — pull every split.
4. **Quantify.** `apk_assets.py` for composition, `frame_diff.py` for motion,
   `image_probe.py` for pixels.
5. **Write the spec**, and end it with a PASS / PLAUSIBLE / SKIP table. Anything you
   could not measure is labelled, never quietly upgraded to a measurement.

`references/pitfalls.md` is the highest-value file here. Nearly every entry is a mistake
that actually happened; the one or two that are pre-identified risks say so themselves. **Read it before you start**, not after you are stuck.

## Boundaries — these are not negotiable

| Action | |
|---|---|
| Analysing mechanism, timing, colour, structure, asset types | ✅ that is the point |
| Turning findings into your own spec and rebuilding it yourself | ✅ |
| Screenshots and frames, for analysis and comparison | ✅ label them |
| **Downloading a free package from a public source** | ⚠️ **ask first**, then verify its version |
| Signing into the user's store account to fetch one | ⛔ never |
| Paid apps, region-locked apps, anything behind a purchase | ⛔ never |
| Shipping a competitor's assets in your product | ⛔ never |
| Committing competitor assets to any repo | ⛔ never |
| Bypassing paywalls, patching clients, circumventing DRM | ⛔ never — public packages only |

Extracted assets are analysis scratch. Keep them outside the repo; `.gitignore`
blocks the obvious extensions, which is a backstop, not permission to try.

## Changing this repo

- **Python 3.8+, standard library only.** `ffmpeg`/`ffprobe` on PATH are the only
  external tools. Do not add a dependency — Pillow and numpy are deliberately absent
  so this runs anywhere.
- **Run the checks:** `python3 scripts/test_regressions.py` (no framework, ~1s).
- **Fixing a bug means adding a sample for it** in `scripts/test_regressions.py`, and
  writing the story into `references/pitfalls.md` — what happened, what the numbers
  actually were, and the criterion that would have caught it.
- **Verify the sample actually fails without your fix.** Revert the fix, watch the test
  go red, put it back. A test that passes both ways is measuring nothing. Assert that
  your mutation landed on the target line — one silently missed and the suite stayed
  green.
- Do not soften a stated limitation to make the tool sound better. `README.md`
  says what this cannot do on purpose.

## Explicit non-goals

- **iOS.** There is no adb for iPhone, so the whole device-to-package chain is missing
  and the asset inventory — the most decisive step — cannot run. Recording and
  screenshot analysis still work; package composition does not. Say so rather than
  guessing.
- Merging split APKs, scraping stores, or anything that downloads on its own.
