#!/usr/bin/env python3
"""replay-commands: every recorded shell command through guard-shared-checkouts, before and after a change.

Reads Claude Code transcripts (read-only) and runs each distinct Bash / PowerShell command, with the working
directory its record carries, through the shared-checkout check of this checkout's guard and of the guard at a
baseline git ref (default `main`), both with this machine's shared-checkouts config. It prints the commands whose
verdict differs - `BLOCK->allow` is a check the change lost, `allow->BLOCK` one it added - and the calls the guard
cannot read cleanly (unclear()), with whether they block or would hold a merge. Re-run it after changing the
scanner: real commands are what a model writes, which review rounds keep missing.

Not installed: a development tool for this repository, like usage.py and replay-merges.py.

  python scripts/replay-commands.py [--baseline REF] [--projects DIR] [--limit N]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
GUARD = HERE / "guard-shared-checkouts.py"


def load(path: Path, name: str) -> ModuleType:
    """A guard imported by path - registered in sys.modules first, as AGENTS.md requires."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"replay-commands: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def commands(projects: Path) -> list[tuple[str, str, bool]]:
    """Each distinct (command, cwd, powershell) in the transcripts, in the order first seen."""
    seen: dict[tuple[str, str, bool], None] = {}
    for path in sorted(projects.glob("*/*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            print(f"replay-commands: skipped {path} ({exc})", file=sys.stderr)
            continue
        for line in lines:
            if '"tool_use"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = record.get("message") if isinstance(record, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("name") not in ("Bash", "PowerShell"):
                    continue
                tool_input = block.get("input")
                command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
                if command:
                    seen.setdefault((command, str(record.get("cwd") or ""), block.get("name") == "PowerShell"))
    return list(seen)


def check(guard: ModuleType, command: str, cwd: str, config: object, powershell: bool) -> str | None:
    """The guard's block reason, calling the older check_command (no shell argument) the way it takes it."""
    try:
        return guard.check_command(command, cwd, config, powershell)
    except TypeError:
        return guard.check_command(command, cwd, config)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--baseline", default="main")
    parser.add_argument("--projects", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=40, help="differences to print in full")
    args = parser.parse_args()
    projects = args.projects or Path(os.environ.get("LOST_MARY_PROJECTS_DIR") or Path.home() / ".claude" / "projects")
    source = subprocess.run(
        ["git", "-C", str(HERE.parent), "show", f"{args.baseline}:scripts/guard-shared-checkouts.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if source.returncode != 0:
        print(f"replay-commands: cannot read the guard at {args.baseline}: {source.stderr.strip()}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="replay-commands-") as tmp:
        base_path = Path(tmp) / "guard_baseline.py"
        base_path.write_text(source.stdout, encoding="utf-8")
        base = load(base_path, "guard_baseline")
        head = load(GUARD, "guard_head")
    config_base, config_head = base.load_config(), head.load_config()
    if config_base is None or config_head is None:
        print("replay-commands: no shared-checkouts config on this machine - nothing to compare", file=sys.stderr)
        return 2
    found = commands(projects)
    diffs: list[tuple[str, str, str, bool]] = []
    unclear: list[tuple[str, str, bool, bool]] = []
    slowest = 0.0
    for command, cwd, powershell in found:
        try:
            before = check(base, command, cwd, config_base, powershell)
        except Exception:  # the baseline's own failure mode is fail-open
            before = None
        started = time.perf_counter()
        after = check(head, command, cwd, config_head, powershell)
        why = head.unclear(command, powershell) if hasattr(head, "unclear") else None
        slowest = max(slowest, time.perf_counter() - started)
        if why:
            held = bool(head.check_merge(command, "", "", cwd, powershell))
            unclear.append((why, command, bool(after), held))
        if bool(before) != bool(after):
            diffs.append(("BLOCK->allow" if before else "allow->BLOCK", command, cwd, powershell))
    lost = sum(1 for d in diffs if d[0] == "BLOCK->allow")
    print(f"{len(found)} distinct commands; slowest check {slowest * 1000:.0f} ms")
    added = len(diffs) - lost
    print(f"verdicts that differ from {args.baseline}: {len(diffs)} ({lost} BLOCK->allow, {added} allow->BLOCK)")
    for kind, command, cwd, powershell in diffs[: args.limit]:
        print(f"  [{kind}] {'PS' if powershell else 'sh'} cwd={cwd}: {' '.join(command.split())[:200]}")
    blocking = sum(1 for _, _, blocks, held in unclear if blocks or held)
    print(f"unclear calls: {len(unclear)} ({blocking} of them block or hold a merge)")
    for why, command, blocks, held in unclear[: args.limit]:
        flags = f"block={'yes' if blocks else 'no'} merge-held={'yes' if held else 'no'}"
        print(f"  [{why}] {flags}: {' '.join(command.split())[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
