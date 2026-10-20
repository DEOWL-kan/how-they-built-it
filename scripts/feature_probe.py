#!/usr/bin/env python3
"""Evidence for what a feature is built out of — not what you guess it is built out of.

The visual half of this toolkit measures pixels. This measures composition: which
native libraries ship, which third-party SDKs are linked, which hosts and API
paths appear as string constants. Those four answer questions that are otherwise
pure speculation — is the transcription on-device or in the cloud, do they roll
their own billing, is there a hardware/firmware path at all.

Every line of output names the file it came from. A claim you cannot trace to a
file is a guess, and this script exists so you stop shipping guesses.

⛔ READ THIS BEFORE USING THE OUTPUT
  - A string in a binary is NOT a shipped feature. Dead code, unlaunched work and
    A/B-gated branches all leave strings behind. Confirm on a real device before
    any claim that a feature exists.
  - Endpoints found here are for understanding structure. ⛔ Do not call them,
    probe them, or publish the list. That stops being analysis.

⚠️ Split APKs matter here, unlike for a visual teardown. Measured on three shipping
   apps, every native library sat in `split_config.<abi>.apk` and base.apk held none —
   and feature splits can hold whole modules. Pass every APK `pm path` returned, and
   say in the report which ones you actually had.
⚠️ Flutter/RN apps keep their business logic OUT of dex. For a Flutter app the dex
   holds only the Android-side SDKs; the app itself is AOT-compiled into
   `lib/*/libapp.so`. Reading dex alone yields a confident, wrong "this app has no
   features" picture.
⚠️ Hybrid apps keep features in H5. A Flutter shell can open a WebView whose whole
   feature lives in `assets/**/*.js` (or an offline-package .zip inside assets) —
   measured on one app, a keyword in that JS returned "no matches" because only
   .so/.dex/RN bundles were read. .js/.mjs/.html/.htm and nested .zip are read now;
   H5 fetched at runtime or packed in an unrecognised/encrypted archive is still
   uncovered, so the header prints how many web files were actually read.

stdlib only. Usage:
    feature_probe.py base.apk split_config.arm64_v8a.apk
    feature_probe.py *.apk --keyword transcri,chapter,summar
    feature_probe.py base.apk --section sdk,host
"""
import argparse
import collections
import io
import os
import plistlib
import re
import struct
import zipfile
from urllib.parse import urlsplit

SECTIONS = ("native", "sdk", "dart", "host", "api", "keyword")

# Native libraries whose presence answers a real architecture question.
LIB_MEANING = [
    # Wording matters: shipping a runtime is not the same as using it. These say
    # what is BUNDLED. Whether the feature actually runs through it is a [device]
    # question, and the report must not promote one to the other.
    (r"onnxruntime|tensorflowlite|tflite|mlkit|ncnn|mnn|executorch",
     "on-device inference runtime bundled — capable of running a model locally"),
    (r"whisper|sherpa|kaldi|vosk",
     "on-device speech recognition engine bundled"),
    (r"avcodec|avformat|avfilter|ffmpeg",
     "ffmpeg bundled — can transcode/decode locally"),
    (r"audioeffect|webrtc|rnnoise|speex|opus",
     "audio processing bundled (denoise / AEC / codec)"),
    (r"^libapp\.so$|flutter", "Flutter — business logic is in libapp.so, NOT in dex"),
    (r"hermes|reactnative|jsc", "React Native — logic is in the JS bundle, NOT in dex"),
    (r"rive|lottie|skia", "vector animation runtime"),
    (r"pdfium|pdf", "PDF rendering"),
    (r"sqlcipher", "encrypted local database"),
    (r"realm|sqlite", "local database"),
]

SKIP_HOSTS = re.compile(
    r"(^|\.)(w3\.org|apache\.org|adobe\.com|openxmlformats\.org|xml\.org|json-schema\.org"
    r"|schemas\.microsoft\.com|aomedia\.org|dashif\.org|whatwg\.org|iana\.org|gnu\.org"
    r"|oracle\.com|sun\.com|example\.com|localhost|127\.0\.0\.1)$")


