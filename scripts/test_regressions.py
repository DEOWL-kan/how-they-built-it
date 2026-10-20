#!/usr/bin/env python3
"""Regression samples for the bugs the 2026-09-18 cross-case run found.

Every assert here corresponds to an entry in references/pitfalls.md (9-14).
stdlib only, no framework:  python3 scripts/test_regressions.py
"""
import os
import contextlib
import io
import re
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apk_assets
import feature_probe
import frame_diff
import image_probe


def test_short_token_boundaries():
    """pitfall 9 — `ble` must not match drawable/variable/double/cable."""
    for name in ("res/drawable/ic_cog.png",
                 "assets/fonts/InterVariable.ttf",
                 "assets/double_color_ball_animation.json",
                 "assets/images/cable_sync_blue.png",
                 "assets/icons/repair_flow.png"):
        assert apk_assets.classify_screen(name) != "device", name
    for name in ("assets/ble_scan.png", "assets/bt/ble/pairing_hint.png",
                 "assets/device_reset_step1.png"):
        assert apk_assets.classify_screen(name) == "device", name
    # `auth` still wins for real auth assets but not for an author avatar.
    assert apk_assets.classify_screen("assets/auth/apple.png") == "login"
    assert apk_assets.classify_screen("assets/oauth_google.png") == "login"
    assert apk_assets.classify_screen("assets/author_avatar.png") is None
    # fonts are app-wide and must never be bucketed by screen
    assert apk_assets.classify_screen("assets/fonts/InterVariable.ttf", "font") is None
    assert apk_assets.classify_screen("assets/empty_text/Inter-Bold.ttf", "font") is None


def test_contrast_checks_rare_and_middle_pixels_without_grid():
    """Sparse and middle-luminance backgrounds must not get a false pass."""
    from unittest.mock import patch
    for cells, fg in [(["#000000"] * 179 + ["#FFFFFF"], "#FFFFFF"),
                      (["#000000", "#757575", "#FFFFFF"] * 60, "#757575"),
                      (["#000000"] * 180, "#FFFFFF")]:
        raw = bytes.fromhex("".join(c[1:] for c in cells))
        expected = "21.00" if len(set(cells)) == 1 else "1.00"
        for grid in ("0", "9x20"):
            with patch.object(sys, "argv", ["image_probe.py", "bg.png", "--grid", grid,
                                            "--contrast", fg]), \
                 patch.object(image_probe, "require_ffmpeg"), \
                 patch.object(image_probe, "dimensions", return_value=(9, 20, "rgb24")), \
                 patch.object(image_probe.os.path, "getsize", return_value=len(raw)), \
                 patch.object(image_probe, "raw_rgb", return_value=raw):
                out = _capture(image_probe.main)
            assert re.search(r"minimum pixel contrast\s+" + re.escape(expected), out), out
            assert "AA ok" not in out, out
            assert "not an accessibility verdict" in out, out



def test_chroma_peak_role():
    """pitfall 8 sibling — chroma peak on a LIGHT image is not the light."""
    light = ["#FFFFFF"] * 8 + ["#EAF0F7"] * 8 + ["#BDD8F7"]
    assert "FAR END" in image_probe.peak_role("#BDD8F7", light)
    dark = ["#0B1014"] * 8 + ["#16202A"] * 8 + ["#3E6EA8"]
    assert "light source" in image_probe.peak_role("#3E6EA8", dark)


def test_not_a_loop():
    """pitfall 11 — a one-shot launch sequence is not a cycle."""
    assert frame_diff.loop_verdict([2.05, 1.15]) == "not-a-loop"   # measured cold start
    assert frame_diff.loop_verdict([3.00, 2.95, 3.05]) == "loop"   # measured crossfade
    assert frame_diff.loop_verdict([1.60]) == "one-gap"            # can't tell yet


def test_min_run_follows_sample_rate():
    """pitfall 7 — a hard-coded 0.2s floor eats real 0.10s static holds."""
    fps, quiet = 20.0, 0.1
    diffs, t = [], 0.0
    for _ in range(3):                      # hold 0.10s, move 0.60s, three times
        for v in [0.0, 0.0] + [5.0] * 12:
            t += 1 / fps
            diffs.append((t, v))
    runs = frame_diff.segments(diffs, fps, quiet)
    holds = [r for r in runs if r[0] == "quiet"]
    assert len(holds) >= 2, runs             # 0.2s floor would merge them all away
    # and the same series with a 0.2s floor collapses, which is the bug
    assert len([r for r in frame_diff.segments(diffs, fps, quiet, min_run=0.2)
                if r[0] == "quiet"]) < len(holds)


def test_chroma_not_hls_saturation():
    """pitfall 8 — a near-white tinted pixel must not hijack the peak.

    Cell 0 is near-white with a faint tint (HLS saturation ~100%, chroma 2),
    cell 1 is the real tint (chroma 58). The peak must be cell 1.
    """
    import colorsys
    grid = [(0xFD, 0xFD, 0xFF), (0xBD, 0xD8, 0xF7), (0xFF, 0xFF, 0xFF)]
    hls_sat = [colorsys.rgb_to_hls(*[c / 255 for c in px])[2] for px in grid]
    assert hls_sat[0] > hls_sat[1], hls_sat          # the trap is real
    data = bytes(b for px in grid for b in px)
    chroma, x, y, hx = image_probe.saturation_peak(data, 3, 1)
    assert (x, hx) == (1, "#BDD8F7"), (x, hx)        # chroma picks the right one


