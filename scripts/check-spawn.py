#!/usr/bin/env python3
"""check-spawn: PreToolUse hook on the Agent tool - an `implement` spawn carries its contract or does not start.

Why this exists
---------------
skills/lost-mary/SKILL.md says every `implement` spawn carries OWNED, OFF-LIMITS,
DONE MEANS and VALIDATION, and agents/implement.md says a spawn missing one comes
back as `MISSING: <field>`. That is a rule enforced by the subagent - after the
spawn, at the cost of a full round trip and a model invocation, and only if the
subagent remembers. This hook enforces it before the spawn: the Agent call is
inspected, and an `implement` brief lacking a required field is blocked with the
same `MISSING:` line the lead already knows how to act on. Nothing else is
judged - the fields' content is the lead's business.

Wiring: PreToolUse with matcher "Agent". Exit 2 blocks the call and returns
stderr to the model; exit 0 allows. Fails OPEN, with a notice on stderr, when
the payload cannot be read. Roles other than `implement` are never blocked.

Runtime dependencies (see capabilities.md): PreToolUse stdin fields `tool_name`,
`tool_input` with `subagent_type` and `prompt` (the Agent tool's documented
parameters). The set of governed roles and their required fields is the table
below; keep it in step with the spawn block in skills/lost-mary/SKILL.md.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

TOOL = "Agent"
REQUIRED: dict[str, tuple[str, ...]] = {
    "implement": ("OWNED", "OFF-LIMITS", "DONE MEANS", "VALIDATION"),
}
# `OWNED:` at the start of a line, optionally bolded or bulleted, case-insensitive.
FIELD_LINE = r"(?im)^[\s*#>-]*{field}\s*:"


def notice(text: str) -> None:
    print(f"check-spawn: {text}", file=sys.stderr)


def read_payload() -> dict[str, Any] | None:
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read() if buffer is not None else sys.stdin.read().encode("utf-8")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def missing_fields(role: str, prompt: str) -> list[str]:
    """Required fields of `role` that `prompt` does not carry as a `FIELD:` line."""
    required = REQUIRED.get(role.strip().lower(), ())
    return [field for field in required if not re.search(FIELD_LINE.format(field=re.escape(field)), prompt)]


def main() -> int:
    payload = read_payload()
    if payload is None:
        notice("unreadable payload - allowing")
        return 0
    if payload.get("tool_name") != TOOL:
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        notice("no tool_input - allowing")
        return 0
    role = tool_input.get("subagent_type")
    prompt = tool_input.get("prompt")
    if not isinstance(role, str) or not isinstance(prompt, str):
        return 0
    missing = missing_fields(role, prompt)
    if not missing:
        return 0
    print(
        f"MISSING: {', '.join(missing)}\n"
        f"An `{role}` spawn carries OWNED / OFF-LIMITS / DONE MEANS / VALIDATION as `FIELD: value` lines "
        f"(skills/lost-mary/SKILL.md, step 3). Fill the missing field(s) from the repository and spawn again - "
        f"this is the lead's to fill, never a question for the user.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a hook fails open, loudly - never with a traceback
        notice(f"unexpected error ({exc!r}) - allowing")
        sys.exit(0)