def uleb128(buf, i):
    result = shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, i


def dex_strings(data, source=""):
    """Every string constant in a .dex, straight out of the string_ids table.

    `source` is quoted in every diagnostic: across a dozen splits, "one file was
    refused" is useless unless you know which, and the report has to say which
    files are uncovered.
    """
    at = f" [{source}]" if source else ""
    if data[:4] != b"dex\n":
        return []
    try:
        count = struct.unpack_from("<I", data, 0x38)[0]
        table = struct.unpack_from("<I", data, 0x3C)[0]
    except struct.error:
        return []                       # truncated header
    # Bounds-check before the loop, not inside it. A corrupt header once produced
    # a table offset of ~993 MB against a 1 KB buffer and took the whole probe
    # down — one odd file in an APK must not cost you the other twenty.
    if table + count * 4 > len(data):
        return []
    # The dex spec fixes header_size at 0x70. Trusting the file's own value let a
    # crafted header_size=1 pass the bounds check and return the file magic
    # ("x\n035") as if it were app data — silently wrong evidence.
    declared = struct.unpack_from("<I", data, 0x24)[0]
    if declared != 0x70:
        # Say so. Returning an empty list in silence is the failure mode this whole
        # script exists to avoid — "no strings" and "I refused to read it" must look
        # different to the person reading the report.
        print(f"  ! header_size is {declared}, not 0x70 — refusing to parse{at}")
        return []
    header_size = 0x70
    out, skipped = [], 0
    for k in range(count):
        off = struct.unpack_from("<I", data, table + k * 4)[0]
        # An offset inside the header is not a string. Unchecked, offset 0 decoded
        # the file magic and returned "ex\n" as if it were app data — silently
        # wrong evidence is worse than no evidence.
        # Also reject offsets inside the string_ids table itself.
        if not max(header_size, table + count * 4) <= off < len(data):
            skipped += 1
            continue
        try:
            _, pos = uleb128(data, off)
            end = data.index(b"\x00", pos)
            # dex uses MUTF-8, not UTF-8. Two differences, both checked:
            #  - NUL is encoded C0 80, so a raw 0x00 really is the terminator and
            #    scanning for it above is safe (the uleb128 length is redundant here)
            #  - supplementary-plane chars (emoji) use CESU-8 surrogate pairs that a
            #    UTF-8 decoder rejects -> U+FFFD. ASCII and the whole BMP (Chinese,
            #    Indonesian, …) decode correctly, and everything this script looks for
            #    (hosts, class prefixes, endpoints, keyword stems) is ASCII.
            #    ponytail: not worth a MUTF-8 decoder until someone needs emoji.
            out.append(data[pos:end].decode("utf-8", "replace"))
        except (IndexError, ValueError):
            skipped += 1
            continue
    if skipped:
        # Report it. "No hits" and "hits I could not read" must not look the same.
        print(f"  ! {skipped} of {count} string entries were unreadable and skipped{at}")
    return out


def binary_strings(data, minlen=6):
    """Printable runs out of a compiled .so — how you read a Flutter/RN app.

    Returns (text, complete) pairs, where `complete` is a HEURISTIC judgement, not
    a fact: a run is *treated as* a fragment when readable text appears to carry
    on past where this ASCII scan had to stop — that is, when the bytes right after
    it begin a valid non-ASCII UTF-8 character.

    That distinction is not pedantry. `/api/v1/usersérchive` arrives here as the
    fragment `/api/v1/users`, a perfectly well-formed endpoint that never existed.

    ⚠️ The opposite rule — "complete only if followed by NUL" — was tried and is
    wrong: compiled Dart stores strings against arbitrary binary, so it flagged
    111738 of one library's strings as fragments and threw the whole API surface
    away. So plain binary is *treated as* "the string ended" — a judgement call that
    keeps recall, not a fact. Text-looking bytes (a UTF-8 character, a control byte
    other than NUL) are *treated as* "it did not".
    """
    out = []
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, data):
        out.append((m.group(0).decode("ascii", "ignore"),
                    not (_text_before(data, m.start()) or _text_after(data, m.end()))))
    return out