def test_suggest_crop_finds_the_moving_region():
    """roadmap 4 — the box must cover what moved and nothing else."""
    sw = sh = 10
    flat = bytearray([100]) * (sw * sh)
    moved = bytearray(flat)
    for y in range(2, 5):                       # cells x=4..6, y=2..4
        for x in range(4, 7):
            moved[y * sw + x] = 255
    box = frame_diff.suggest_crop([bytes(flat), bytes(moved)], sw, sh, 100, 100)
    assert box == (30, 30, 40, 20), box
    # a clip where nothing changes has no region to suggest
    assert frame_diff.suggest_crop([bytes(flat), bytes(flat)], sw, sh, 100, 100) is None


def _capture(fn, *args):
    """Run a report function and return what it actually printed.

    Asserting on constants instead of output is how four tests here stayed green
    while an adversarial reviewer replaced every report_* with a no-op.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


def _run_quiet(fn, *args):
    """Call fn, swallowing its diagnostics, and return the value."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


def _tiny_dex(words):
    """A minimal dex: real header fields + string_ids table + string_data.

    Not a fully valid dex (no map, no class_defs) — just enough to exercise the
    string-table path, which is all this parser reads.
    """
    header = bytearray(0x70)
    header[0:8] = b"dex\n035\x00"
    data, offsets = bytearray(), []
    base = 0x70 + 4 * len(words)
    def uleb(n):                                    # real multi-byte encoding, so a
        out = bytearray()                           # "skip one byte" stand-in for the
        while True:                                 # decoder fails instead of passing
            b = n & 0x7F
            n >>= 7
            out.append(b | 0x80 if n else b)
            if not n:
                return bytes(out)
    for w in words:
        offsets.append(base + len(data))
        raw = w.encode("utf-8")
        data += uleb(len(w)) + raw + b"\x00"
    struct.pack_into("<I", header, 0x24, 0x70)         # header_size
    struct.pack_into("<I", header, 0x38, len(words))   # string_ids_size
    struct.pack_into("<I", header, 0x3C, 0x70)         # string_ids_off
    table = b"".join(struct.pack("<I", o) for o in offsets)
    return bytes(header) + table + bytes(data)


def test_dex_strings_parses_the_string_table():
    """feature_probe — the dex parser must read real string constants out."""
    # One word is >127 chars on purpose: its uleb128 length needs two bytes, so a
    # decoder that just skips one byte reads the string one character short.
    words = ["Lcom/revenuecat/purchases/Foo;", "https://api.example.com/v1/x", "hi",
             "/api/v1/" + "x" * 130]
    assert feature_probe.dex_strings(_tiny_dex(words)) == words

    # Contract: hostile input yields nothing and NEVER crashes — one corrupt file
    # inside an APK must not cost you the other twenty.
    for junk in (b"", b"PK\x03\x04short",
                 b"PK\x03\x04" + bytes(range(256)) * 4,
                 b"dex\n" + b"\xff" * 300):
        assert feature_probe.dex_strings(junk) == [], junk[:8]

    # An offset pointing into the header is not a string. Unchecked, offset 0
    # decoded the file magic and returned "ex\n" as if it were app data.
    bad = bytearray(_tiny_dex(["x"]))
    struct.pack_into("<I", bad, 0x70, 0)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = feature_probe.dex_strings(bytes(bad))
    assert got == [], got
    assert "unreadable and skipped" in buf.getvalue(), buf.getvalue()


def test_dex_rejects_a_forged_header_and_table_offsets():
    """feature_probe — both dex guards must be pinned, not just present.

    Each of these kills one guard: without them a crafted header returned the file
    magic as app data, and an offset into the string_ids table returned ''.
    """
    # header_size is fixed at 0x70 by the spec; trusting the file's own value let
    # header_size=1 + offset=1 pass the bounds check and decode the magic.
    forged = bytearray(_tiny_dex(["x"]))
    struct.pack_into("<I", forged, 0x24, 1)
    struct.pack_into("<I", forged, 0x70, 1)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert feature_probe.dex_strings(bytes(forged)) == []
    # and it must SAY it refused — silent empty reads as "nothing here"
    assert "refusing to parse" in buf.getvalue(), buf.getvalue()

    # An offset pointing into the string_ids table is table data, not a string.
    into_table = bytearray(_tiny_dex(["x"]))
    struct.pack_into("<I", into_table, 0x70, 0x70)
    assert _run_quiet(feature_probe.dex_strings, bytes(into_table)) == []


def test_no_endpoint_is_ever_invented_out_of_a_fragment():
    """feature_probe — six review rounds, six ways to fabricate `/api/v1/users`.

    Each of these once produced that path; none of them contains it. The scan that
    dug URLs out of long lines is gone, and truncated fragments are refused, so the
    family was killed at its root instead of one terminator at a time.
    ⚠️ That is not a proof of completeness — later rounds found further entries.
    """
    ninety = "x" * 90
    fabrications = [
        'var u="https://api.example.io/api/v1/users,archive";' + ninety,
        r'var u="https://api.example.io/api/v1/users,\u0061rchive";' + ninety,
        r'var u="https://api.example.io/api/v1/users\u0061rchive";' + ninety,
        'var u="https://api.example.io/api/v1/users\'archive";' + ninety,
        "https://" + "a" * 80 + ".example.io/api/v1/users,",
    ]
    for text in fabrications:
        out = _capture(feature_probe.report_api, [(text, "fx", True)])
        assert "/api/v1/users" not in out, text[:60]

    # A fragment — cut short upstream by a non-ASCII byte — must never become a path.
    cut = feature_probe.binary_strings(
        b"https://api.example.io/api/v1/users\xc3\xa9rchive\x00", 6)
    assert cut[0] == ("https://api.example.io/api/v1/users", False), cut
    assert "/api/v1/users" not in _capture(feature_probe.report_api,
                                           [(s, "fx", ok) for s, ok in cut])

    # A complete string that IS the path still comes through, query trimmed.
    for path in ("/api/v1/widgets/", "/v12/transcribe", "/api/v1/users/{id}",
                 "/api/v1/getUser"):
        assert path in _capture(feature_probe.report_api, [(path, "fx", True)])
    assert "/api/v1/users" in _capture(feature_probe.report_api,
                                       [("/api/v1/users?limit=10", "fx", True)])


