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

No lingering teammates
----------------------
An `explore`, `review` or `implement` spawn given a `name` has it dropped
(with `team_name`): a named spawn is a teammate that stays listed as running
after its report, and hands the report back truncated. See unnamed().

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
within the last FABLE_RETRY_HOURS. While one is, no scan runs at all. After
the window the first fable spawn takes a PROBE_SECONDS lease and tries Fable;
spawns started meanwhile stay on opus; a fresh 429 re-arms the fallback. The
spawns already in flight when the limit first hits fail - nothing can see the
limit before it - and are respawned unchanged. The scan reads only bytes
appended since the last one (per-file offsets, newest files first, a 64 KB
overlap for a record split across reads, a SCAN_SECONDS budget checked between
chunks). State lives in `<agent-library>/state/fable-out.json`, replaced
atomically; a malformed or future-dated field is dropped.
`LOST_MARY_FABLE=off` forces the fallback, `=on` disables it.
`LOST_MARY_PROJECTS_DIR` / `LOST_MARY_STATE_DIR` relocate the two roots (tests).
`LOST_MARY_SCAN_SECONDS` overrides the budget (tests). On any error the spawn
goes unchanged.

Not moved (known gaps): a plugin role (`plugin:role`, whose file is not read),
a role without a `model:` line that inherits a fable LEAD (the payload does not
name the lead's model; `CLAUDE_CODE_SUBAGENT_MODEL` is honoured), and a project
agent file under a directory other than the payload's `cwd`. A 429 on another
model that also bills usage credits would arm it too - harmless, since that
model would be out as well. "Only Fable bills to usage credits" is observed,
not documented.
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

LIBRARY_ROLES = frozenset({"explore", "review", "implement"})  # spawned and forgotten: never teammates
TEAM_KEYS = ("name", "team_name")
FALLBACK_MODEL = "opus"  # the family alias: the newest permitted Opus (sub-agents docs)
FABLE_MARKER = b'"apiError":"model_requires_usage_credits"'
FABLE_RETRY_HOURS = 6.0
SCAN_SECONDS = 4.0  # well inside the hook's 10 s timeout
PROBE_SECONDS = 600.0  # after the window, one spawn tries fable; the others stay on opus this long
CHUNK = 8 * 1024 * 1024
OVERLAP = 64 * 1024  # a record cut by the previous read is re-read whole
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


def failures_in(data: bytes) -> float | None:
    """The newest Fable usage-credit failure timestamp in a run of transcript lines, or None."""
    if FABLE_MARKER not in data:
        return None
    newest: float | None = None
    for line in data.splitlines():
        if FABLE_MARKER in line and (found := TIMESTAMP.search(line)):
            when = epoch(found.group(1).decode("ascii", "replace"))
            if when is not None and (newest is None or when > newest):
                newest = when
    return newest


def read_state(path: Path, now: float) -> dict[str, Any]:
    """The scan state, validated: a malformed or future-dated field is dropped, never trusted."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"last_seen": 0.0, "probe_until": 0.0, "offsets": {}}
    raw = raw if isinstance(raw, dict) else {}

    def stamp(name: str, limit: float) -> float:
        value = raw.get(name)
        return float(value) if isinstance(value, int | float) and 0 <= value <= limit else 0.0

    offsets = raw.get("offsets")
    clean = (
        {k: int(v) for k, v in offsets.items() if isinstance(k, str) and isinstance(v, int) and v >= 0}
        if isinstance(offsets, dict)
        else {}
    )
    return {
        "last_seen": stamp("last_seen", now + 60),
        "probe_until": stamp("probe_until", now + PROBE_SECONDS + 60),
        "offsets": clean,
    }


def scan(projects: Path, state: dict[str, Any], now: float) -> None:
    """Read the bytes appended since the last scan to every transcript changed within the window, newest first.

    Each file's offset is kept, so a spawn reads new bytes only; a read starts OVERLAP bytes early so a record
    cut in half by the previous read is seen whole. A file that shrank is read from the start. The budget is
    checked between chunks; whatever was read is kept, so a scan cut short resumes where it stopped. A file that
    cannot be read keeps its old offset and is tried again next time.
    """
    window = FABLE_RETRY_HOURS * 3600
    offsets: dict[str, int] = state["offsets"]
    candidates: list[tuple[float, int, Path]] = []
    for path in projects.glob("**/*.jsonl"):
        try:
            info = path.stat()
        except OSError:
            continue
        if info.st_mtime >= now - window:
            candidates.append((info.st_mtime, info.st_size, path))
    candidates.sort(key=lambda c: c[0], reverse=True)
    live = {str(p) for _, _, p in candidates}
    for name in [k for k in offsets if k not in live]:
        del offsets[name]  # older than the window: never needed again
    try:
        scan_seconds = float(os.environ.get("LOST_MARY_SCAN_SECONDS") or SCAN_SECONDS)
    except ValueError:
        scan_seconds = SCAN_SECONDS
    started = time.monotonic()
    for _, size, path in candidates:
        name = str(path)
        done = offsets.get(name, 0)
        if done > size:
            done = 0  # rewritten or truncated
        if done >= size:
            continue
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, done - OVERLAP))
                while done < size:
                    if time.monotonic() - started > scan_seconds:
                        return
                    chunk = handle.read(min(CHUNK, size - handle.tell()))
                    if not chunk:
                        break
                    when = failures_in(chunk)
                    if when is not None and when > state["last_seen"]:
                        state["last_seen"] = min(when, now)
                    done = handle.tell()
                    offsets[name] = done
                    if done < size:
                        handle.seek(max(0, done - OVERLAP))
        except OSError:
            continue


def fable_out(now: float) -> bool:
    """Whether spawns should leave fable now: a usage-credit failure within the window, or another spawn probing."""
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
    state = read_state(state_file, now)
    if now - state["last_seen"] < window:
        return True  # already known: no scan
    scan(projects, state, now)
    out = now - state["last_seen"] < window
    if not out and state["last_seen"] > 0:
        # Fable failed before and the window has passed: one spawn probes it; the rest stay on opus meanwhile.
        if state["probe_until"] > now:
            out = True
        else:
            state["probe_until"] = now + PROBE_SECONDS
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, state_file)  # concurrent sessions: the last whole write wins, never a torn file
    except OSError as exc:
        notice(f"cannot write {state_file} ({exc})")
    return out


def fallback(payload: dict[str, Any], tool_input: dict[str, Any]) -> str | None:
    """The additionalContext line when this fable spawn must move to opus because Fable is out; else None."""
    requested = tool_input.get("model")
    if requested:
        if not is_fable(requested):
            return None  # an explicit non-fable choice is the lead's
    else:
        role = tool_input.get("subagent_type")
        cwd = payload.get("cwd")
        if not isinstance(role, str):
            return None
        model = agent_model(role, cwd if isinstance(cwd, str) else ".")
        if model in (None, "inherit"):  # no model line: CLAUDE_CODE_SUBAGENT_MODEL, else the lead's (unknown here)
            model = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
        if not is_fable(model):
            return None
    if not fable_out(time.time()):
        return None
    return (
        f"Fable is out (a usage-credit 429 within {FABLE_RETRY_HOURS:g} h), so this spawn runs on "
        f"{FALLBACK_MODEL} (the newest Opus) instead of fable; it moves back on its own."
    )


def unnamed(tool_input: dict[str, Any]) -> str | None:
    """The additionalContext line when a library role was spawned with a `name`, which is dropped; else None.

    A named spawn becomes an agent-team teammate: it stays listed as running after its report (four reviewers
    sat there for up to 3 h on 2026-09-25, one on an exhausted model that could not even take a shutdown), and
    its report reaches the lead as an idle notification truncated at about 3 KB. Unnamed, the role runs as an
    ordinary subagent (in this harness subagents already run in the background) that ends when it reports,
    hands back its whole report, and can still be resumed with SendMessage to its agent id.
    """
    role = tool_input.get("subagent_type")
    if not (isinstance(role, str) and role.strip().lower() in LIBRARY_ROLES):
        return None
    if not any(tool_input.get(key) for key in TEAM_KEYS):
        return None
    return (
        f"`name`/`team_name` were dropped (library roles never join a team): a named `{role}` becomes a "
        "teammate that stays running after its report. It runs as an ordinary subagent that ends when it "
        "reports; resume it with SendMessage to the agent id in the result."
    )


def rewrite(payload: dict[str, Any], tool_input: dict[str, Any]) -> dict[str, Any] | None:
    """The PreToolUse output with the rewritten spawn, or None when nothing changes. Never raises."""
    updated = dict(tool_input)
    notes: list[str] = []
    try:
        if note := unnamed(tool_input):
            for key in TEAM_KEYS:
                updated.pop(key, None)
            notes.append(note)
    except Exception as exc:  # a convenience: never let it cost the spawn
        notice(f"name check skipped ({exc!r})")
    try:
        if note := fallback(payload, tool_input):
            updated["model"] = FALLBACK_MODEL
            notes.append(note)
    except Exception as exc:
        notice(f"fable fallback skipped ({exc!r})")
    if not notes:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": updated,
            "additionalContext": "check-spawn: " + " Also, ".join(notes),
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
        output = rewrite(payload, tool_input)
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