def _text_after(data, end):
    """Does a non-ASCII CHARACTER start right here? Then the run was cut short.

    Decode exactly one character, sized from its lead byte. Grabbing a fixed
    4-byte window instead let `usersé中archive` (window ends mid-character) and
    `users\xc3\xa9\x00\xff` (window holds a stray byte) fail to decode and get
    waved through as complete. And no .strip(): U+00A0 is a real character that
    text continues with, even though it looks like nothing.
    """
    lead = data[end:end + 1]
    # The printable-run regex cuts on EVERY control byte, so by the time
    # candidate_path sees the string the control character is already gone and it
    # cannot refuse it: `/api/v1/users\x01archive` and `…\x7farchive` both arrived
    # as a clean, complete-looking `/api/v1/users`. Treat any C0 except NUL, plus
    # DEL, as "something was cut here".
    #
    # NUL stays a clean terminator, and that is what keeps recall: measured on a
    # real libapp.so, 124 of 125 endpoint candidates are followed by a byte >= 0x80
    # (Dart's length prefix for the next record) and exactly ONE by a C0 byte. So
    # this costs one path out of 125 and closes the whole bypass.
    if lead and (0x01 <= lead[0] <= 0x1F or lead[0] == 0x7F):
        return True
    if not lead or lead[0] < 0xC2 or lead[0] > 0xF4:
        return False                     # NUL, plain binary, or a stray continuation byte
    size = 2 if lead[0] < 0xE0 else 3 if lead[0] < 0xF0 else 4
    try:
        data[end:end + size].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False                     # not a character — treated as "it ended here"


def _text_before(data, start):
    """Does a whole non-ASCII character appear to end right where this run begins?

    ⚠️ HEURISTIC, and it misfires. Measured on a real Dart snapshot, two complete
    path records were rejected because the two bytes before them happened to form
    a valid UTF-8 sequence — one byte was the tail of the PREVIOUS string, the other
    was this record's own length byte. "These bytes decode as a character" is not
    evidence that the character belongs to this string.


    Truncation cuts from the left too: `中文/api/v1/users` yields the ASCII run
    `/api/v1/users`, a complete-looking path that is only the back half of the
    real string.

    ⚠️ "the previous byte is 0x80-0xBF" is NOT the test. A quarter of all byte
    values fall in that range, so in compiled binaries it fired constantly —
    measured on one real library it turned 152 fragments into 57664 and threw
    most of the API surface away. Require an actual character: a valid lead byte
    whose sequence ends exactly where this run begins.
    """
    prev = data[start - 1:start] if start else b""
    if prev and (0x01 <= prev[0] <= 0x1F or prev[0] == 0x7F):
        return True                      # something was cut off on the left as well
    for size in (2, 3, 4):
        if start < size:
            continue
        seq = data[start - size:start]
        lead = seq[0]
        if lead < 0xC2 or lead > 0xF4:
            continue
        expected = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
        if expected != size:
            continue
        try:
            seq.decode("utf-8")
            return True
        except UnicodeDecodeError:
            continue
    return False


WEB_TEXT = (".js", ".mjs", ".html", ".htm")


MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"}       # 64/32-bit, little-endian
FAT_MAGICS = {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}         # fat / fat64, big-endian
LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64 = 0x21, 0x2C


def is_macho(head):
    # 0xcafebabe is also a Java class file; a fat Mach-O has a small arch count.
    if head[:4] in FAT_MAGICS:
        return len(head) >= 8 and 0 < struct.unpack(">I", head[4:8])[0] < 20
    return head[:4] in MACHO_MAGICS