def test_paths_come_only_from_whole_strings():
    """feature_probe — the embedded-URL scan is gone and must stay gone.

    It was the source of six different fabricated endpoints. A URL sitting inside a
    longer string is deliberately not an endpoint, however tempting it looks.
    """
    embedded = 'const cfg = {url: "https://api.example.io/api/v1/buried"}; // note'
    assert "/api/v1/buried" not in _capture(feature_probe.report_api,
                                            [(embedded, "fx", True)])
    # the same path, as its own complete string, is fine
    assert "/api/v1/buried" in _capture(feature_probe.report_api,
                                        [("/api/v1/buried", "fx", True)])


def test_fragment_detection_reads_one_whole_character():
    """feature_probe — every way a cut string slipped through as complete.

    Each of these once produced a fabricated `/api/v1/users`: a window ending
    mid-character, a stray byte inside the window, a non-ASCII space, and text
    cut off on the LEFT instead of the right.
    """
    cases = [
        b"https://api.example.io/api/v1/users\xc3\xa9rchive\x00",
        # The FIRST continuing character must be 3- and 4-byte too: with only the
        # 2-byte `é` sample, shrinking the decode back to a fixed 2 bytes stayed green.
        b"/api/v1/users\xe4\xb8\xadarchive\x00",            # 中
        b"/api/v1/users\xf0\x9f\x98\x80archive\x00",        # 😀
        # TAB/LF/CR are text and the printable-run regex cuts on them, so the control
        # character is gone before candidate_path could ever refuse it.
        b"/api/v1/users\tarchive\x00",
        b"/api/v1/users\narchive\x00",
        b"text\t/api/v1/users\x00",                        # cut on the left, too
        # The run regex cuts on EVERY control byte, so the character is gone before
        # candidate_path could refuse it. NUL is the one that still means "ended".
        b"/api/v1/users\x01archive\x00",
        b"/api/v1/users\x7farchive\x00",
        b"\x01https://api.example.io/api/v1/users\x00",
        b"text\x1f/api/v1/users\x00",
        b"https://api.example.io/api/v1/users\xc3\xa9\xe4\xb8\xadarchive\x00",
        b"/api/v1/users\xc3\xa9\x00\xff",
        b"/api/v1/users\xc2\xa0archive\x00",          # U+00A0 is still a character
        b"\xe4\xb8\xad\xe6\x96\x87/api/v1/users\x00",   # cut on the left
    ]
    for blob in cases:
        rows = [(s, "fx", ok) for s, ok in feature_probe.binary_strings(blob, 6)]
        assert "/api/v1/users" not in _capture(feature_probe.report_api, rows), blob

    # ⚠️ And the left check must require a REAL character, not just a byte in the
    # 0x80-0xBF range: a quarter of all byte values land there, so the naive version
    # turned 152 fragments into 57664 on one real library and binned most of its API
    # surface. Arbitrary binary on the left means the string started here.
    assert feature_probe.binary_strings(b"\x91\xb3/api/v1/kept\x00", 6)[0][1] is True
    assert feature_probe.binary_strings(b"\xd8\xb0/api/v1/cut\x00", 6)[0][1] is False

    # Bytes that are not a character are TREATED AS "the string ended" — a judgement
    # that keeps recall on real binaries, not a fact about the data.
    # A NUL on the LEFT is a terminator too: `prefix\x00/api/v1/kept` is a complete
    # record that must survive. Only the right-hand NUL was covered, so treating a
    # left NUL as a cut stayed green while silently dropping real paths.
    rows = [(s, "so", ok)
            for s, ok in feature_probe.binary_strings(b"prefix\x00/api/v1/kept\x00", 6)]
    assert "/api/v1/kept" in _capture(feature_probe.report_api, rows), rows

    # NUL is treated as a clean terminator; other C0 bytes and DEL are not. ⚠️ >=0x80 depends:
    # plain binary keeps it, a valid UTF-8 character means text carried on.
    assert feature_probe.binary_strings(b"ended_here\x00", 6)[0][1] is True
    assert feature_probe.binary_strings(b"ended_here\x91\xb3", 6)[0][1] is True
    assert feature_probe.binary_strings(b"cut_here\x01\x02", 6)[0][1] is False
    assert feature_probe.binary_strings(b"cut_here\x7f", 6)[0][1] is False
    assert feature_probe.binary_strings(b"ended_here\xff\xfe", 6)[0][1] is True


def test_a_complete_string_is_never_rewritten():
    """feature_probe — every "harmless" normalisation fabricated a path.

    Quote-stripping, whitespace-stripping and urlsplit's silent removal of
    TAB/CR/LF each turned a complete string into a `/api/v1/users` that the app
    never had. A string whose exact bytes cannot be vouched for is refused.
    """
    forgeries = [
        "https://api.example.io/api/v1/users'",       # apostrophe is part of it
        "\u00a0/api/v1/users",                         # U+00A0 is a real character
        "/api/v1/users\u00a0",
        "https://api.example.io/api/v1/us\ters",       # urlsplit deletes the TAB
        "/api/v1/us\ners",
        " https://api.example.io/api/v1/users",      # urlsplit strips the space
        "\x01https://api.example.io/api/v1/users",   # ...and C0 control characters
    ]
    for text in forgeries:
        out = _capture(feature_probe.report_api, [(text, "dex", True)])
        assert "/api/v1/users" not in out, text
    # untouched paths still come through
    for good in ("/api/v1/users.", "/api/v1/users/{id}", "/api/v1/getUser"):
        assert good in _capture(feature_probe.report_api, [(good, "dex", True)])


