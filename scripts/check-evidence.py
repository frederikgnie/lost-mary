#!/usr/bin/env python3
"""check-evidence: SubagentStop hook that tests a subagent's claims against its transcript.

Why this exists
---------------
"Never claim tests passed unless you ran them" is a rule; rules get forgotten.
The transcript is the evidence. This hook reads the subagent's own JSONL
transcript and blocks the stop when:

  1. the final message claims validation (tests pass, typecheck clean, ...)
     but no validation command was run;
  2. the final message claims validation but the last run of that kind failed;
  3. files were edited, nothing was run, and the message does not say so
     explicitly ("validation not run because ...").

One strike: after a block the agent re-emits, and the second stop is allowed
even if the message is still wrong. That keeps a confused agent from looping;
the lead sees both messages and judges.

Wiring: SubagentStop, matcher restricted to roles that write code
(e.g. "implement|general-purpose"). Exit 2 blocks the stop and returns stderr
to the subagent. Exit 0 allows. Fails OPEN, with a notice on stderr, whenever
the payload or transcript cannot be read - enforcement must never wedge a
session, and must never vanish in silence either.

Declared runtime dependencies (see capabilities.md): SubagentStop stdin fields
`agent_id`, `agent_type`, `transcript_path` (parent session), and
`last_assistant_message`; subagent transcript stored at
`<parent-dir>/<parent-stem>/subagents/agent-<agent_id>.jsonl` with
`message.content[]` entries of type `tool_use` / `tool_result`. The transcript
format is internal to Claude Code; tests/test-hooks.py pins the observed shape.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

VALIDATION_RE = re.compile(
    r"\b(?:pytest|py\.test|unittest|ty\s+check|ruff\s+(?:check|format)|mypy|pyright|"
    r"pycheck\.py|npm\s+(?:test|run\s+(?:test|lint|typecheck|check))|pnpm\s+(?:test|lint)|"
    r"yarn\s+(?:test|lint)|cargo\s+(?:test|check|clippy)|go\s+(?:test|vet)|"
    r"make\s+(?:test|check|lint)|tox|nox|dotnet\s+test|gradle\w*\s+test|mvn\s+test|"
    r"tsc|eslint|jest|vitest)\b"
)

# A claim is a validation noun near a success verb, or the usual summary idioms.
CLAIM_RE = re.compile(
    r"\b(?:tests?|suite|checks?|lint(?:ing)?|type[ -]?check(?:s|ing)?|ty|ruff|pytest|mypy|"
    r"pyright|ci|build|validation)\b[^.\n]{0,40}?\b(?:pass(?:es|ed|ing)?|green|clean|"
    r"succeed(?:s|ed)?|ok)\b"
    r"|\b(?:all|\d+)\s+tests?\s+(?:pass|passed|passing)\b"
    r"|\bno\s+(?:type\s+)?errors?\b|\b0\s+(?:errors?|failures?|failed)\b"
    r"|\b(?:validated|verified)\b",
    re.IGNORECASE,
)

# Honest statements that validation did not happen. Stripped from the message
# before claim matching, so "not verified" is a disclaimer, not a claim.
DISCLAIMER_RE = re.compile(
    r"\b(?:not|never|didn'?t|did not|could not|couldn'?t|unable to|without)\s+"
    r"(?:run(?:ning)?|execut(?:e|ing)|verif(?:y|ied|ying)|validat(?:e|ed|ing)|test(?:ed|ing)?)\b"
    r"|\bunverified\b|\bunvalidated\b|\bnot\s+(?:run|verified|validated|tested|checked)\b"
    r"|\bno\s+(?:tests?|validation|checks?)\s+(?:were\s+|was\s+)?run\b"
    r"|\bvalidation\s+(?:was\s+)?skipped\b|\bskipped\s+(?:the\s+)?(?:tests?|validation)\b"
    r"|\bran\s+nothing\b|\bnothing\s+(?:was\s+)?(?:run|executed)\b",
    re.IGNORECASE,
)


@dataclass
class Evidence:
    edited: list[str] = field(default_factory=list)
    runs: list[tuple[str, bool, str]] = field(default_factory=list)  # (command, ok, snippet)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict)]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def read_transcript(path: Path) -> Evidence:
    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    evidence = Evidence()
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = record.get("message") if isinstance(record, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                kind = item.get("type")
                if kind == "tool_use":
                    name = item.get("name")
                    tool_input = item.get("input") if isinstance(item.get("input"), dict) else {}
                    if isinstance(item.get("id"), str) and isinstance(name, str):
                        pending[item["id"]] = (name, tool_input)
                    if name in EDIT_TOOLS:
                        target = tool_input.get("file_path") or tool_input.get("notebook_path")
                        if isinstance(target, str):
                            evidence.edited.append(target)
                elif kind == "tool_result":
                    use = pending.pop(item.get("tool_use_id", ""), None)
                    if use is None or use[0] != "Bash":
                        continue
                    command = str(use[1].get("command", ""))
                    if not VALIDATION_RE.search(command):
                        continue
                    text = _text_of(item.get("content"))
                    ok = not item.get("is_error") and not text.lstrip().startswith("Exit code")
                    evidence.runs.append((command.strip(), ok, text.strip()[:300]))
    return evidence


def subagent_transcript(event: dict[str, Any]) -> Path | None:
    explicit = event.get("agent_transcript_path")
    if isinstance(explicit, str) and Path(explicit).is_file():
        return Path(explicit)
    parent = event.get("transcript_path")
    agent_id = event.get("agent_id")
    if not isinstance(parent, str) or not isinstance(agent_id, str):
        return None
    parent_path = Path(parent)
    derived = parent_path.parent / parent_path.stem / "subagents" / f"agent-{agent_id}.jsonl"
    if derived.is_file():
        return derived
    # Layout changed? Search the project directory for the agent's file.
    project_dir = parent_path.parent
    if project_dir.is_dir():
        for candidate in project_dir.rglob(f"agent-{agent_id}.jsonl"):
            return candidate
    return None


def strike_marker(agent_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", agent_id)
    return Path(tempfile.gettempdir()) / "claude-check-evidence" / safe


def read_stdin_json() -> Any:
    """Bytes + utf-8-sig: PowerShell prepends a BOM when piping to a native exe."""
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read().decode("utf-8-sig", "replace") if buffer is not None else sys.stdin.read()
    return json.loads(raw.strip())


def decide(message: str, evidence: Evidence) -> str | None:
    """Return the block reason, or None to allow."""
    disclaims = bool(DISCLAIMER_RE.search(message))
    claims = bool(CLAIM_RE.search(DISCLAIMER_RE.sub(" ", message)))
    if claims and not evidence.runs:
        return (
            "Your final message claims validation, but the transcript contains no "
            "test/typecheck/lint command. Run the project's checks now and report "
            "the exact command and result, or remove the claim and state plainly "
            "that you did not verify."
        )
    if claims:
        failed = [r for r in evidence.runs if not r[1]]
        last_ok = evidence.runs[-1][1]
        if failed and not last_ok:
            cmd, _, snippet = evidence.runs[-1]
            return (
                "Your final message claims validation, but the last validation "
                f"command failed:\n  $ {cmd}\n  {snippet[:200]}\n"
                "Fix and re-run, or report the failure honestly."
            )
    if evidence.edited and not evidence.runs and not disclaims:
        files = sorted(set(evidence.edited))
        shown = "\n".join(f"  - {f}" for f in files[:8])
        more = f"\n  ... {len(files) - 8} more" if len(files) > 8 else ""
        return (
            f"You changed {len(files)} file(s) but ran no validation:\n{shown}{more}\n"
            "Run the project's checks (see its CLAUDE.md for the exact commands) and "
            "report the command and result - or say explicitly that validation was "
            "not run and why."
        )
    return None


def main() -> int:
    try:
        event = read_stdin_json()
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"check-evidence: unreadable hook payload ({exc}); allowing.", file=sys.stderr)
        return 0
    if not isinstance(event, dict):
        print("check-evidence: payload was not an object; allowing.", file=sys.stderr)
        return 0
    if event.get("stop_hook_active") is True:
        return 0

    agent_id = event.get("agent_id")
    message = event.get("last_assistant_message")
    if not isinstance(agent_id, str) or not isinstance(message, str):
        return 0

    marker = strike_marker(agent_id)
    if marker.exists():
        # Second stop after a block: let it through and clean up.
        try:
            marker.unlink()
        except OSError:
            pass
        return 0

    transcript = subagent_transcript(event)
    if transcript is None:
        print(
            "check-evidence: subagent transcript not found for "
            f"agent_id={agent_id!r}; allowing (claims unverified).",
            file=sys.stderr,
        )
        return 0

    try:
        evidence = read_transcript(transcript)
    except OSError as exc:
        print(f"check-evidence: cannot read {transcript} ({exc}); allowing.", file=sys.stderr)
        return 0

    reason = decide(message, evidence)
    if reason is None:
        return 0

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("blocked once\n", encoding="utf-8")
    except OSError:
        pass
    print(f"Evidence check failed ({event.get('agent_type', 'subagent')}).\n{reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
