#!/usr/bin/env python3
"""Check research evidence links and explicit claim boundaries, not factual truth.

Usage: python3 scripts/check_report.py report.json
No files are modified. Exit 0 means the record is structurally valid, not that
its claims have been independently verified.
"""
import argparse
import datetime
import json
import os
import sys

SOURCES = {"device", "browser", "package", "public-source", "user-report"}
KINDS = {"observation", "network", "code", "document"}
DEPTHS = ("L1", "L2", "L3", "L4", "L5")
MAX_BYTES = 8 * 1024 * 1024


def validate(report):
    errors = []

    def fail(path, message):
        errors.append("{}: {}".format(path, message))

    def text(value, path):
        if not isinstance(value, str) or not value.strip():
            fail(path, "expected non-empty text")
            return False
        return True

    def choice(value, options, path):
        if not isinstance(value, str) or value not in options:
            fail(path, "expected one of " + ", ".join(sorted(options)))
            return False
        return True

    def timestamp(value, path):
        if not text(value, path):
            return
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError:
            fail(path, "expected ISO 8601 timestamp with timezone")

    def texts(value, path):
        if not isinstance(value, list):
            fail(path, "expected a list")
            return []
        for index, item in enumerate(value):
            text(item, "{}[{}]".format(path, index))
        return value

    def rows(name):
        value = report.get(name)
        if not isinstance(value, list):
            fail(name, "expected a list")
            return []
        result = []
        for index, row in enumerate(value):
            path = "{}[{}]".format(name, index)
            if not isinstance(row, dict):
                fail(path, "expected an object")
            else:
                result.append((path, row))
        return result

    def index_rows(items):
        result = {}
        for path, row in items:
            ident = row.get("id")
            if text(ident, path + ".id"):
                if ident in result:
                    fail(path + ".id", "duplicate ID " + ident)
                result[ident] = row
        return result

    if not isinstance(report, dict):
        return ["report: expected an object"]
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        fail("schema_version", "expected integer 1")
    context = report.get("context")
    if not isinstance(context, dict):
        fail("context", "expected an object")
    else:
        for key in ("product", "platform", "version", "goal"):
            text(context.get(key), "context." + key)
        timestamp(context.get("observed_at"), "context.observed_at")
        target = context.get("target_depth")
        achieved = context.get("achieved_depth")
        target_ok = choice(target, DEPTHS, "context.target_depth")
        achieved_ok = choice(achieved, DEPTHS, "context.achieved_depth")
        gaps = texts(context.get("gaps"), "context.gaps")
        if target_ok and achieved_ok and DEPTHS.index(achieved) < DEPTHS.index(target) and not gaps:
            fail("context.gaps", "explain why the target depth was not reached")

    questions = rows("questions")
    evidence = rows("evidence")
    claims = rows("claims")
    coverage = rows("coverage")
    question_index = index_rows(questions)
    evidence_index = index_rows(evidence)
    index_rows(claims)
    if not questions:
        fail("questions", "at least one question is required")
    if not coverage:
        fail("coverage", "record observations or explain uncovered scope")
    for path, row in questions:
        text(row.get("text"), path + ".text")
        if type(row.get("critical")) is not bool:
            fail(path + ".critical", "expected boolean")
    for path, row in evidence:
        choice(row.get("source"), SOURCES, path + ".source")
        choice(row.get("kind"), KINDS, path + ".kind")
        text(row.get("observation"), path + ".observation")
        timestamp(row.get("observed_at"), path + ".observed_at")
        texts(row.get("conditions"), path + ".conditions")
        texts(row.get("limitations"), path + ".limitations")
        locator = row.get("locator")
        if not isinstance(locator, dict):
            fail(path + ".locator", "expected an object")
            continue
        for field in ("target", "position"):
            text(locator.get(field), path + ".locator." + field)
        if row.get("source") in ("public-source", "user-report"):
            text(locator.get("published_at"), path + ".locator.published_at")
        if row.get("source") == "user-report":
            text(locator.get("author"), path + ".locator.author")
        if row.get("kind") == "network":
            if row.get("source") != "browser":
                fail(path + ".source", "network records require browser source")
            for field in ("session_id", "request_id"):
                text(locator.get(field), path + ".locator." + field)
            choice(locator.get("body_status"),
                   {"captured", "empty", "not_requested", "unavailable", "truncated"},
                   path + ".locator.body_status")

    answered = set()
    for path, row in claims:
        question = row.get("question_id")
        if not isinstance(question, str) or question not in question_index:
            fail(path + ".question_id", "unknown question ID")
        else:
            answered.add(question)
        for field in ("text", "rationale"):
            text(row.get(field), path + "." + field)
        texts(row.get("alternatives"), path + ".alternatives")
        choice(row.get("scope"), {"runtime", "static", "statement", "design"}, path + ".scope")
        choice(row.get("basis"), {"direct", "inferred", "proposal"}, path + ".basis")
        choice(row.get("status"), {"PASS", "PLAUSIBLE", "SKIP"}, path + ".status")
        refs = texts(row.get("evidence_ids"), path + ".evidence_ids")
        selected = []
        for ref in refs:
            if not isinstance(ref, str) or ref not in evidence_index:
                fail(path + ".evidence_ids", "unknown evidence ID " + repr(ref))
            else:
                selected.append(evidence_index[ref])
        if row.get("status") != "SKIP" and not refs:
            fail(path + ".evidence_ids", "non-SKIP claims require evidence")
        if row.get("basis") in ("inferred", "proposal") and row.get("status") == "PASS":
            fail(path + ".status", "inferred conclusions and proposals cannot be PASS")
        if row.get("scope") == "design" and row.get("basis") != "proposal":
            fail(path + ".basis", "design recommendations require proposal basis")
        if row.get("status") == "PASS":
            scope = row.get("scope")
            if scope == "runtime" and not any(
                    e.get("source") in ("device", "browser") and
                    e.get("kind") in ("observation", "network") for e in selected):
                fail(path + ".scope", "runtime PASS requires device/browser observation")
            if scope == "static" and not any(
                    e.get("source") in ("package", "browser") for e in selected):
                fail(path + ".scope", "static PASS requires package/browser evidence")
            if scope == "statement" and not any(
                    e.get("source") in ("public-source", "user-report") for e in selected):
                fail(path + ".scope", "statement PASS verifies a sourced statement only")
    for path, row in questions:
        if row.get("critical") is True and isinstance(row.get("id"), str) and row["id"] not in answered:
            fail(path, "critical question " + row["id"] + " needs a claim, including SKIP when uncovered")
    for path, row in coverage:
        text(row.get("target"), path + ".target")
        choice(row.get("status"), {"observed", "partial", "not_observed"}, path + ".status")
        text(row.get("reason"), path + ".reason")
    return errors


