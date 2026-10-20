# AGENTS.md

Instructions for any coding agent working in — or with — this repository.
Follows the [agents.md](https://agents.md) convention, so Codex, Cursor, Windsurf,
Gemini CLI, opencode, Amp and friends pick it up automatically.

Claude Code users: `SKILL.md` is the same thing in skill form, and it is the
fuller document (in Chinese). Everything below still applies.

## What this repo is

Stdlib-only Python scripts plus a written workflow for **reverse-engineering
how a competitor's screen is actually built**, so you can hand someone a spec
instead of an adjective.

The governing rule: **measure what can be measured, label the rest as inference.**
"It uses a big photo and soft light" is not an output. "2.7s eased crossfade
between five JPEGs, light centred at 58% height" is.

## Using it on a teardown task

Three kinds of request arrive, and they want different outputs:

- **"Tear this down"** — the user has named a product or screen they admire. Output is a
  spec for that one screen (mechanism, measured values, what to borrow, what not to).
- **"Research this feature"** — the user is about to build something and wants to know how
  shipping products do it. Output is a requirements analysis, a numbered walkthrough
  (1, 2, 3, 4 — each step with a screenshot and, where it moves, a recording clip; build it
  with `scripts/walkthrough.py`), a feature breakdown (entry
  points, states, limits, failure handling) and optionally a technical one (on-device vs
  cloud, third-party stack, what the API surface implies). **Every claim carries an evidence
  tag**: `[device]` you watched it happen on a device, `[browser]` observed in a browser,
  `[package]` traced to a named file,
  `[public-source]` an official statement with URL/date, `[user-report]` a user statement with author/URL/date, `[inferred]`
  the evidence does not carry the claim — which includes having multiple tags for a
  statement they only weakly support. A string proves those bytes shipped, never that a
  feature is live, so `[package]` alone never verifies a live feature, and architecture conclusions
  stay inferred even with both tags. Run `scripts/feature_probe.py` over **every APK
  `pm path` returned** — native libs can be in base or ABI splits, feature splits can hold whole
  modules, a Flutter app keeps its logic in `libapp.so`, not in dex, and a hybrid app
  can keep a whole feature in bundled H5 (`assets/**/*.js`, offline-package zips) — the
  probe header says how many H5 files it read; zero means uncovered, not absent. Say in the report
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
   a useful starting point; inspect splits if the target assets are missing. For a
   **feature or technical** teardown pull every APK returned, including all splits.
4. **Quantify.** `apk_assets.py` for composition, `frame_diff.py` for motion,
   `image_probe.py` for pixels.
5. **Write the spec**, and end it with a PASS / PLAUSIBLE / SKIP table. Anything you
   could not measure is labelled, never quietly upgraded to a measurement.

`references/pitfalls.md` is the highest-value file here. Nearly every entry is a mistake
that actually happened; the one or two that are pre-identified risks say so themselves. **Read it before you start**, not after you are stuck.

## Research depth and evidence

Read `references/research-workflow.md` for question decomposition, competing hypotheses
and depth L1–L5. Normal research targets L2; implementation questions target L3;
our own implementation plans may add L4/L5. Record achieved depth and gaps.
Use `references/evidence-format.md` and `scripts/check_report.py` for traceable records.
Validation checks structure and declared boundaries, not factual truth or actual depth.

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

## Optional deeper analysis

Web snapshot and network capture are separate: `scripts/web_probe.js` reads resolved page state; `tools/web-capture` optionally records HTTP and attached-page WS/SSE. Follow `references/web.md` for limits and time-window candidate attribution. No captured log proves causation by itself.

`apk_profile.py` inventories supplied packages with optional apkanalyzer decoding; `references/code-tracing.md` covers targeted JADX reading. No device means runtime claims remain SKIP. Even a zero decompiler exit needs error-log and expected-method checks. Examples use self-authored source and build outputs outside the repo.

[REA](https://github.com/morluto/rea) is an optional deeper step past these scripts' ceilings (H5 module graphs, a partial Dart AOT package inventory, native xrefs); see `references/rea.md`. It is never installed or configured by this skill, its static output stays `[package]`, and Frida against a competitor's app is out of bounds.

## Changing this repo

- **Core Python 3.8+, standard library only.** Image/video analysis uses FFmpeg;
  device operations use adb. Optional advanced tools must remain isolated: Web network
  capture may use Node/Playwright/Chromium, APK/code analysis may use apkanalyzer/JADX.
  Declare and check these capabilities; never auto-install or require them for core scripts.
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

- **iOS beyond what a Mac can read.** There is no adb for iPhone and no IPA without the
  user's store account. The one legitimate package route is an iPhone app the user
  installed on an Apple Silicon Mac (`references/ios.md`, `scripts/ios_bundle.py`):
  plain resources, plists, privacy manifests and frameworks, never the FairPlay range
  and never `Wrapper/iTunesMetadata.plist`. Apps not offered on the Mac, paid apps and
  everything device-side (UI tree, network) stay out of reach or unverified — say so
  rather than guessing.
- Merging split APKs, scraping stores, or anything that downloads on its own.
