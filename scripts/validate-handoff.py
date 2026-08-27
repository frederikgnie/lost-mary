#!/usr/bin/env python3
"""Validate a Claude Code agent handoff JSON (schema 1.0).

Usage:
  validate-handoff.py <file.json>
  validate-handoff.py -          # read stdin
  pbpaste | validate-handoff.py -   # macOS pasteboard
  xclip -o | validate-handoff.py -  # Linux clipboard

Exit 0 = ok, 1 = invalid, 2 = usage/IO error.
No deps beyond Python 3 stdlib. Intentionally minimal.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

SCHEMA_VERSION = "1.0"
REQUIRED = (
    "schema_version",
    "handoff_id",
    "from_role",
    "to",
    "task_id",
    "status",
    "confidence",
    "summary",
    "timestamp",
    "payload",
)
ROLES = {
    "architect",
    "researcher",
    "implementer",
    "debugger",
    "tester",
    "reviewer",
    "security-reviewer",
    "lead",
}
STATUSES = {"done", "blocked", "needs_review", "failed", "plan_ready"}

# Required payload keys per role — aligned with handoff.schema.json / handoff.md
PAYLOAD_REQUIRED: dict[str, tuple[str, ...]] = {
    "implementer": (
        "result",
        "owned_scope",
        "files_changed",
        "files_off_limits_touched",
        "validation",
        "tests_added_or_updated",
        "risks",
        "integration_notes",
        "rejected_approaches",
    ),
    "debugger": (
        "result",
        "failure",
        "reproduction",
        "root_cause",
        "fix",
        "owned_scope",
        "files_changed",
        "files_off_limits_touched",
        "validation",
        "tests_added_or_updated",
        "risks",
        "integration_notes",
        "rejected_approaches",
    ),
    "tester": (
        "coverage_assessment",
        "tests_added",
        "validation",
        "findings",
        "recommendation",
    ),
    "reviewer": (
        "posture",
        "findings",
        "positive_controls",
        "residual_risk",
    ),
    "security-reviewer": (
        "posture",
        "findings",
        "positive_controls",
        "residual_risk",
    ),
    "architect": (
        "understanding",
        "change_surface",
        "dependencies",
        "parallelization",
        "risks",
        "recommendation",
        "task_breakdown",
    ),
    "researcher": (
        "question",
        "findings",
        "options",
        "recommendation",
        "caveats",
    ),
}


def load(path: str) -> Any:
    if path == "-":
        raw = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    raw = raw.strip()
    return json.loads(raw)


def check(data: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return ["handoff must be a JSON object"], []

    for key in REQUIRED:
        if key not in data:
            errors.append(f"missing required field: {key}")

    if "schema_version" in data and data["schema_version"] != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be '{SCHEMA_VERSION}', got {data.get('schema_version')!r}"
        )

    if "from_role" in data and data["from_role"] not in ROLES:
        errors.append(f"invalid from_role: {data.get('from_role')!r}")

    if "status" in data and data["status"] not in STATUSES:
        errors.append(f"invalid status: {data.get('status')!r}")

    if "confidence" in data:
        c = data["confidence"]
        if not isinstance(c, (int, float)) or not (0.0 <= float(c) <= 1.0):
            errors.append("confidence must be a number in [0, 1]")

    if "summary" in data:
        s = data["summary"]
        if not isinstance(s, str) or len(s.strip()) < 10:
            errors.append("summary must be a string of at least 10 characters")
        elif not any(ch.isdigit() for ch in s) and ":" not in s:
            errors.append(
                "summary must include a proof token (test count, SHA, file:line, or command evidence)"
            )

    if "timestamp" in data and isinstance(data["timestamp"], str):
        ts = data["timestamp"].replace("Z", "+00:00")
        try:
            datetime.fromisoformat(ts)
        except ValueError:
            errors.append(f"timestamp is not ISO-8601: {data['timestamp']!r}")

    if "handoff_id" in data:
        hid = data["handoff_id"]
        if not isinstance(hid, str) or len(hid) < 8:
            errors.append("handoff_id must be a string of length >= 8")

    if "task_id" in data and (
        not isinstance(data["task_id"], str) or not data["task_id"].strip()
    ):
        errors.append("task_id must be a non-empty string")

    # Optional envelope fields
    if "tokens_used" in data:
        tu = data["tokens_used"]
        if not isinstance(tu, (int, float)) or float(tu) < 0:
            errors.append("tokens_used must be a non-negative number")

    if "attempts" in data:
        at = data["attempts"]
        if not isinstance(at, int) or isinstance(at, bool) or at < 1:
            errors.append("attempts must be an integer >= 1")

    if "stop_reason" in data and not isinstance(data["stop_reason"], str):
        errors.append("stop_reason must be a string")

    if "payload" in data and not isinstance(data["payload"], dict):
        errors.append("payload must be an object")
    elif isinstance(data.get("payload"), dict):
        role = data.get("from_role")
        required = PAYLOAD_REQUIRED.get(role or "", ())
        missing = [k for k in required if k not in data["payload"]]
        if missing:
            errors.append(
                f"payload missing required fields for {role}: {', '.join(missing)}"
            )
        # Scope discipline signal
        off = data["payload"].get("files_off_limits_touched")
        if isinstance(off, list) and off:
            warnings.append(
                f"files_off_limits_touched is non-empty ({len(off)} path(s)) — scope violation?"
            )

    return errors, warnings


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = argv[1]
    try:
        data = load(path)
    except FileNotFoundError:
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    errors, warnings = check(data)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        print("INVALID", file=sys.stderr)
        return 1

    role = data.get("from_role", "?")
    status = data.get("status", "?")
    task = data.get("task_id", "?")
    extra = []
    if "attempts" in data:
        extra.append(f"attempts={data['attempts']}")
    if "stop_reason" in data:
        extra.append(f"stop_reason={data['stop_reason']}")
    suffix = ("  " + "  ".join(extra)) if extra else ""
    print(f"OK  role={role}  status={status}  task_id={task}{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