def validate_capture(report, directory):
    """Cross-check HTTP and stream references; timestamps do not prove causation."""
    def load(name, lines=False):
        with open(os.path.join(directory, name), "rb") as handle:
            raw = handle.read(32 * MAX_BYTES + 1)
        if len(raw) > 32 * MAX_BYTES:
            raise ValueError(name + " exceeds 256 MiB")
        def decode(data):
            return json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
        return [decode(line) for line in raw.splitlines() if line.strip()] if lines else decode(raw)

    manifest = load("manifest.json")
    requests = load("network.jsonl", True)
    actions = load("actions.jsonl", True)
    needs_streams = any(isinstance(e, dict) and isinstance(e.get("locator"), dict) and "event_id" in e["locator"] for e in report.get("evidence", []))
    streams = load("streams.jsonl", True) if needs_streams else []
    errors = []
    session = manifest.get("session_id")
    if not isinstance(session, str) or not session:
        raise ValueError("manifest has no session_id")
    if manifest.get("complete") is not True:
        errors.append("capture: session did not finish")
    indexes = []
    for rows, key in ((requests, "request_id"), (actions, "action_id"), (streams, "event_id")):
        index = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get(key), str):
                raise ValueError("capture has malformed " + key)
            if row.get("session_id") != session:
                errors.append("capture: cross-session " + key)
            candidates = row.get("candidate_action_ids", [])
            if not isinstance(candidates, list) or any(not isinstance(v, str) or not v for v in candidates):
                errors.append("capture: candidate_action_ids must be a list of non-empty IDs")
                continue
            if row[key] in index:
                errors.append("capture: duplicate " + key)
            index[row[key]] = row
        indexes.append(index)
    request_index, action_index, stream_index = indexes
    for evidence in report.get("evidence", []):
        if not isinstance(evidence, dict) or evidence.get("kind") != "network":
            continue
        locator = evidence.get("locator", {})
        if not isinstance(locator, dict):
            continue
        label = "evidence " + str(evidence.get("id"))
        if locator.get("session_id") != session:
            errors.append(label + ": cross-session reference")
        request_id = locator.get("request_id")
        event_id = locator.get("event_id")
        if event_id is not None and (not isinstance(event_id, str) or not event_id):
            errors.append(label + ": event_id must be non-empty text")
            continue
        row = stream_index.get(event_id) if isinstance(event_id, str) else request_index.get(request_id) if isinstance(request_id, str) else None
        if row is None:
            errors.append(label + ": request/event not found")
            continue
        if event_id is not None and row.get("request_id") != request_id:
            errors.append(label + ": mismatched stream request_id")
        for key in ("body_status", "page_id", "frame_id", "redirect_hop"):
            if key in locator and locator[key] != row.get(key):
                errors.append(label + ": mismatched " + key)
        action_id = locator.get("action_id")
        if action_id is not None:
            if not isinstance(action_id, str) or action_id not in action_index:
                errors.append(label + ": action not found")
            elif action_id not in row.get("candidate_action_ids", []):
                errors.append(label + ": action is not a recorded time-window candidate")
    return errors


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("non-JSON number: " + value)