def macho_encryption(data):
    """[(file_offset, size, cryptid)] for every LC_ENCRYPTION_INFO in every slice.

    App Store binaries carry FairPlay encryption over a declared range only —
    measured on iPhone apps installed on a Mac: one 4096-byte page of the main
    executable, frameworks cryptid 0. This reports the range so callers can skip
    it; nothing here decrypts anything.
    """
    slices = []
    if data[:4] in FAT_MAGICS:
        wide = data[:4] == b"\xca\xfe\xba\xbf"
        count = struct.unpack_from(">I", data, 4)[0]
        step = 32 if wide else 20
        for k in range(min(count, 20)):
            at = 8 + k * step
            if at + step > len(data):
                break
            offset = (struct.unpack_from(">Q", data, at + 8)[0] if wide
                      else struct.unpack_from(">I", data, at + 8)[0])
            slices.append(offset)
    else:
        slices.append(0)
    found = []
    for base in slices:
        head = data[base:base + 4]
        if head not in MACHO_MAGICS:
            continue
        wide = head == b"\xcf\xfa\xed\xfe"
        try:
            ncmds = struct.unpack_from("<I", data, base + 16)[0]
        except struct.error:
            continue
        at = base + (32 if wide else 28)
        for _ in range(min(ncmds, 4096)):
            try:
                cmd, size = struct.unpack_from("<II", data, at)
            except struct.error:
                break
            if cmd in (LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64):
                off, length, cid = struct.unpack_from("<III", data, at + 8)
                found.append((base + off, length, cid))
            if size < 8:
                break
            at += size
    return found


def without_encrypted(data):
    """The bytes with every encrypted range blanked: those bytes are ciphertext,
    not text, and they are the one part of an App Store binary we never read."""
    ranges = [(o, n) for o, n, cid in macho_encryption(data) if cid]
    if not ranges:
        return data
    buf = bytearray(data)
    for offset, size in ranges:
        buf[offset:offset + size] = bytes(min(size, max(0, len(buf) - offset)))
    return bytes(buf)


def resolve_app(path):
    """An iPhone app installed on an Apple Silicon Mac is /Applications/X.app with
    the real bundle at Wrapper/Y.app. Only Y.app is ever read: Wrapper/ also holds
    iTunesMetadata.plist, which can carry the purchaser's account details."""
    wrapped = False
    wrapper = os.path.join(path, "Wrapper")
    if os.path.isdir(wrapper):
        inner = [n for n in sorted(os.listdir(wrapper)) if n.endswith(".app")]
        if len(inner) != 1:
            raise ValueError(f"{path}: Wrapper/ holds {len(inner)} .app bundles, expected 1")
        path, wrapped = os.path.join(wrapper, inner[0]), True
    # A Wrapper/ alone proves nothing: one Mac app on the test machine had
    # Wrapper/X.app holding only an icon and an Info.plist. Require the executable.
    try:
        with open(os.path.join(path, "Info.plist"), "rb") as f:
            exe = plistlib.load(f).get("CFBundleExecutable")
        with open(os.path.join(path, exe), "rb") as f:
            ok = is_macho(f.read(8))
    except (OSError, ValueError, AttributeError, TypeError, plistlib.InvalidFileException):
        ok = False
    if not ok:
        raise ValueError(f"{path}: no Info.plist naming a Mach-O executable — not an app bundle")
    return path, wrapped


def _scan_dir(root, label, native, strings):
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(folder, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root)
            src = f"{label}:{rel}"
            low = name.lower()
            with open(full, "rb") as f:
                head = f.read(8)
            if is_macho(head):
                with open(full, "rb") as f:
                    data = f.read()
                native.append((rel, len(data), src))
                strings += [(s, src, ok) for s, ok in binary_strings(without_encrypted(data))]
            elif low.endswith(WEB_TEXT):
                with open(full, "rb") as f:
                    text = f.read().decode("utf-8", "replace")
                strings += [(line, src, True) for line in text.splitlines() if line.strip()]
            elif low.endswith(".zip"):
                try:
                    with zipfile.ZipFile(full) as z:
                        _scan_zip(z, src, native, strings, 1)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
                    print(f"  ! stopped reading nested archive ({e}): {src}")


