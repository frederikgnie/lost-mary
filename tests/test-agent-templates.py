#!/usr/bin/env python3
"""Guard against drift between agent files and the handoff contract.

The JSON template embedded in each `agents/*.md` is the version the model
actually reads. Fixtures in tests/handoff-fixtures/ are not: they can stay
green while an agent file drifts. This asserts every agent's template carries
exactly the payload keys the validator requires for that role.

Usage: python tests/test-agent-templates.py     (exit 0 = all aligned)
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
VALIDATOR = ROOT / "scripts" / "validate-handoff.py"

# Top-level envelope keys every agent template must show.
REQUIRED_ENVELOPE = {
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
}


def load_validator() -> Any:
    spec = importlib.util.spec_from_file_location("_v", VALIDATOR)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    validator = load_validator()
    failures: list[str] = []

    agent_files = sorted(AGENTS.glob("*.md"))
    if not agent_files:
        print(f"no agent files found under {AGENTS}", file=sys.stderr)
        return 1

    for path in agent_files:
        text = path.read_text(encoding="utf-8")

        name_match = re.search(r"^name:\s*(\S+)", text, re.MULTILINE)
        if name_match is None:
            failures.append(f"{path.name}: no 'name:' in frontmatter")
            continue
        role = name_match.group(1)

        block = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
        if block is None:
            failures.append(f"{path.name}: no ```json handoff template")
            continue
        body = block.group(1)

        # Envelope keys sit at two-space indent in the templates.
        envelope = set(re.findall(r'^\s{2}"([a-z_]+)"\s*:', body, re.MULTILINE))
        missing_env = REQUIRED_ENVELOPE - envelope
        if missing_env:
            failures.append(
                f"{path.name} ({role}): template missing envelope keys "
                f"{sorted(missing_env)}"
            )

        if '"payload"' not in body:
            failures.append(f"{path.name} ({role}): template has no payload block")
            continue
        payload_text = body.split('"payload"', 1)[1]
        payload_keys = set(re.findall(r'^\s{4}"([a-z_]+)"\s*:', payload_text, re.MULTILINE))

        required = set(validator.PAYLOAD_REQUIRED.get(role, ()))
        if not required:
            failures.append(
                f"{path.name} ({role}): validator has no PAYLOAD_REQUIRED entry "
                "for this role - agent and validator disagree that it emits a handoff"
            )
            continue

        missing = required - payload_keys
        extra = payload_keys - required
        if missing:
            failures.append(
                f"{path.name} ({role}): template missing required payload keys {sorted(missing)}"
            )
        if extra:
            failures.append(
                f"{path.name} ({role}): template shows payload keys the validator "
                f"does not require {sorted(extra)}"
            )
        if not missing and not extra:
            print(f"  ok   {path.name} ({role}): {len(required)} payload keys aligned")

        # The template's own from_role must match the agent's name.
        declared = re.search(r'"from_role"\s*:\s*"([^"]+)"', body)
        if declared and declared.group(1) != role:
            failures.append(
                f"{path.name} ({role}): template declares from_role "
                f"{declared.group(1)!r}"
            )

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"All {len(agent_files)} agent templates match the validator contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
