#!/usr/bin/env python3
"""What an iOS app bundle is made of — the iOS counterpart of apk_assets.py plus
the manifest read, for the one legitimate place an iOS bundle is readable without
a jailbreak: an iPhone app the user installed on an Apple Silicon Mac.

    ios_bundle.py "/Applications/Some App.app"      # Mac copy of an iPhone app
    ios_bundle.py path/to/Payload/Y.app             # any .app directory

Reads plain files only: composition by type, Info.plist (URL schemes, queried
schemes, ATS, background modes, permission strings), privacy manifests, embedded
frameworks / bundles / extensions, and each Mach-O's FairPlay range. It never reads
Wrapper/iTunesMetadata.plist (purchaser details) and never touches encrypted bytes.

⚠️ The Mac copy is thinned by the store for ONE device class. Measured on two apps:
   one got iPad assets ("idiom 2"), the other iPhone ("idiom 1"). Read the thinning
   line before treating asset sizes or layout as the iPhone build's.
⚠️ A privacy manifest is the developer's declaration, not observed behaviour; a
   framework or .bundle name hints at a vendor, it does not prove which feature uses it.

stdlib only (plistlib reads binary plists). `xcrun assetutil` is used when present.
"""
import argparse
import collections
import json
import os
import plistlib
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feature_probe import is_macho, macho_encryption, resolve_app  # noqa: E402

KINDS = [
    ("image", (".png", ".jpg", ".jpeg", ".webp", ".heic", ".gif", ".pdf", ".svg")),
    ("asset catalog", (".car",)),
    ("video", (".mp4", ".mov", ".m4v")),
    ("audio", (".mp3", ".wav", ".m4a", ".caf", ".aac")),
    ("font", (".ttf", ".otf", ".ttc")),
    ("interface (nib/storyboard)", (".nib", ".storyboardc")),
    ("ml model", (".mlmodelc", ".mnn", ".tnnmodel", ".tflite", ".onnx", ".bin")),
    ("H5", (".js", ".html", ".htm", ".css")),
    ("json", (".json",)),
]


def kind_of(name, full):
    low = name.lower()
    for kind, exts in KINDS:
        if low.endswith(exts):
            if kind == "json" and _is_lottie(full):
                return "lottie (json)"
            return kind
    return "other"


def _is_lottie(path):
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        return b'"layers"' in head and b'"v"' in head
    except OSError:
        return False


def read_plist(path):
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def inventory(app):
    kinds = collections.defaultdict(lambda: [0, 0])
    largest, machos, privacy = [], [], []
    for folder, dirs, files in os.walk(app):
        dirs.sort()
        # .storyboardc / .mlmodelc are directories; count them once as a unit.
        for d in list(dirs):
            if d.lower().endswith((".storyboardc", ".mlmodelc")):
                size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(os.path.join(folder, d))
                           for f in fs if not os.path.islink(os.path.join(r, f)))
                k = kind_of(d, os.path.join(folder, d))
                kinds[k][0] += 1
                kinds[k][1] += size
                dirs.remove(d)
        for name in sorted(files):
            full = os.path.join(folder, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, app)
            size = os.path.getsize(full)
            if name == "PrivacyInfo.xcprivacy":
                privacy.append(rel)
            with open(full, "rb") as f:
                head = f.read(8)
            if is_macho(head):
                with open(full, "rb") as f:
                    machos.append((rel, size, macho_encryption(f.read())))
                continue
            k = kind_of(name, full)
            kinds[k][0] += 1
            kinds[k][1] += size
            largest.append((size, rel))
    return kinds, sorted(largest, reverse=True)[:10], machos, privacy