def collect(paths):
    """Returns (native, strings) where strings is [(text, source_file, complete)].

    Accepts APKs and, for iOS, an .app bundle directory (or /Applications/X.app
    holding an iPhone app under Wrapper/)."""
    native, strings = [], []
    for path in paths:
        if isinstance(path, str) and os.path.isdir(path):
            app, wrapped = resolve_app(path)
            if wrapped:
                print(f"  ! {os.path.basename(path)} is the Mac copy of an iPhone app — the store thinned it")
                print("    for one device class; ios_bundle.py prints which (idiom 1 iPhone, 2 iPad)")
            _scan_dir(app, os.path.basename(app), native, strings)
            continue
        if not zipfile.is_zipfile(path):
            print(f"  ! skipped (not a zip/apk): {path}")
            continue
        label = os.path.basename(path) if isinstance(path, str) else "<stream>"
        with zipfile.ZipFile(path) as z:
            _scan_zip(z, label, native, strings)
    return native, strings


def _scan_zip(z, label, native, strings, depth=0):
    for info in z.infolist():
        name = info.filename
        # Full member path, not the basename: lib/arm64-v8a/libfoo.so and
        # lib/x86_64/libfoo.so are different files and must not share a label.
        src = f"{label}:{name}"
        low = name.lower()
        if name.endswith(".so"):
            native.append((name.split("/")[-1], info.file_size, src))
            strings += [(s, src, ok) for s, ok in binary_strings(z.read(info))]
        elif name.endswith(".dex"):
            # dex strings come from the string table with an explicit
            # terminator, so they are always complete.
            strings += [(s, src, True) for s in dex_strings(z.read(info), src)]
        elif name.endswith((".bundle", ".jsbundle")) or name.endswith("index.android.bundle"):
            # React Native keeps its whole app here, the way Flutter keeps
            # it in libapp.so. Skipping it meant claiming RN coverage we
            # did not have.
            strings += [(s, src, ok) for s, ok in binary_strings(z.read(info), 4)]
        elif low.endswith(WEB_TEXT):
            # H5 is text, so decode it rather than scanning ASCII runs: a Chinese
            # keyword in the JS must match. One line is one string; report_api
            # still only accepts a line that is wholly a path, so nothing is carved.
            text = z.read(info).decode("utf-8", "replace")
            strings += [(line, src, True) for line in text.splitlines() if line.strip()]
        elif low.endswith(".zip"):
            # Offline H5 packages ship as a zip inside assets. Depth-capped: a
            # competitor package is untrusted input.
            data = z.read(info)
            try:
                if depth >= 2 or not zipfile.is_zipfile(io.BytesIO(data)):
                    raise zipfile.BadZipFile("not a readable zip" if depth < 2 else "nested too deep")
                with zipfile.ZipFile(io.BytesIO(data)) as inner:
                    _scan_zip(inner, src, native, strings, depth + 1)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
                # Encrypted or custom offline packages land here. Say so — this
                # file is uncovered, not empty.
                print(f"  ! stopped reading nested archive ({e}): {src}")


def report_native(native):
    print("## Native libraries — what the app can do on the device itself")
    if not native:
        print("  none found in the analysed packages.")
        print("  Native libraries may live in base or ABI splits (split_config.<abi>.apk).")
        print("  Check every APK returned by pm path; missing splits are uncovered.")
        return
    for lib, size, src in sorted(native, key=lambda r: -r[1]):
        note = next((m for pat, m in LIB_MEANING if re.search(pat, lib, re.I)), "")
        print(f"  {size//1024:7d} KB  {lib:<28} {note}")
        print(f"                       ^ {src}")