def test_a_trailing_dot_survives_and_fragments_are_counted():
    """feature_probe — `.` is a legal path character; and the skip count is real."""
    assert "/api/v1/users." in _capture(feature_probe.report_api,
                                        [("/api/v1/users.", "fx", True)])
    out = _capture(feature_probe.report_api,
                   [("/api/v1/a", "fx", False), ("/api/v1/b", "fx", False),
                    ("/api/v1/kept", "fx", True)])
    assert "2 strings rejected as possibly truncated" in out, out
    assert "heuristic" in out, out          # never present the count as certainty
    assert "/api/v1/kept" in out


def test_binary_strings_flags_fragments():
    """feature_probe — how the completeness HEURISTIC decides, and where it doesn't.

    Text continuing (a UTF-8 character, or a TAB/LF/CR the run regex cut on) means
    fragment. Arbitrary binary means the string simply ended. Neither is proof.
    """
    # Followed by binary, NUL, or nothing => TREATED AS "the string ended there".
    assert feature_probe.binary_strings(b"complete_run\x00", 6) == [("complete_run", True)]
    assert feature_probe.binary_strings(b"at_the_very_end", 6) == [("at_the_very_end", True)]
    # Followed by more TEXT (a UTF-8 character) => treated as a prefix of a longer string.
    assert feature_probe.binary_strings(b"cut_here\xc3\xa9more_text", 6)[0] == ("cut_here", False)
    # ⚠️ "complete only if NUL-terminated" threw away 111738 of one real library's
    # strings — compiled Dart stores strings against arbitrary binary.
    assert feature_probe.binary_strings(b"dart_string\x8f\x21\x00", 6)[0][1] is True


def test_dex_diagnostics_name_the_file_they_came_from():
    """feature_probe — "one file was refused" is useless without which file."""
    forged = bytearray(_tiny_dex(["x"]))
    struct.pack_into("<I", forged, 0x24, 1)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        feature_probe.dex_strings(bytes(forged), "app.apk:classes2.dex")
    assert "app.apk:classes2.dex" in buf.getvalue(), buf.getvalue()

    into_table = bytearray(_tiny_dex(["x"]))
    struct.pack_into("<I", into_table, 0x70, 0x70)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        feature_probe.dex_strings(bytes(into_table), "app.apk:classes3.dex")
    assert "app.apk:classes3.dex" in buf.getvalue(), buf.getvalue()

    # End-to-end: collect() must actually PASS the source down, and must mark dex
    # strings COMPLETE — they come from a length-prefixed table, so an endpoint read
    # off one is not a fragment. Flagging them incomplete silently drops every path.
    buf_zip = io.BytesIO()
    forged2 = bytearray(_tiny_dex(["x"]))
    struct.pack_into("<I", forged2, 0x24, 1)
    with zipfile.ZipFile(buf_zip, "w") as z:
        z.writestr("classes2.dex", bytes(forged2))
        z.writestr("classes.dex", _tiny_dex(["healthy_string_here", "/api/v1/from_dex"]))
    buf_zip.seek(0)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _, strings = feature_probe.collect([buf_zip])
    assert "classes2.dex" in buf.getvalue(), buf.getvalue()
    assert any("healthy_string_here" in s for s, _, _ in strings), strings
    assert "/api/v1/from_dex" in _capture(feature_probe.report_api, strings)

    # a healthy dex stays silent
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        feature_probe.dex_strings(_tiny_dex(["ok"]), "app.apk:classes.dex")
    assert buf.getvalue() == "", buf.getvalue()