def assetutil_names(car):
    tool = shutil.which("xcrun") and "xcrun"
    if not tool:
        return None
    try:
        out = subprocess.run(["xcrun", "assetutil", "--info", car], capture_output=True, timeout=60)
        rows = json.loads(out.stdout or b"[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    names = [r.get("Name") for r in rows if isinstance(r, dict) and r.get("Name")]
    thinning = next((r.get("ThinningParameters") for r in rows if isinstance(r, dict)
                     and r.get("ThinningParameters")), None)
    return len(set(names)), thinning


def report(path):
    app, wrapped = resolve_app(path)
    info = read_plist(os.path.join(app, "Info.plist"))
    if not isinstance(info, dict) or not info.get("CFBundleExecutable"):
        raise ValueError(f"{app}: no Info.plist with CFBundleExecutable — not an iOS app bundle")
    exe = os.path.join(app, info["CFBundleExecutable"])
    with open(exe, "rb") as f:
        if not is_macho(f.read(8)):
            raise ValueError(f"{exe}: not a Mach-O — not an iOS app bundle")

    print(f"# {info.get('CFBundleIdentifier')}  {info.get('CFBundleShortVersionString')} "
          f"({info.get('CFBundleVersion')})  min iOS {info.get('MinimumOSVersion')}")
    print(f"  bundle: {app}")
    if wrapped:
        print("  ⚠️  Mac copy of an iPhone app: thinned for one device class — see the thinning line")
        print("      (idiom 1 = iPhone, 2 = iPad) before calling sizes or layout the iPhone build's.")

    kinds, largest, machos, privacy = inventory(app)
    print("\n## Composition")
    for kind, (n, size) in sorted(kinds.items(), key=lambda kv: -kv[1][1]):
        print(f"  {size / 1048576:8.1f} MB  {n:6d}  {kind}")
    print("  largest:")
    for size, rel in largest:
        print(f"  {size / 1048576:8.1f} MB  {rel}")

    cars = [os.path.join(r, f) for r, _, fs in os.walk(app) for f in fs if f.endswith(".car")]
    if cars:
        total, thin = 0, None
        for car in cars:
            got = assetutil_names(car)
            if got is None:
                total = None
                break
            total += got[0]
            thin = thin or got[1]
        if total is None:
            print(f"  {len(cars)} Assets.car — not read (needs `xcrun assetutil`, macOS with Xcode tools)")
        else:
            print(f"  {len(cars)} Assets.car, {total} asset names (xcrun assetutil --info)")
            if thin:
                idiom = {"1": "iPhone", "2": "iPad"}.get(thin.split("<idiom ", 1)[-1][:1], "unknown")
                print(f"  thinned for: {idiom}  ({thin[:90]}…)")

    print("\n## Code and frameworks")
    for rel, size, enc in sorted(machos):
        state = ", ".join(f"cryptid {cid} ({n} bytes @ {off})" for off, n, cid in enc) or "no encryption info"
        print(f"  {size / 1048576:8.1f} MB  {rel}   [{state}]")
    print("  ⛔ Encrypted ranges are never read. feature_probe.py reads the rest of these files")
    print("     for libraries, hosts and keywords, with those ranges blanked.")
    bundles = sorted({os.path.relpath(os.path.join(r, d), app) for r, ds, _ in os.walk(app) for d in ds
                      if d.endswith(".bundle")})
    if bundles:
        print(f"  {len(bundles)} resource bundles (SDK names are hints, not proof):")
        print("    " + ", ".join(os.path.basename(b) for b in bundles[:40]) + (" …" if len(bundles) > 40 else ""))
    appex = sorted(d for d in os.listdir(os.path.join(app, "PlugIns"))) if os.path.isdir(
        os.path.join(app, "PlugIns")) else []
    if appex:
        print(f"  extensions: {', '.join(appex)}")

    print("\n## Info.plist — declared capabilities")
    schemes = [s for t in info.get("CFBundleURLTypes", []) if isinstance(t, dict)
               for s in t.get("CFBundleURLSchemes", [])]
    print(f"  URL schemes ({len(schemes)}): {', '.join(schemes[:20])}")
    queried = info.get("LSApplicationQueriesSchemes", [])
    print(f"  queries other apps ({len(queried)}): {', '.join(queried[:30])}")
    print(f"  background modes: {', '.join(info.get('UIBackgroundModes', [])) or 'none'}")
    ats = info.get("NSAppTransportSecurity")
    print(f"  ATS: {json.dumps(ats, ensure_ascii=False) if ats else 'default'}")
    usage = sorted(k for k in info if k.startswith("NS") and k.endswith("UsageDescription"))
    print(f"  permission strings ({len(usage)}): {', '.join(k[2:-16] for k in usage)}")

    print("\n## Privacy manifests — the developer's declarations, not observed behaviour")
    if not privacy:
        print("  none found")
    for rel in privacy:
        p = read_plist(os.path.join(app, rel)) or {}
        types = [t.get("NSPrivacyCollectedDataType", "?").replace("NSPrivacyCollectedDataType", "")
                 for t in p.get("NSPrivacyCollectedDataTypes", []) if isinstance(t, dict)]
        apis = [t.get("NSPrivacyAccessedAPIType", "?").replace("NSPrivacyAccessedAPICategory", "")
                for t in p.get("NSPrivacyAccessedAPITypes", []) if isinstance(t, dict)]
        print(f"  {rel}: tracking={p.get('NSPrivacyTracking')}")
        if p.get("NSPrivacyTrackingDomains"):
            print(f"    tracking domains: {', '.join(p['NSPrivacyTrackingDomains'])}")
        if types:
            print(f"    collects: {', '.join(types)}")
        if apis:
            print(f"    required-reason APIs: {', '.join(apis)}")

    print("\n## Before any of this becomes a claim")
    print("  All of it is [package]: it proves what shipped in this bundle, not what runs.")
    print("  Screen recording: QuickTime over USB or the app running on this Mac, then frame_diff.py.")
    print("  UI built with nibs/storyboards: REA decode-interface-builder (references/rea.md).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("app", help="/Applications/X.app (iPhone app on a Mac) or a .app directory")
    a = ap.parse_args()
    try:
        report(a.app)
    except (OSError, ValueError) as e:
        sys.exit(f"ios_bundle failed: {e}")


if __name__ == "__main__":
    main()