def report_sdk(strings):
    counts, src = collections.Counter(), {}
    for text, where, _ in strings:
        m = re.match(r"^L(com|io|ai|org|net|dev)/([a-z0-9_]+)/([a-z0-9_]+)", text)
        if m:
            key = ".".join(m.groups())
            counts[key] += 1
            src.setdefault(key, where)
    print("\n## Third-party packages (package-prefix hits in the string table)")
    print("   ⚠️  This counts STRING HITS, not class definitions — it does not read")
    print("       class_defs. Treat the number as 'how loudly this package appears'.")
    if not counts:
        print("  none — dex carries no third-party packages.")
        print("  ⚠️  Normal for Flutter/React Native. Their stack is in the .so / JS bundle,")
        print("      ⛔ do NOT read this as 'they built everything themselves'.")
        return
    for key, n in counts.most_common(20):
        print(f"  {n:6d}  {key:<40} ({src[key]})")


DART_PACKAGE = re.compile(r"package:([a-z][a-z0-9_]*)/")


def report_dart(strings):
    """Every Dart package whose library URIs survive in libapp.so.

    Flutter keeps its third-party stack out of dex, so report_sdk says "none" for a
    Flutter app. The AOT snapshot still carries `package:<name>/…` library URIs.
    REA's inspect-dart-aot returns at most 500 of them in alphabetical order —
    measured on one app it stopped at `package:flutter/…` and missed a direct
    dependency — so this lists every name instead of a capped projection.
    """
    counts, src = collections.Counter(), {}
    libapp = False
    for text, where, _ in strings:
        # Android: lib/<abi>/libapp.so. iOS: Frameworks/App.framework/App.
        if not where.endswith(("libapp.so", "App.framework/App")):
            continue
        libapp = True
        for m in DART_PACKAGE.finditer(text):
            counts[m.group(1)] += 1
            src.setdefault(m.group(1), where)
    print("\n## Dart packages in libapp.so (Flutter third-party stack)")
    if not libapp:
        print("  no libapp.so read — not a Flutter AOT app, or its ABI split was not supplied.")
        return
    if not counts:
        print("  libapp.so read, no package: URIs found — uncovered, not 'no dependencies'.")
        return
    print(f"  {len(counts)} packages. Count = string hits, not code size.")
    for name in sorted(counts):
        print(f"  {counts[name]:6d}  {name:<40} ({src[name]})")
    print("  ⚠️  A URI here means some of that package was compiled in; it does not say")
    print("      which feature uses it or that the code runs. `flutter`, `dart:`-adjacent")
    print("      and the app's own package appear too. Not measured on --obfuscate builds.")


def report_host(strings):
    counts, src = collections.Counter(), {}
    for text, where, _ in strings:
        for m in re.finditer(r"https?://[^\s\"'<>\\]+", text):
            # urlsplit, not a hand-rolled regex: `https://user@real.example.io/x`
            # read as host "user" with the naive one.
            try:
                host = urlsplit(m.group(0)).hostname
            except ValueError:
                continue
            if not host or SKIP_HOSTS.search(host):
                continue
            counts[host] += 1
            src.setdefault(host, where)
    print("\n## Hosts — the cloud side of the feature")
    if not counts:
        print("  none found.")
        return
    for host, n in counts.most_common(20):
        print(f"  {n:5d}  {host:<46} ({src[host]})")


# A dex type descriptor looks like `Lcom/google/android/gms/common/api/Foo;`.
# Left alone, the endpoint regex carves `/api/Foo` out of it and reports Java
# class paths as REST endpoints — measured on one app, every single dex "endpoint"
# was a class name. Reject the descriptor, and require REST-shaped segments
# (lowercase/digit/-/_), which class names fail on their capitals.
# Anchored on the WHOLE path, deliberately. Unanchored, this carved `/v1/icons/logo`
# out of `/assets/v1/icons/logo.png` and truncated long segments into paths that
# never existed. Precision over recall: a path embedded mid-sentence is missed, and
# that is the right trade when the output is called evidence.
# Segments allow camelCase (`/api/v1/getUser`) and `{id}` / `:id` templates, both
# of which appear verbatim in shipped code. A leading lowercase letter or digit
# still keeps Java class descriptors out.
ENDPOINT = re.compile(r"^/(?:api|v\d+)(?:/[a-z0-9{:][A-Za-z0-9_\-{}:.]{0,63})+/?$")


