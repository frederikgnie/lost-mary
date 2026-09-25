#!/usr/bin/env python3
"""replay-merges: which historical merges would guard-shared-checkouts hold, and what really happened to them?

Reads Claude Code transcripts (read-only), finds every Bash / PowerShell call the guard counts as a merge, and
runs the guard's check_merge against the transcript cut at that call - the view the hook would have had then.
Each call gets its real outcome (refused by the auto-mode classifier, blocked by a hook, failed, ok) and the
guard's verdict (allow, chained, api, unreviewed; `checkout` when the merge check allows it but the configured
shared-checkout rules would block it). A good guard holds every refusal and few of the merges that went through.

Not installed: a development tool for this repository, like usage.py. It reads nothing but transcripts, the
guard script beside it and the guard's shared-checkouts config.

  python scripts/replay-merges.py [--projects DIR] [--since 2026-09-24T11:40] [--detail]

`--projects` defaults to $LOST_MARY_PROJECTS_DIR, else ~/.claude/projects. `--since` also prints the table for
the calls from that time on (default: when the user's autoMode.allow merge rule went live); `--detail` lists,
from then on, the merges that went through but would be held, and the refusals the guard would allow.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

GUARD = Path(__file__).resolve().parent / "guard-shared-checkouts.py"
RULE_LIVE = "2026-09-24T11:40"  # the user's autoMode.allow merge rule, first observed passing a merge
REFUSED = "denied by the Claude Code auto mode classifier"


class Row(NamedTuple):
    project: str
    session: str
    when: str
    outcome: str
    verdict: str
    command: str


def load_guard() -> ModuleType:
    """The guard, imported by path - registered in sys.modules first, as AGENTS.md requires."""
    spec = importlib.util.spec_from_file_location("guard_shared_checkouts", GUARD)
    if spec is None or spec.loader is None:
        raise SystemExit(f"replay-merges: cannot load {GUARD}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["guard_shared_checkouts"] = module
    spec.loader.exec_module(module)
    return module


def outcome_of(result: tuple[bool, str] | None) -> str:
    if result is None:
        return "failed"  # no result recorded: the session was cut off, or the call never ran
    error, body = result
    if REFUSED in body[:120]:
        return "refused"
    if "hook error" in body[:200] or "guard-shared-checkouts:" in body[:300]:
        return "hook-blocked"
    return "failed" if error else "ok"


def body_of(block: dict[str, Any]) -> str:
    raw = block.get("content")
    if isinstance(raw, list):
        return "\n".join(str(b.get("text", "")) for b in raw if isinstance(b, dict))
    return str(raw or "")


def replay(guard: ModuleType, path: Path, scratch: Path, config: Any, slowest: list[float]) -> list[Row]:
    """Every merge call in one transcript, with its outcome and the guard's verdict."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError as exc:
        print(f"replay-merges: skipped {path} ({exc})", file=sys.stderr)
        return []
    calls: dict[str, tuple[int, str, str, str]] = {}  # tool_use id -> line, command, time, cwd
    results: dict[str, tuple[bool, str]] = {}
    for i, line in enumerate(lines):
        if "merg" not in line.lower() and '"tool_result"' not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") if isinstance(record, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") in guard.SHELL_TOOLS:
                tool_input = block.get("input")
                command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
                if any(guard.is_merge(t) for t in guard.segments(command)):
                    when = str(record.get("timestamp", ""))[:16]
                    calls[str(block.get("id"))] = (i, command, when, str(record.get("cwd") or ""))
            elif block.get("type") == "tool_result" and str(block.get("tool_use_id")) in calls:
                results[str(block.get("tool_use_id"))] = (block.get("is_error") is True, body_of(block))
    names = {guard.MERGE_CHAINED: "chained", guard.MERGE_API: "api", guard.MERGE_UNREVIEWED: "unreviewed"}
    rows = []
    for uid, (i, command, when, cwd) in calls.items():
        cut = scratch / "cut.jsonl"
        cut.write_text("".join(lines[: i + 1]), encoding="utf-8")
        started = time.perf_counter()
        held = guard.check_merge(command, str(cut), uid)
        slowest[0] = max(slowest[0], time.perf_counter() - started)
        verdict = names.get(held, "other") if held else "allow"
        if verdict == "allow" and config is not None and guard.check_command(command, cwd, config):
            verdict = "checkout"
        flat = " ".join(command.split())
        rows.append(Row(path.parent.name, path.stem[:8], when, outcome_of(results.get(uid)), verdict, flat))
    return rows


def table(rows: list[Row]) -> list[str]:
    counts = Counter((r.outcome, r.verdict) for r in rows)
    return [f"  {'outcome':13} {'verdict':11} calls"] + [f"  {o:13} {v:11} {n}" for (o, v), n in sorted(counts.items())]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--projects", type=Path, default=None)
    parser.add_argument("--since", default=RULE_LIVE)
    parser.add_argument("--detail", action="store_true")
    args = parser.parse_args()
    projects = args.projects or Path(os.environ.get("LOST_MARY_PROJECTS_DIR") or Path.home() / ".claude" / "projects")
    guard = load_guard()
    config = guard.load_config()
    transcripts = sorted(projects.glob("*/*.jsonl"))
    slowest = [0.0]
    rows: list[Row] = []
    with tempfile.TemporaryDirectory(prefix="replay-merges-") as tmp:
        for path in transcripts:
            rows.extend(replay(guard, path, Path(tmp), config, slowest))
    recent = [r for r in rows if r.when >= args.since]
    print(f"{len(rows)} merge calls in {len(transcripts)} transcripts (slowest check {slowest[0]:.2f} s)")
    print("\nall time:")
    print("\n".join(table(rows)))
    print(f"\nsince {args.since}:")
    print("\n".join(table(recent)))
    if args.detail:
        print(f"\nsince {args.since}: went through but held, or refused but allowed:")
        for r in sorted(recent, key=lambda r: r.when):
            if (r.outcome == "ok" and r.verdict != "allow") or (r.outcome == "refused" and r.verdict == "allow"):
                print(f"  {r.when} {r.project[:22]:22} {r.session} {r.outcome:8} {r.verdict:10} {r.command[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