def validate_walkthrough(report, path):
    """Each walkthrough step must point at evidence that really is that observation.

    A screenshot in the report next to "[device]" is only as good as the record
    behind it: the id must exist, be a device/browser observation, and — for a step
    cut from a recording — cite that same recording, not some other session.
    """
    errors = []
    with open(path, encoding="utf-8") as handle:
        spec = json.load(handle, object_pairs_hook=unique_object, parse_constant=reject_constant)
    evidence = {e.get("id"): e for e in report.get("evidence", []) if isinstance(e, dict)}
    steps = spec.get("steps") if isinstance(spec, dict) else None
    if not isinstance(steps, list) or not steps:
        return ["walkthrough.steps: expected a non-empty list"]
    for number, step in enumerate(steps, 1):
        where = "walkthrough.steps[{}]".format(number)
        if not isinstance(step, dict):
            errors.append(where + ": expected an object")
            continue
        ident = step.get("evidence")
        if not isinstance(ident, str) or not ident.strip():
            errors.append(where + ".evidence: every step needs the id of the evidence it shows")
            continue
        row = evidence.get(ident)
        if row is None:
            errors.append(where + ".evidence: no evidence with id " + ident)
            continue
        if row.get("source") not in ("device", "browser") or row.get("kind") != "observation":
            errors.append(where + ".evidence: " + ident + " is not a device/browser observation")
        video = step.get("video") or spec.get("video")
        if video and ("at" in step or step.get("clip")):
            target = str((row.get("locator") or {}).get("target", ""))
            if os.path.basename(target) != os.path.basename(video):
                errors.append(where + ".evidence: " + ident + " cites " + repr(target)
                              + ", but the step is cut from " + repr(video))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("--capture", help="Cross-check HTTP/CDP/stream links against a capture directory")
    parser.add_argument("--walkthrough", help="Cross-check a walkthrough.py steps.json against this report's evidence")
    args = parser.parse_args()
    try:
        with open(args.report, "rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("report exceeds 8 MiB")
        report = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                            parse_constant=reject_constant)
        errors = validate(report)
        if args.capture and not errors:
            errors.extend(validate_capture(report, args.capture))
        if args.walkthrough and not errors:
            errors.extend(validate_walkthrough(report, args.walkthrough))
    except (OSError, ValueError, RecursionError, AttributeError, TypeError) as exc:
        errors = ["report: " + str(exc)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("VALID structure and references; factual truth and achieved depth need human review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
