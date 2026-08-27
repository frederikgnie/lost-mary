#!/usr/bin/env python3
"""SubagentStop hook: enforce the handoff contract on real agent output.

Without this, `validate-handoff.py` only ever runs against fixtures in CI and
the lead is asked in prose to check handoffs — the same trust-the-agent problem
the contract exists to solve. This puts the validator in the loop: a specialist
that ends without a conforming handoff is blocked and told why, and must
re-emit before it can stop.

SubagentStop supplies `agent_type` and `last_assistant_message` directly, so no
transcript parsing is needed.

Wiring: SubagentStop matcher "*". Exit 2 blocks the stop and returns stderr to
the agent. Exit 0 allows.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

# Roles required to emit a handoff. `lead` is excluded: it is the recipient.
HANDOFF_ROLES = frozenset(
    {
        "architect",
        "researcher",
        "implementer",
        "debugger",
        "tester",
        "reviewer",
        "security-reviewer",
    }
)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def load_validator() -> Any | None:
    """Import validate-handoff.py from alongside this hook."""
    target = Path(__file__).resolve().parent / "validate-handoff.py"
    if not target.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_handoff_validator", target)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the handoff object out of a final message.

    Agents are told the final message is JSON only; be lenient about a stray
    code fence or trailing prose rather than failing on formatting alone.
    """
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    candidates.extend(m.group(1) for m in _FENCE.finditer(text))
    # Outermost brace span, for a message with prose around the object.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def read_stdin_json() -> Any:
    """Parse a JSON payload from stdin, tolerant of encoding and BOM.

    Reads BYTES and decodes utf-8-sig rather than using text mode. On Windows
    text-mode stdin decodes with the locale codepage, so the UTF-8 BOM that
    PowerShell prepends when piping to a native executable arrives as mojibake
    rather than U+FEFF and cannot be stripped as a character. utf-8-sig removes
    the BOM and forces correct decoding regardless of locale.
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        raw = buffer.read().decode("utf-8-sig", "replace")
    else:
        raw = sys.stdin.read().lstrip("﻿")
    return json.loads(raw.strip())


def main() -> int:
    try:
        event = read_stdin_json()
    except (json.JSONDecodeError, ValueError) as exc:
        # Fail open, but noisily: enforcement must never vanish in silence.
        print(f"check-handoff-hook: unreadable payload ({exc}); allowing.", file=sys.stderr)
        return 0
    if not isinstance(event, dict):
        print("check-handoff-hook: payload was not an object; allowing.", file=sys.stderr)
        return 0

    # Do not fight an already-blocked stop; avoids a retry loop.
    if event.get("stop_hook_active") is True:
        return 0

    agent_type = event.get("agent_type")
    if not isinstance(agent_type, str) or agent_type not in HANDOFF_ROLES:
        return 0

    message = event.get("last_assistant_message")
    if not isinstance(message, str):
        return 0

    def block(detail: str) -> int:
        print(
            f"Handoff contract violation ({agent_type}).\n{detail}\n\n"
            "Re-emit your final message as a single raw JSON object matching "
            "the handoff contract. Nothing before or after the JSON.",
            file=sys.stderr,
        )
        return 2

    handoff = extract_json(message)
    if handoff is None:
        return block("Final message contained no JSON object.")

    validator = load_validator()
    if validator is None:
        # Fail open on a broken install rather than wedging every subagent.
        print(
            "warning: validate-handoff.py not found next to check-handoff-hook.py; "
            "handoff not verified.",
            file=sys.stderr,
        )
        return 0

    errors, warnings = validator.check(handoff)

    # An agent must not claim a role it is not running as.
    claimed = handoff.get("from_role")
    if isinstance(claimed, str) and claimed != agent_type:
        errors = [*errors, f"from_role is {claimed!r} but this agent is {agent_type!r}"]

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if errors:
        return block("\n".join(f"  - {e}" for e in errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