def test_bundled_h5_is_read_including_offline_zips():
    """feature_probe — a Flutter shell's WebView feature lived in assets/*.js and a
    keyword search over it said "no matches" (pitfall 35)."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("dist/ask.js", 'fetch("https://ask.h5.test/x");\n/api/v1/ask\n')
    buf_zip = io.BytesIO()
    with zipfile.ZipFile(buf_zip, "w") as z:
        z.writestr("assets/flutter_assets/web/main.js", "const retriever_info = '检索结果';\n")
        z.writestr("assets/offline/h5.zip", inner.getvalue())
        z.writestr("assets/offline/sealed.zip", b"PK\x03\x04 not really a zip")
    buf_zip.seek(0)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _, strings = feature_probe.collect([buf_zip])
    # an archive we could not open is named, not silently treated as empty
    assert "assets/offline/sealed.zip" in buf.getvalue(), buf.getvalue()
    out = _capture(feature_probe.report_keyword, strings, ["retriever_info", "检索"])
    assert "web/main.js" in out and "no matches" not in out, out
    assert "检索结果" in out, out
    assert "ask.h5.test" in _capture(feature_probe.report_host, strings)
    # a line that is wholly a path counts; the nested source is fully named
    api = _capture(feature_probe.report_api, strings)
    assert "/api/v1/ask" in api and "h5.zip:dist/ask.js" in api, api


def test_report_native_states_bundled_not_running():
    """feature_probe — shipping a runtime is a [package] fact, not a [device] one."""
    out = _capture(feature_probe.report_native,
                   [("libonnxruntime.so", 4096, "x.apk:lib/arm64-v8a/libonnxruntime.so")])
    assert "bundled" in out
    assert "runs locally" not in out          # never promote package -> device
    assert "lib/arm64-v8a" in out             # source must stay traceable
    # empty must say WHY it may be empty, not imply "no native code"
    assert "split_config" in _capture(feature_probe.report_native, [])


def test_report_host_uses_a_real_url_parser():
    """feature_probe — `https://user@real.example.io/x` is not hosted at `user`."""
    out = _capture(feature_probe.report_host,
                   [("https://user@real.example.io/x", "fx", True),
                    ("see https://www.w3.org/xml for details", "fx", True)])
    assert "real.example.io" in out
    assert re.search(r"^\s+\d+\s+user\b", out, re.M) is None, out
    assert "w3.org" not in out                # xmlns boilerplate is not a dependency


def test_report_api_rejects_everything_that_is_not_an_endpoint():
    """feature_probe — class paths and asset paths must not be sold as APIs."""
    out = _capture(feature_probe.report_api, [
        ("Lcom/acme/api/Client;", "fx", True),      # dex type descriptor
        ("/assets/v1/icons/logo.png", "fx", True),  # asset path
        ("/api/" + "a" * 70, "fx", True),           # too long -> must not be truncated
        ("/api/v1/widgets/", "fx", True),        # real
        ("/v12/transcribe", "fx", True),            # multi-digit version, real
    ])
    assert "/api/v1/widgets/" in out and "/v12/transcribe" in out
    for bad in ("/api/Client", "/v1/icons", "aaaaaaaaaa"):
        assert bad not in out, bad


def test_one_malformed_url_does_not_end_the_run():
    """feature_probe — `https://[broken` raised ValueError out of urlsplit and took
    every later finding down with it. One bad string in a 300k-string binary is
    normal; losing the whole analysis to it is not."""
    rows = [("https://[broken", "x.dex", True), ("/v12/transcribe", "x.dex", True),
            ("https://ok.example.io/api/v1/live", "x.dex", True)]
    out = _capture(feature_probe.report_api, rows)
    assert "/v12/transcribe" in out and "/api/v1/live" in out, out
    assert feature_probe.candidate_path("https://[broken") is None
    _capture(feature_probe.report_host, rows)        # must not raise either


def test_report_sdk_does_not_claim_to_count_classes():
    """feature_probe — it counts string hits; claiming class counts is a fake measure."""
    out = _capture(feature_probe.report_sdk, [("Lcom/acme/product/NotAClass", "fx", True)] * 2)
    assert "class count" not in out.lower()
    assert "STRING HITS" in out or "string table" in out
    # and the actual finding, not just the disclaimer — printing only the header
    # was a mutation that survived the first version of this test
    assert "com.acme.product" in out
    assert re.search(r"^\s+2\s+com\.acme\.product", out, re.M), out
    assert "fx" in out                                     # source stays traceable


def test_react_native_bundle_is_actually_read():
    """feature_probe — claiming RN coverage while skipping the bundle was false.

    In-memory ZIP: a read-only checkout must still be able to run the suite.
    The bundle is one minified line on purpose — that is what broke the
    whole-string matchers the first time.
    """
    buf = io.BytesIO()
    line = ('const u="https://rn.example.io/api/v1/go";const f="transcription";'
            + "x" * 220)
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("assets/index.android.bundle", line)
    buf.seek(0)
    _, strings = feature_probe.collect([buf])
    assert any("rn.example.io" in s for s, _, _ in strings), strings[:3]
    # Hosts and keywords survive a minified bundle; paths deliberately do not —
    # see test_no_endpoint_is_ever_invented_out_of_a_fragment for why.
    assert "rn.example.io" in _capture(feature_probe.report_host, strings)
    assert "transcription" in _capture(feature_probe.report_keyword, strings, ["transcription"])


def test_missing_native_libraries_do_not_imply_missing_splits():
    out = _capture(feature_probe.report_native, [])
    assert "analysed packages" in out, out
    assert "base or ABI splits" in out, out
    assert "MEANS NOTHING" not in out, out


def test_research_report_preserves_evidence_boundaries():
    import copy
    import importlib
    import json
    import pathlib
    try:
        checker = importlib.import_module("check_report")
    except ImportError as exc:
        raise AssertionError("report checker is not implemented") from exc
    path = pathlib.Path(__file__).resolve().parent.parent / "examples/research/report.json"
    report = json.loads(path.read_text())
    assert checker.validate(report) == [], checker.validate(report)
    mutations = [
        (lambda r: r.update(schema_version=True), "schema_version"),
        (lambda r: r["evidence"].append(copy.deepcopy(r["evidence"][0])), "duplicate"),
        (lambda r: r["claims"][0].update(evidence_ids=["missing"]), "evidence_ids"),
        (lambda r: r["claims"][0].update(question_id="missing"), "question_id"),
        (lambda r: r["claims"][0].update(scope="runtime", status="PASS"), "runtime"),
        (lambda r: r["claims"][0].update(basis="inferred", status="PASS"), "inferred"),
        (lambda r: r["claims"][0].update(evidence_ids=[]), "evidence_ids"),
        (lambda r: r["evidence"][0].update(locator={}), "locator"),
        (lambda r: r["evidence"][0].update(observed_at="yesterday"), "observed_at"),
        (lambda r: r["context"].update(achieved_depth="L1", gaps=[]), "gaps"),
        (lambda r: r["questions"].append({"id": "q2", "text": "Unanswered?", "critical": True}), "q2"),
        (lambda r: r.update(evidence=[None]), "evidence[0]"),
    ]
    for mutate, diagnostic in mutations:
        changed = copy.deepcopy(report)
        mutate(changed)
        errors = checker.validate(changed)
        assert any(diagnostic in error for error in errors), (diagnostic, errors)
    assert checker.validate([]), "a report must be an object"
    assert checker.validate({}), "empty report must fail"


def test_research_report_cli_rejects_invalid_json():
    import pathlib
    import subprocess
    import tempfile
    script = pathlib.Path(__file__).resolve().parent / "check_report.py"
    fixture = script.parent.parent / "examples/research/report.json"
    good = subprocess.run([sys.executable, str(script), str(fixture)],
                          capture_output=True, text=True, timeout=10)
    assert good.returncode == 0 and "not" not in good.stderr, good.stderr
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "report.json"
        for payload, message in [(b'{"schema_version":1,"schema_version":1}', "duplicate"),
                                 (b'{"schema_version":NaN}', "non-JSON"),
                                 (b'\xff', "codec"),
                                 (b'[]', "expected an object")]:
            path.write_bytes(payload)
            result = subprocess.run([sys.executable, str(script), str(path)],
                                    capture_output=True, text=True, timeout=10)
            assert result.returncode == 1 and message in result.stderr, result.stderr
            assert "Traceback" not in result.stderr, result.stderr


def test_report_keyword_shows_the_hit_and_its_source():
    """feature_probe — an empty keyword report was a surviving mutation."""
    out = _capture(feature_probe.report_keyword,
                   [("Pemisahan Speaker (Beta)", "libapp.so", True),
                    ("Invalid XXCH speaker layout mask", "libavcodec.so", True)],
                   ["speaker"])
    assert "Pemisahan Speaker (Beta)" in out
    assert "libapp.so" in out and "libavcodec.so" in out   # source tells them apart
    assert "NOT a shipped feature" in out                  # the caveat is the point
    assert "no matches" in _capture(feature_probe.report_keyword, [], ["nothing"])


def test_whole_word_keyword_cli_preserves_stems_and_literal_terms():
    from unittest.mock import patch
    strings = [(s, "fixture.so", True) for s in
               ("drawable", "ble scan", "éble", "ble2", "ble_name", "a+b", "aaab",
                "x" * 210 + " ble")]
    def run(extra):
        with patch.object(sys, "argv", ["feature_probe.py", "fixture.apk", "--section",
                                        "keyword", "--keyword", "ble,a+b"] + extra), \
             patch.object(feature_probe, "collect", return_value=([], strings)):
            return _capture(feature_probe.main)
    default = run([])
    assert "drawable" in default and "[ble] 6 match(es)" in default, default
    try:
        exact = run(["--whole-word"])
    except SystemExit as exc:
        raise AssertionError("--whole-word is unavailable") from exc
    assert "[ble] 2 match(es)" in exact, exact
    assert "drawable" not in exact and "éble" not in exact, exact
    assert "[a+b] 1 match(es)" in exact and "fixture.so" in exact, exact
    assert "    a+b " in exact and "aaab" not in exact, exact


def test_binary_strings_needs_a_minimum_run():
    """feature_probe — short noise runs would bury the readable strings."""
    blob = b"\x00\x01ab\x00transcription\xff\xfeshort\x00"
    got = [s for s, _ in feature_probe.binary_strings(blob)]
    assert "transcription" in got
    assert "ab" not in got


def test_duplicate_frames_are_reported_not_repaired():
    """pitfall 30 — duplicated frames read as static holds and split transitions.

    The signature is a sample below the quiet threshold with motion on both
    sides. Measured on a 4.2s synthetic loop: 0.7% of samples on a natively
    recorded clip, 10.7% on the same content upsampled 25 -> 30, where the
    verdict flipped to NOT A LOOP.
    """
    q = 0.1
    # one dip surrounded by motion -> a duplicate
    diffs = [(i * 0.05, v) for i, v in enumerate([0.5, 0.6, 0.02, 0.6, 0.5])]
    assert frame_diff.duplicate_samples(diffs, q) == [0.1], diffs
    # a genuine hold is not a duplicate: its neighbours are quiet too
    hold = [(i * 0.05, v) for i, v in enumerate([0.5, 0.02, 0.02, 0.02, 0.5])]
    assert frame_diff.duplicate_samples(hold, q) == []
    # A dip that is shallow RELATIVE to its neighbours is a real slowdown, not a
    # duplicate. Measured duplicates sat at 12-28% of the local peak after lossy
    # encoding and downsampling, so the ratio is what separates them — an
    # absolute threshold would call every eased transition a duplicate.
    shallow = [(i * 0.05, v) for i, v in enumerate([0.3, 0.25, 0.09, 0.2, 0.3])]
    assert frame_diff.duplicate_samples(shallow, q) == [], shallow   # 0.09/0.25 = 36%
    # ends are never candidates — there is no "both neighbours" to check
    assert frame_diff.duplicate_samples([(0.0, 0.0), (0.05, 0.5)], q) == []


def test_declared_and_average_frame_rates_are_both_reported():
    """pitfall 30 — the two rates are reported, never reconciled.

    screenrecord labels a variable-rate file r_frame_rate=30/1 while averaging
    15.2. An earlier version tried to derive a "correct" sampling rate from
    this and produced cascading advice (25 fps -> 10 -> 8 -> 6 -> 5); that is
    gone. Both numbers are printed and the analyst judges.
    """
    vfr = {"r_frame_rate": "30/1", "avg_frame_rate": "3255/214"}
    declared, average = frame_diff.source_rates(vfr)
    assert declared == 30 and abs(average - 15.21) < 0.01
    cfr = {"r_frame_rate": "30/1", "avg_frame_rate": "30/1"}
    assert frame_diff.source_rates(cfr) == (30, 30)
    # ffprobe writes 0/0 when it cannot tell; degrade, never crash
    assert frame_diff.source_rates({"r_frame_rate": "0/0", "avg_frame_rate": "0/0"}) == (None, None)
    assert frame_diff.source_rates({}) == (None, None)
    # the removed heuristic must not come back
    assert not hasattr(frame_diff, "divisor_advice"), "divisor_advice was removed (pitfall 30)"


def test_timeline_matches_ffmpeg_not_raw_pts():
    """pitfall 36 — ffmpeg's -ss/select/showinfo count from the first frame; only
    ffprobe's raw pts do not. A "fix" to raw pts put frame_diff 1.00s off ffmpeg's
    scene timestamps. Skips without ffmpeg."""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("ffmpeg"):
        print("      (skipped: no ffmpeg)")
        return
    with tempfile.TemporaryDirectory() as root:
        clip = os.path.join(root, "off.mp4")
        # black 1s, fade to white over 0.5s, hold; shifted so the first frame is at
        # pts 1.0. (A one-frame cut is absorbed by min_run — it needs a real move.)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=white:s=64x64:r=10:d=2,fade=in:st=1:d=0.5",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-output_ts_offset", "1.0", clip], check=True)
        out = subprocess.run([sys.executable, os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "frame_diff.py"), clip, "--no-plot", "--fps", "10"],
            capture_output=True, text=True, check=True).stdout
    assert "raw stream start_time 1.000s" in out, out
    move = re.search(r"move\s+([\d.]+) ->", out)
    assert move and abs(float(move.group(1)) - 1.0) < 0.15, out


def test_walkthrough_steps_have_pictures_and_the_right_frame():
    """walkthrough — numbered steps, a picture each, and the frame that was ON
    screen at `at`: `-ss T -frames:v 1` returned the next change on a VFR clip
    (pitfall 36). Skips without ffmpeg."""
    import json
    import shutil
    import subprocess
    import tempfile
    import walkthrough
    # every step needs a picture; clips need a video
    errs = walkthrough.check({"steps": [{"action": "tap"}, {"action": "x", "image": "a.png", "clip": [1, 2]}]})
    assert any("step 1" in e and "picture" in e for e in errs), errs
    assert any("step 2" in e and "video" in e for e in errs), errs
    # competitor screens must not be written into the repo
    try:
        walkthrough.build(os.path.join(walkthrough.REPO, "nope.json"), os.path.join(walkthrough.REPO, "out"))
        raise AssertionError("wrote inside the repo")
    except SystemExit as e:
        assert "inside the repo" in str(e), e
    if not shutil.which("ffmpeg"):
        print("      (skipped: no ffmpeg)")
        return
    with tempfile.TemporaryDirectory() as root:
        # VFR: one black frame held 3s, then white — no frames in between
        for name, colour in (("a.png", "black"), ("b.png", "white")):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            f"color={colour}:s=64x64", "-frames:v", "1", os.path.join(root, name)], check=True)
        with open(os.path.join(root, "l.txt"), "w") as f:
            f.write("file a.png\nduration 3\nfile b.png\nduration 1\nfile b.png\n")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-i", os.path.join(root, "l.txt"),
                        "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        os.path.join(root, "rec.mp4")], check=True)
        with open(os.path.join(root, "steps.json"), "w") as f:
            json.dump({"title": "Ask <flow>", "video": "rec.mp4", "steps": [
                {"action": "Open <Ask>", "result": "panel", "at": 2.9, "clip": [2.5, 3.5], "evidence": "e3"},
                {"action": "Send", "image": "b.png"}]}, f)
        out = walkthrough.build(os.path.join(root, "steps.json"), os.path.join(root, "out"))
        page = open(os.path.join(out, "walkthrough.html"), encoding="utf-8").read()
        md = open(os.path.join(out, "walkthrough.md"), encoding="utf-8").read()
        assert page.count("<li>") == 2 and "&lt;Ask&gt;" in page and "<Ask>" not in page, page
        assert 'src="rec.mp4#t=2.5,3.5"' in page, page
        assert "1. **Open <Ask>**" in md and "2. **Send**" in md and "![步骤 1](step01.png)" in md, md
        pix = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", os.path.join(out, "step01.png"),
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        assert pix and max(pix) < 40, "step 1 at 2.9s must be the black frame still on screen"


def test_plugin_manifest_version_skills_and_marketplaces_agree():
    """pitfall 39 — plugin installs update only when `version` changes, and Codex
    exposes the root SKILL.md only with skills="./." (not "./", ".", or nothing)."""
    import json
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plugin = json.load(open(os.path.join(root, ".claude-plugin", "plugin.json")))
    assert plugin["skills"] == "./.", plugin.get("skills")
    top = re.search(r"^## (\S+) ·", open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8").read(), re.M)
    assert top and top.group(1) == plugin["version"], (top and top.group(1), plugin["version"])
    claude = json.load(open(os.path.join(root, ".claude-plugin", "marketplace.json")))
    codex = json.load(open(os.path.join(root, ".agents", "plugins", "marketplace.json")))
    assert claude["name"] == codex["name"], "README tells both clients the same @marketplace name"
    for m in (claude, codex):
        assert [e["name"] for e in m["plugins"]] == [plugin["name"]], m


def test_nb_frames_is_not_frame_count():
    """pitfall 13 — the header must not echo the container's nb_frames."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "frame_diff.py")).read()
    header = src.split("frames = sample(")[0]
    assert "nb_frames')} frames" not in header
    assert re.search(r"len\(frames\)\} frames sampled", src)


