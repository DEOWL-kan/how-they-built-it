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


def test_percentile_extremes_ignore_text():
    """pitfall 10 — one very dark cell (text) must not become 'the background'.

    170 background cells plus 10 near-black text cells: the shape of a 9x20
    grid taken off a real screenshot with a headline on it.
    """
    cells = ["#E3E7EE"] * 170 + ["#16181A"] * 10
    darkest, lightest, abs_d, abs_l = image_probe.background_extremes(cells)
    assert darkest == "#E3E7EE", darkest      # background, not the headline
    assert abs_d == "#16181A", abs_d          # still reported, just not used
    # the whole point: the verdict flips depending on which pair you use
    assert image_probe.contrast("#111111", abs_d) < 1.5
    assert image_probe.contrast("#111111", darkest) > 4.5


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


def test_binary_strings_needs_a_minimum_run():
    """feature_probe — short noise runs would bury the readable strings."""
    blob = b"\x00\x01ab\x00transcription\xff\xfeshort\x00"
    got = [s for s, _ in feature_probe.binary_strings(blob)]
    assert "transcription" in got
    assert "ab" not in got


def test_sampling_rate_that_does_not_divide_the_source_is_called_out():
    """pitfall 30 — an uneven sampling ratio fabricated a NOT A LOOP verdict.

    Measured on a synthetic 4.2s loop (hold 1.5s + eased crossfade 2.7s): a
    30 fps source sampled at the default 20 reported 5 static holds, 4 moves and
    "NOT A LOOP, 80% spread". Sampled at 15 the same clip gave cycle 4.16s. The
    flat gradient is the necessary half — with film grain added, 20 fps was fine
    — but flat gradients are exactly what this tool gets pointed at.
    """
    # 30/20 = 1.5: uneven, and 15 is the largest whole rate that divides 30.
    assert frame_diff.divisor_advice(30, 20) == 15
    # Integer ratios must stay silent, or the warning becomes wallpaper.
    assert frame_diff.divisor_advice(60, 20) is None
    assert frame_diff.divisor_advice(30, 15) is None
    assert frame_diff.divisor_advice(30, 10) is None
    # No rate to compare against -> no advice, never a crash.
    assert frame_diff.divisor_advice(None, 20) is None
    assert frame_diff.divisor_advice(0, 20) is None
    # Never hand back false precision: a VFR average of 15.21 must not come back
    # as "--fps 15.2103", which is both unusable and a rate that does not exist.
    got = frame_diff.divisor_advice(15.21, 20)
    assert got == 15 and float(got).is_integer(), got


def test_a_variable_rate_recording_is_not_trusted_for_its_declared_fps():
    """pitfall 30 — screenrecord labels a VFR file with a cadence it never had.

    Measured on a real device: 2s of a static screen produced a single frame,
    and a clip averaging 15.2 fps still declared r_frame_rate=30/1. Same header
    dishonesty as nb_frames (pitfall 13).
    """
    vfr = {"r_frame_rate": "30/1", "avg_frame_rate": "3255/214"}   # 30 vs 15.2
    assert frame_diff.is_vfr(vfr)
    assert abs(frame_diff.source_rate(vfr) - 15.21) < 0.01   # the average wins
    cfr = {"r_frame_rate": "30/1", "avg_frame_rate": "30/1"}
    assert not frame_diff.is_vfr(cfr)
    assert frame_diff.source_rate(cfr) == 30
    # ffprobe writes 0/0 when it cannot tell; that must degrade, not crash.
    unknown = {"r_frame_rate": "0/0", "avg_frame_rate": "0/0"}
    assert not frame_diff.is_vfr(unknown)
    assert frame_diff.source_rate(unknown) is None
    assert frame_diff.source_rate({}) is None


def test_nb_frames_is_not_frame_count():
    """pitfall 13 — the header must not echo the container's nb_frames."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "frame_diff.py")).read()
    header = src.split("frames = sample(")[0]
    assert "nb_frames')} frames" not in header
    assert re.search(r"len\(frames\)\} frames sampled", src)


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