def candidate_path(text):
    """The path part of a string, if the string is a URL or a bare path.

    One malformed URL must not end the run: `https://[broken` raises ValueError
    out of urlsplit, and unguarded it took every later finding down with it.
    """
    # ⛔ Nothing here may rewrite the string. Every "harmless" normalisation turned
    # out to fabricate a path from a complete one:
    #   .strip('"\'') -> `…/api/v1/users'` became `…/api/v1/users`
    #   .strip()       -> `\u00a0/api/v1/users` and `/api/v1/users\u00a0` both became
    #                     `/api/v1/users`, even though the binary side had just
    #                     correctly ruled U+00A0 a real character
    #   urlsplit()     -> silently deletes TAB/CR/LF from a URL path, so
    #                     `/api/v1/us\ters` reports as `/api/v1/users`
    # Anything whose exact bytes we cannot vouch for is refused instead.
    # Validate the RAW string before anything parses it. urlsplit silently strips
    # leading whitespace and C0 control characters, so ` https://e.test/api/v1/users`
    # and `\x01https://…` both came back as a clean `/api/v1/users` even after the
    # explicit TAB/CR/LF check — the normalisation happened one layer below it.
    if text != text.strip() or any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        return None
    if "://" in text:
        try:
            split = urlsplit(text)
        except ValueError:
            return None
        return split.path
    return text.split("#", 1)[0] if text.startswith("/") else None


def report_api(strings):
    """Endpoints, and ONLY where the whole string is the path.

    There used to be a second pass that dug URLs out of long lines, so that minified
    JS bundles would yield endpoints too. Six review rounds produced six different
    ways for it to invent a path that never existed — a comma, a backslash escape,
    a single quote inside a double-quoted literal, a non-ASCII byte upstream — each
    leaving a clean, plausible, wrong `/api/v1/users`. Every fix was a new terminator
    to special-case, and the next reviewer always found another one.

    So it is gone. A whole complete string that IS a path, or nothing. Minified
    bundles still give up their hosts and keyword hits; their paths are out of reach,
    and the report says so rather than guessing.
    """
    seen, fragments = {}, 0
    for text, where, complete in strings:
        if not complete:
            fragments += 1
            continue
        path = candidate_path(text)
        if not path:
            continue
        path = path.split("?", 1)[0]            # /api/v1/users?limit=10
        if ENDPOINT.match(path):
            seen.setdefault(path, where)
    print("\n## API paths — read these as a FEATURE MAP, nothing more")
    if fragments:
        print(f"  ({fragments} strings rejected as possibly truncated — heuristic,")
        print(f"   it does misfire: on one measured libapp.so it rejected 3 of 125 paths")
        print(f"   that look complete — 2 from the UTF-8 rule, 1 from the control-byte rule)")
    if not seen:
        print("  none matched.")
    for path, where in sorted(seen.items())[:40]:
        print(f"  {path:<56} ({where})")
    if len(seen) > 40:
        print(f"  ... {len(seen) - 40} more")
    print("  ⚠️  'none matched' is NOT 'this app has no API'. Only a COMPLETE string that")
    print("      is itself a path starting /api or /v<n> counts. A URL embedded in a")
    print("      minified line, a path built by concatenation at runtime, or one cut")
    print("      short by a non-ASCII byte is deliberately invisible — every attempt to")
    print("      recover those produced endpoints that did not exist.")
    if not seen:
        return
    print("  ⚠️  Some of these belong to third-party SDKs, not to the app — check the source")
    print("     file and cross-reference the host list before calling anything 'their API'.")
    print("  ⛔ Do not call these, probe them, or publish the list. Group them into")
    print("     feature areas in your report; the raw list is not a deliverable.")