def test_capture_report_references():
    import check_report
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        rows = {
            "manifest.json": {"session_id": "s1", "complete": True},
            "network.jsonl": {"session_id": "s1", "request_id": "r1", "body_status": "captured", "candidate_action_ids": ["a1"]},
            "actions.jsonl": {"session_id": "s1", "action_id": "a1"},
        }
        for name, row in rows.items():
            with open(os.path.join(root, name), "w") as f:
                json.dump(row, f)
        locator = {"session_id": "s1", "request_id": "r1", "action_id": "a1", "body_status": "captured"}
        report = {"evidence": [{"id": "e1", "kind": "network", "locator": locator}]}
        assert check_report.validate_capture(report, root) == []
        with open(os.path.join(root, "network.jsonl"), "w") as f:
            json.dump({"session_id":"s1", "request_id":"r1", "body_status":"captured", "candidate_action_ids":"prefix-a1-suffix"}, f)
        assert check_report.validate_capture(report, root), "string must not match as substring"
        with open(os.path.join(root, "network.jsonl"), "w") as f:
            json.dump(rows["network.jsonl"], f)
        for key, bad in (("session_id", "s2"), ("request_id", "missing"), ("action_id", "missing"), ("body_status", "empty")):
            original = locator[key]
            locator[key] = bad
            assert check_report.validate_capture(report, root), key
            locator[key] = original


