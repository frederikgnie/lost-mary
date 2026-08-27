#!/usr/bin/env python3
"""Exit-code tests for the enforcement hooks.

Hooks are the only deterministic enforcement point in this library, so their
allow/block behaviour is tested rather than assumed. Exit 0 = allow, 2 = block.

Usage: python tests/test-hooks.py     (exit 0 = all passed)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "guard-readonly-bash.py"
HANDOFF = ROOT / "scripts" / "check-handoff-hook.py"
FIXTURES = ROOT / "tests" / "handoff-fixtures"
RUNTIME_INVALID = ROOT / "tests" / "handoff-fixtures-runtime-invalid"

ALLOW, BLOCK = 0, 2

failures: list[str] = []


def run_hook(script: Path, event: dict[str, object], bom: bool = False) -> int:
    """Run a hook with `event` on stdin.

    stdin is written as explicit UTF-8 bytes, not text mode: on Windows text
    mode uses the locale codepage, which cannot encode a BOM and would make the
    bom=True cases unrunnable rather than meaningful.
    """
    payload = json.dumps(event)
    if bom:
        payload = chr(0xFEFF) + payload
    result = subprocess.run(
        [sys.executable, str(script)],
        input=payload.encode("utf-8"),
        capture_output=True,
    )
    return result.returncode


def expect(name: str, actual: int, wanted: int) -> None:
    verdict = "ok  " if actual == wanted else "FAIL"
    if actual != wanted:
        failures.append(f"{name}: expected exit {wanted}, got {actual}")
    print(f"  {verdict} {name}")


def bash_event(agent_type: str | None, command: str) -> dict[str, object]:
    event: dict[str, object] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(ROOT),
    }
    if agent_type is not None:
        event["agent_type"] = agent_type
    return event


def stop_event(agent_type: str, message: str, active: bool = False) -> dict[str, object]:
    return {
        "hook_event_name": "SubagentStop",
        "agent_type": agent_type,
        "agent_id": "test-agent-id",
        "last_assistant_message": message,
        "stop_hook_active": active,
    }


print("guard-readonly-bash.py - read-only role must not mutate")
# Legitimate inspection must survive, or reviewers become useless.
expect("reviewer: git diff", run_hook(GUARD, bash_event("reviewer", "git diff HEAD~1")), ALLOW)
expect("reviewer: git log", run_hook(GUARD, bash_event("reviewer", "git log --oneline -5")), ALLOW)
expect("reviewer: rg with -> in pattern", run_hook(GUARD, bash_event("reviewer", 'rg "foo->bar" src/')), ALLOW)
expect("reviewer: => in pattern", run_hook(GUARD, bash_event("reviewer", 'rg "a => b" src/')), ALLOW)
expect("reviewer: benign 2>/dev/null", run_hook(GUARD, bash_event("reviewer", "pytest -q 2>/dev/null")), ALLOW)
expect("reviewer: benign 2>&1", run_hook(GUARD, bash_event("reviewer", "make check 2>&1")), ALLOW)
expect("reviewer: git worktree list", run_hook(GUARD, bash_event("reviewer", "git worktree list")), ALLOW)
expect("reviewer: git config --get", run_hook(GUARD, bash_event("reviewer", "git config --get remote.origin.url")), ALLOW)
expect("architect: cat file", run_hook(GUARD, bash_event("architect", "cat src/app.py")), ALLOW)

# Mutations must be blocked.
expect("reviewer: redirect to file", run_hook(GUARD, bash_event("reviewer", "echo hacked > src/app.py")), BLOCK)
expect("reviewer: append to file", run_hook(GUARD, bash_event("reviewer", "echo x >> src/app.py")), BLOCK)
expect("reviewer: sed -i", run_hook(GUARD, bash_event("reviewer", "sed -i 's/a/b/' src/app.py")), BLOCK)
expect("reviewer: rm", run_hook(GUARD, bash_event("reviewer", "rm -rf build/")), BLOCK)
expect("reviewer: git commit", run_hook(GUARD, bash_event("reviewer", 'git commit -m "fix"')), BLOCK)
expect("reviewer: git checkout", run_hook(GUARD, bash_event("reviewer", "git checkout -- src/")), BLOCK)
expect("reviewer: git worktree add", run_hook(GUARD, bash_event("reviewer", "git worktree add ../x")), BLOCK)
expect("reviewer: tee", run_hook(GUARD, bash_event("reviewer", "cat a | tee out.txt")), BLOCK)
expect("reviewer: python -c", run_hook(GUARD, bash_event("reviewer", 'python -c "open(1,2)"')), BLOCK)
expect("reviewer: pip install", run_hook(GUARD, bash_event("reviewer", "pip install requests")), BLOCK)
expect("security-reviewer: redirect", run_hook(GUARD, bash_event("security-reviewer", "echo x > f.py")), BLOCK)

# Scope: the hook must not police roles that are allowed to write.
expect("implementer: redirect allowed", run_hook(GUARD, bash_event("implementer", "echo x > src/app.py")), ALLOW)
expect("tester: sed -i allowed", run_hook(GUARD, bash_event("tester", "sed -i 's/a/b/' t.py")), ALLOW)
expect("main session (no agent_type)", run_hook(GUARD, bash_event(None, "rm -rf build/")), ALLOW)

# BOM regression (PowerShell pipes prepend one; hooks fail open on parse error,
# so a BOM silently disabled enforcement on Windows).
expect("reviewer: BOM + write is still blocked", run_hook(GUARD, bash_event("reviewer", "echo x > a.py"), bom=True), BLOCK)
expect("reviewer: BOM + read is still allowed", run_hook(GUARD, bash_event("reviewer", "git diff"), bom=True), ALLOW)

print()
print("check-handoff-hook.py - specialist must emit a valid handoff")
valid_reviewer = (FIXTURES / "reviewer.json").read_text(encoding="utf-8")
valid_implementer = (FIXTURES / "implementer.json").read_text(encoding="utf-8")
weak = (RUNTIME_INVALID / "weak-proof-colon-only.json").read_text(encoding="utf-8")

expect("reviewer: valid handoff", run_hook(HANDOFF, stop_event("reviewer", valid_reviewer)), ALLOW)
expect("implementer: valid handoff", run_hook(HANDOFF, stop_event("implementer", valid_implementer)), ALLOW)
expect(
    "reviewer: valid handoff in json fence",
    run_hook(HANDOFF, stop_event("reviewer", f"```json\n{valid_reviewer}\n```")),
    ALLOW,
)
expect(
    "reviewer: prose only",
    run_hook(HANDOFF, stop_event("reviewer", "I reviewed everything and it looks good to me.")),
    BLOCK,
)
expect(
    "reviewer: truncated json",
    run_hook(HANDOFF, stop_event("reviewer", '{"schema_version": "1.0", "from_role":')),
    BLOCK,
)
expect(
    "implementer: claims reviewer role",
    run_hook(HANDOFF, stop_event("implementer", valid_reviewer)),
    BLOCK,
)
expect(
    "reviewer: weak proof token rejected",
    run_hook(HANDOFF, stop_event("reviewer", weak)),
    BLOCK,
)
# Roles and conditions the hook must not police.
expect("lead: prose allowed", run_hook(HANDOFF, stop_event("lead", "Integrated and shipped.")), ALLOW)
expect(
    "stop_hook_active guard (no retry loop)",
    run_hook(HANDOFF, stop_event("reviewer", "prose only", active=True)),
    ALLOW,
)
expect(
    "BOM + prose is still blocked",
    run_hook(HANDOFF, stop_event("reviewer", "I reviewed it, looks fine."), bom=True),
    BLOCK,
)
expect(
    "BOM + valid handoff is still allowed",
    run_hook(HANDOFF, stop_event("reviewer", valid_reviewer), bom=True),
    ALLOW,
)

print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All hook tests passed.")
