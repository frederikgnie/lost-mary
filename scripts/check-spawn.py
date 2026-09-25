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

Fable fallback
--------------
`explore` and `review` run on fable. When the Fable allowance or the monthly
spend limit runs out, their spawns fail with "You've hit your monthly spend
limit" - and nothing in Claude Code switches model: `fallbackModel` excludes
billing and rate-limit errors by design (model-config docs), and no frontmatter
key does it either. So this hook does: a spawn that would run on fable (its
`model` input, else its agent file's `model:` frontmatter) is rewritten to
`model: "opus"` - the alias resolves to the newest Opus - while Fable is out,
through `hookSpecificOutput.updatedInput` (which replaces the whole input and
applies without a permission decision). The model is told in
`additionalContext`.

"Fable is out" is read from the transcripts: a record with
`"apiError":"model_requires_usage_credits"` (observed 2026-09-25, 2.1.281 - the
429 a Fable spawn gets at the limit; only Fable bills to usage credits) seen
within the last FABLE_RETRY_HOURS. After that one spawn tries Fable again: a
quick 429 if it is still out, which re-arms the fallback. The first spawn after
the limit hits still fails once - nothing can see the limit before it. State
lives in `<agent-library>/state/fable-out.json` (last failure seen, and how far
the transcripts were scanned, so each spawn reads only files changed since).
`LOST_MARY_FABLE=off` forces the fallback, `=on` disables it.
`LOST_MARY_PROJECTS_DIR` / `LOST_MARY_STATE_DIR` relocate the two roots (tests).
The scan is capped at SCAN_SECONDS; on any error the spawn goes unchanged.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOL = "Agent"
REQUIRED: dict[str, tuple[str, ...]] = {
    "implement": ("OWNED", "OFF-LIMITS", "DONE MEANS", "VALIDATION"),
}
# `OWNED:` at the start of a line, optionally bolded or bulleted, case-insensitive.
FIELD_LINE = r"(?im)^[\s*#>-]*{field}\s*:"

FALLBACK_MODEL = "opus"  # the family alias: the newest permitted Opus (sub-agents docs)
FABLE_MARKER = b'"apiError":"model_requires_usage_credits"'
FABLE_RETRY_HOURS = 6.0
SCAN_SECONDS = 4.0
FRONTMATTER_MODEL = re.compile(r"(?m)^model\s*:\s*['\"]?([^'\"\s#]+)")
TIMESTAMP = re.compile(rb'"timestamp"\s*:\s*"([^"]+)"')


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


def is_fable(model: object) -> bool:
    return isinstance(model, str) and ("fable" in model.lower() or "mythos" in model.lower())


def agent_model(role: str, cwd: str) -> str | None:
    """The `model:` frontmatter of the role's agent file - the project's first, then the user's."""
    if not re.fullmatch(r"[\w.-]+", role):
        return None  # plugin:role or anything path-like: not ours to read
    for base in (Path(cwd) / ".claude" / "agents", Path.home() / ".claude" / "agents"):
        path = base / f"{role}.md"
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        head = text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else ""
        found = FRONTMATTER_MODEL.search(head)
        return found.group(1) if found else None
    return None


def epoch(stamp: str) -> float | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC).timestamp()
    except ValueError:
        return None


def latest_failure(path: Path) -> float | None:
    """When the newest Fable usage-credit failure in a transcript happened, or None."""
    data = path.read_bytes()
    if FABLE_MARKER not in data:
        return None
    newest: float | None = None
    for line in data.splitlines():
        if FABLE_MARKER in line and (found := TIMESTAMP.search(line)):
            when = epoch(found.group(1).decode("ascii", "replace"))
            if when is not None and (newest is None or when > newest):
                newest = when
    return newest


def fable_out(now: float) -> bool:
    """Whether Fable failed on usage credits within FABLE_RETRY_HOURS, scanning only transcripts changed since."""
    match os.environ.get("LOST_MARY_FABLE", "").strip().lower():
        case "off":
            return True
        case "on":
            return False
        case _:
            pass
    window = FABLE_RETRY_HOURS * 3600
    projects = Path(os.environ.get("LOST_MARY_PROJECTS_DIR") or Path.home() / ".claude" / "projects")
    state_dir = Path(os.environ.get("LOST_MARY_STATE_DIR") or Path.home() / ".claude" / "agent-library" / "state")
    state_file = state_dir / "fable-out.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        state = {}
    last_seen = float(state.get("last_seen") or 0.0)
    since = max(float(state.get("scanned_until") or 0.0), now - window)
    started = time.monotonic()
    complete = True
    for path in projects.glob("**/*.jsonl"):
        if time.monotonic() - started > SCAN_SECONDS:
            complete = False  # the rest on the next spawn; scanned_until stays put
            break
        try:
            if path.stat().st_mtime < since:
                continue
            when = latest_failure(path)
        except OSError:
            continue
        if when is not None and when > last_seen:
            last_seen = when
    new_state = {"last_seen": last_seen, "scanned_until": now if complete else since}
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(new_state), encoding="utf-8")
    except OSError as exc:
        notice(f"cannot write {state_file} ({exc})")
    return now - last_seen < window


def fallback(payload: dict[str, Any], tool_input: dict[str, Any]) -> dict[str, Any] | None:
    """The PreToolUse output that moves a fable spawn to opus while Fable is out; None to leave it alone."""
    requested = tool_input.get("model")
    if requested:
        if not is_fable(requested):
            return None  # an explicit non-fable choice is the lead's
    else:
        role = tool_input.get("subagent_type")
        cwd = payload.get("cwd")
        if not isinstance(role, str) or not is_fable(agent_model(role, cwd if isinstance(cwd, str) else ".")):
            return None
    if not fable_out(time.time()):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {**tool_input, "model": FALLBACK_MODEL},
            "additionalContext": (
                f"check-spawn: Fable is out (a usage-credit 429 within {FABLE_RETRY_HOURS:g} h), so this "
                f"spawn runs on {FALLBACK_MODEL} (the newest Opus) instead of fable. It moves back to fable "
                "on its own once no failure has been seen for that long."
            ),
        }
    }


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
    missing = missing_fields(role, prompt) if isinstance(role, str) and isinstance(prompt, str) else []
    if not missing:
        try:
            output = fallback(payload, tool_input)
        except Exception as exc:  # the fallback is a convenience: never let it cost the spawn
            notice(f"fable fallback skipped ({exc!r})")
            output = None
        if output is not None:
            print(json.dumps(output))
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