def test_apk_profile_manifest_and_incomplete_set():
    import apk_profile
    import tempfile
    import zipfile
    xml = '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="fixture.demo" android:versionCode="7"><uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35"/><uses-permission android:name="android.permission.INTERNET"/><application><activity android:name=".Main"><intent-filter><action android:name="android.intent.action.MAIN"/></intent-filter></activity></application></manifest>'
    manifest = apk_profile.parse_manifest(xml)
    assert manifest["package"] == "fixture.demo"
    assert manifest["components"][0]["exported"] == "unresolved"
    assert manifest["permissions"] == ["android.permission.INTERNET"]
    assert manifest["components"][0]["intent_filters"][0]["action"] == ["android.intent.action.MAIN"]
    with tempfile.TemporaryDirectory() as root:
        package = os.path.join(root, "base.apk")
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("classes.dex", b"synthetic")
            archive.writestr("lib/arm64-v8a/libflutter.so", b"synthetic")
            archive.writestr("lib/arm64-v8a/libapp.so", b"synthetic")
            archive.writestr("assets/flutter_assets/AssetManifest.bin", b"synthetic")
        result = apk_profile.profile([package], expected=["base.apk", "split_config.apk"], tool=None)
        assert result["coverage"]["missing"] == ["split_config.apk"]
        ambiguous=apk_profile.profile([package], expected=["base.apk", "base.apk"], tool=None)
        assert ambiguous["coverage"]["ambiguous_basenames"]
        assert result["packages"][0]["tool"]["status"] == "unavailable"
        assert result["packages"][0]["stack_clues"][0]["name"] == "Flutter"
        assert len(result["packages"][0]["sha256"]) == 64
        missing_tool = apk_profile.profile([package], tool="/missing-fixture-apkanalyzer")
        assert missing_tool["packages"][0]["status"] == "inventoried"
        assert missing_tool["packages"][0]["tool"]["status"] == "partial"
        damaged = os.path.join(root, "bad.apk")
        with open(damaged, "wb") as f:
            f.write(b"not a ZIP")
        assert apk_profile.profile([damaged], tool=None)["packages"][0]["status"] == "invalid"
    conflicting = [{"manifest": {"package": "a", "version_code": "1"}}, {"manifest": {"package": "a", "version_code": "2"}}]
    assert apk_profile.identity_status(conflicting) == "conflict"
    assert apk_profile.identity_status([{"manifest": None}]) == "unknown"