def report_keyword(strings, terms, whole_word=False):
    print(f"\n## Strings matching {terms}")
    hits = collections.defaultdict(set)
    low = [t.lower() for t in terms]
    patterns = [re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)") for t in low] if whole_word else []
    for text, where, _ in strings:
        if len(text) < 3:
            continue
        t = text.lower()
        for index, term in enumerate(low):
            if whole_word:
                match = patterns[index].search(t)
                pos = match.start() if match else -1
            else:
                pos = t.find(term)
            if pos < 0:
                continue
            # Don't drop long lines — a minified bundle is one 200KB string and the
            # old length cap threw every hit in it away. Show a window instead.
            if len(text) <= 200:
                hits[term].add((text, where))
            else:
                start = max(0, pos - 40)
                hits[term].add(("…" + text[start:pos + 60] + "…", where))
    if not any(hits.values()):
        print("  no matches — the vocabulary may differ, try shorter stems.")
        return
    for term in low:
        rows = sorted(hits[term])[:18]
        print(f"\n  [{term}] {len(hits[term])} match(es)")
        for text, where in rows:
            print(f"    {text[:88]:<88} ({where})")
    print("\n  ⛔ A matching string is NOT a shipped feature. Dead code, unlaunched work")
    print("     and A/B-gated branches all leave strings. Confirm on a real device.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("apks", nargs="+", help="base.apk AND every split you pulled; or an iOS .app directory")
    ap.add_argument("--keyword", help="comma-separated stems to hunt, e.g. transcri,chapter")
    ap.add_argument("--whole-word", action="store_true",
                    help="match literal keywords bounded by non-word characters (Unicode; underscore is a word character)")
    ap.add_argument("--section", help=f"comma-separated subset of {','.join(SECTIONS)}")
    a = ap.parse_args()

    want = set(a.section.split(",")) if a.section else set(SECTIONS)
    try:
        native, strings = collect(a.apks)
    except ValueError as e:
        raise SystemExit(f"feature_probe: {e}")
    web = {w for _, w, _ in strings if w.lower().endswith(WEB_TEXT)}
    print(f"# feature_probe — {len(a.apks)} file(s), {len(strings)} strings, {len(native)} native libs, "
          f"{len(web)} H5 files (.js/.html)\n")
    if not web:
        print("  ⚠️  No H5 read. If the feature runs in a WebView, its code was fetched at runtime")
        print("      or sits in an archive this script cannot open — uncovered, not absent.\n")
    # Where this script's ceiling is, say what goes past it.
    if web:
        print("  → H5 found. Module graph and endpoint trace past this script: references/rea.md\n")
    if any(lib == "libapp.so" or lib.endswith("App.framework/App") for lib, _, _ in native):
        print("  → Flutter. Full Dart package list: --section dart. Snapshot hash / engine")
        print("    build id: REA identify-flutter-build (references/rea.md)\n")

    if "native" in want:
        report_native(native)
    if "sdk" in want:
        report_sdk(strings)
    if "dart" in want:
        report_dart(strings)
    if "host" in want:
        report_host(strings)
    if "api" in want:
        report_api(strings)
    if a.keyword and "keyword" in want:
        report_keyword(strings, [t.strip() for t in a.keyword.split(",") if t.strip()], a.whole_word)

    print("\n## Before any of this becomes a claim")
    print("  Everything above is STATIC evidence: it proves the code is in the package,")
    print("  ⛔ not that the feature is live, reachable, or on for every user.")
    print("  Drive the feature on a real device, then label each finding:")
    print("    [device]   you saw it happen        [package]  traced to a file above")
    print("    [public-source] official statement + URL/date (not device verification)")
    print("    [user-report] user statement + author/URL/date (not reproduced here)")
    print("    [inferred] the evidence does not carry the claim — including when you")
    print("               have multiple tags but they support a weaker statement")


if __name__ == "__main__":
    main()