def test_apk_optional_tool_failure_timeout_and_output_limits():
    import apk_profile
    result = apk_profile.run_tool([sys.executable, "-c", "raise SystemExit(3)"])
    assert result["status"] == "failed" and result["returncode"] == 3
    result = apk_profile.run_tool([sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.05)
    assert result["status"] == "timeout"
    result = apk_profile.run_tool([sys.executable, "-c", "print('x'*2048)"], limit=100)
    assert result["status"] == "truncated" and len(result["stdout"]) <= 100
    assert apk_profile.run_tool(["/nonexistent-fixture-tool"])["status"] == "unavailable"
    try:
        apk_profile.parse_manifest('<!DOCTYPE manifest [<!ENTITY x "value">]><manifest/>')
        assert False, "DTD accepted"
    except ValueError:
        pass


def test_apk_empty_resource_package_does_not_trigger_invalid_queries():
    import apk_profile
    import tempfile
    import zipfile
    original = apk_profile.run_tool
    def stub(command):
        kind = command[1]
        return {"command": command, "status": "ok", "stdout": '<manifest package="demo"/>' if kind == "manifest" else "", "stderr": ""}
    with tempfile.TemporaryDirectory() as root:
        package = os.path.join(root, "empty.apk")
        with zipfile.ZipFile(package, "w") as z:
            z.writestr("AndroidManifest.xml", b"synthetic stub")
        try:
            apk_profile.run_tool = stub
            row = apk_profile.inventory(package, "/fixture-tool")
        finally:
            apk_profile.run_tool = original
        assert [r["kind"] for r in row["tool"]["commands"]] == ["manifest", "dex", "resources"]
        assert row["tool"]["status"] == "ok"


def test_capture_stream_evidence_links():
    import check_report
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        rows = {"manifest.json": {"session_id": "s1", "complete": True},
                "network.jsonl": {"session_id": "s1", "request_id": "http-1", "body_status": "unavailable"},
                "actions.jsonl": {"session_id": "s1", "action_id": "a1"},
                "streams.jsonl": {"session_id": "s1", "event_id": "event-2", "request_id": "page:cdp:1:0", "kind": "cdp-body", "body_status": "captured"}}
        for name, row in rows.items():
            with open(os.path.join(root, name), "w") as f:
                json.dump(row, f)
        locator = {"session_id": "s1", "request_id": "page:cdp:1:0", "event_id": "event-2", "body_status": "captured"}
        report = {"evidence": [{"id": "e1", "kind": "network", "locator": locator}]}
        assert check_report.validate_capture(report, root) == []
        for field, bad in (("event_id", "missing"), ("request_id", "different"), ("body_status", "empty"), ("session_id", "s2")):
            old = locator[field]
            locator[field] = bad
            assert check_report.validate_capture(report, root)
            locator[field] = old


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:
                # Not just AssertionError: a crash must read as FAIL too. A mutation
                # that raised struct.error once printed nothing a grep would catch,
                # which looks exactly like "the mutation did not apply".
                failed += 1
                print(f"FAIL  {name}  {type(e).__name__}: {e}")
    print(f"\n{'all regression samples pass' if not failed else str(failed) + ' FAILED'}")
    sys.exit(1 if failed else 0)
