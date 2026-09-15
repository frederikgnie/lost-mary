#!/usr/bin/env python3
"""check-evidence: SubagentStop hook that tests a subagent's claims against its transcript.

Why this exists
---------------
"Never claim tests passed unless you ran them" is a rule; rules get forgotten.
The transcript is the evidence. When a subagent that EDITED files tries to
stop, this hook reads its own JSONL transcript and blocks the stop when:

  A. the last run of any validation kind (test / lint / type) failed and the
     final message does not report a failure - whatever the wording;
  B. the message claims validation but no validation command was run;
  C. files were edited, nothing was run, and the message does not say so.

A subagent that edited nothing has nothing to prove; read-only briefs may quote
the state of the tests they found. One strike: Claude Code re-invokes the hook
on the re-emitted stop with `stop_hook_active: true`, and that stop is allowed,
so a confused agent cannot loop - the lead sees both messages and judges.

Where the evidence comes from. The witness file (`scripts/witness.py`, a
`PostToolUse` hook) when one exists for this session, the transcript otherwise.
The transcript is a fallback, not the source, because the docs call it internal
and say it is "written asynchronously" so it "may not yet include the current
turn's most recent messages when a hook fires" - i.e. a truthful agent can be
judged as having run nothing merely because its last tool calls have not landed
in the file yet. The witness file is appended as each call completes, so it
cannot lag. Falling back is normal and SILENT: `friction.py` reads a hook's
stderr as a fail-open notice, and a session without the witness hook wired is
not one. The rules below do not change with the source; only the reading does.

What counts as a validation run: a Bash command whose executable is a test,
lint or type-check tool - bare, `.exe`, quoted, path-prefixed (`EU_env/.venv/
Scripts/ty.exe check`), `python -m <tool>`, or behind `uv run`/`uvx` - and
that actually checks something (`--collect-only`, `--version`, `--help` do not).
`grep pytest` or `cat tox.ini` mention a tool; they do not run it. Pass/fail is
read from the tool result's error flag AND its text, because `pytest ... | tail`
hands bash the exit code of `tail`.

The lead's own claims (`--lead`, a Stop hook). The lead is what the user reads,
and nothing else checks it. With `--lead` the same rules apply to the lead's
final message: edits and runs of the current turn (records from the first one
carrying the payload's `prompt_id`) decide rules A and C; a claim of passing
validation needs a run somewhere in the session (rule B, "never say tests pass
unless they ran in this session"), and the last run of that kind in the
session must not have failed unreported. Conversation-only turns without a
claim pass untouched. One strike, as above.

Wiring: SubagentStop with a matcher for roles that write (e.g.
"implement|general-purpose"). Exit 2 blocks the stop and returns stderr to the
subagent; exit 0 allows. Fails OPEN, with a notice on stderr, when the payload
or transcript cannot be read.

Runtime dependencies (see capabilities.md): SubagentStop stdin fields
`session_id` (names the witness directory), `agent_id`, `agent_type`,
`agent_transcript_path` (preferred), `transcript_path` (parent session; the
subagent file is derived from it as a fallback), `last_assistant_message`,
`stop_hook_active`; witness lines in the format `scripts/witness.py` documents;
JSONL records whose `message.content[]` holds `tool_use` / `tool_result` items.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
RUN_TOOLS = frozenset({"Bash", "PowerShell"})

# --- the witness file ---------------------------------------------------------------------
# These three mirror scripts/witness.py, the producer, which imports nothing from here on purpose
# (its line format is the whole contract between them); tests/test-hooks.py asserts they still match.
EVIDENCE_ROOT_ENV = "EVIDENCE_ROOT"
DEFAULT_EVIDENCE_ROOT = Path.home() / ".claude" / "agent-library" / "evidence"
ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# The EVENT is the pass/fail signal. Bash sends no exit_code on success (2.1.272), so a null there
# means "not reported", never "passed"; a non-zero exit arrives as PostToolUseFailure instead.
WITNESS_OK = "PostToolUse"

# --- what is a validation run -------------------------------------------------------------
SEGMENT_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
# A heredoc body is data the command writes, not commands it runs: `cat > t.py <<'EOF' ... pytest ... EOF`
# must not be credited with a pytest run. Removed before the command is split into segments.
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?^\1[ \t]*$", re.DOTALL | re.MULTILINE)
ENV_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
RUNNER_PREFIX = re.compile(
    r"^(?:(?:uv|uvx|poetry|pipenv|hatch|pdm|rye)\s+(?:run\s+)?|time\s+|timeout\s+\S+\s+|exec\s+)+", re.IGNORECASE
)
PYTHON_M = re.compile(r"""^["']?\S*?python[\d.]*(?:\.exe)?["']?\s+-m\s+(\S+)(.*)$""", re.IGNORECASE)
# `python tests/test_x.py`, `python test-foo.py`, `python scripts/pycheck.py`: a test
# script or the lint hook run directly. Any other `python script.py` is not validation.
PYTHON_SCRIPT = re.compile(
    r"""^["']?\S*?python[\d.]*(?:\.exe)?["']?\s+["']?"""
    r"""(?P<script>[^\s"']*?(?:tests?[\\/][^\s"']*\.py|test[_-][^\s"']*\.py|[^\s"']*[_-]tests?\.py|pycheck\.py))"""
    r"""["']?(?P<rest>.*)$""",
    re.IGNORECASE,
)
TOOL_TOKEN = re.compile(
    r"""^["']?(?:[A-Za-z]:)?[^\s"']*?[\\/]?"""
    r"(?P<tool>pytest|py\.test|ty|ruff|mypy|pyright|tox|nox|tsc|eslint|jest|vitest|npm|pnpm|yarn|cargo|go|make|dotnet|gradlew?|mvn|pycheck\.py)"
    r"""(?:\.exe)?["']?(?=\s|$)(?P<rest>.*)$""",
    re.IGNORECASE,
)
NOT_A_RUN = re.compile(
    r"(?:^|\s)(?:--collect-only|--co|--version|-V|--help|-h|--fixtures|--markers|--list|--show-settings)(?=\s|$)"
)

# --- did a run fail ------------------------------------------------------------------------
FAIL_TEXT_RE = re.compile(
    r"(?im)^\s*(?:Exit code\s+[1-9]\d*|FAILED\b|ERROR\b|error\[|.+?:\d+:\d+: error\b)"
    r"|\b[1-9]\d*\s+(?:failed|errors?)\b"
    r"|\bFound\s+[1-9]\d*\s+errors?\b"
    r"|\bwould reformat\b|\b[1-9]\d*\s+files?\s+would be reformatted\b"
    r"|\bexit(?:\s*code)?\s*[=:]?\s*[1-9]\d*\b"
    r"|\bTraceback \(most recent call last\)"
    r"|\bInterrupted:\s+[1-9]\d*\s+errors?"
)

# --- what the final message says -------------------------------------------------------------
CLAIM_RE = re.compile(
    r"\b(?:tests?|suite|checks?|lint(?:ing)?|type[ -]?check(?:s|ing)?|typecheck|ty|ruff|pytest|mypy|"
    r"pyright|ci|build|validation)\b[^.\n]{0,40}?\b(?:pass(?:es|ed|ing)?|green|clean|succeed(?:s|ed)?|ok|\d+\s*/\s*\d+)\b"
    r"|\b(?:all|\d+)\s+tests?\s+(?:pass|passed|passing)\b"
    r"|\bno\s+(?:type\s+)?errors?\b|\b0\s+(?:errors?|failures?|failed)\b|\bno\s+failures?\b"
    r"|\bexit\s*(?:code\s*)?0\b",
    re.IGNORECASE,
)
# Honest statements that validation did not happen or did not pass.
DISCLAIMER_RE = re.compile(
    r"\b(?:not|never|didn'?t|did not|could not|couldn'?t|unable to|without)\s+"
    r"(?:run(?:ning)?|execut(?:e|ing)|verif(?:y|ied|ying)|validat(?:e|ed|ing)|test(?:ed|ing)?|check(?:ed|ing)?)\b"
    r"|\bunverified\b|\bunvalidated\b|\bnot\s+(?:run|verified|validated|tested|checked|met)\b"
    r"|\bno\s+(?:tests?|validation|checks?)\s+(?:were\s+|was\s+)?run\b|\bnothing\s+to\s+run\b"
    r"|\bno\s+tests?\s+(?:exist|to\s+run|in\s+this\s+repo)\b|\bdoc(?:umentation)?[- ]only\b"
    r"|\bvalidation\s+(?:was\s+)?skipped\b|\bskipped\s+(?:the\s+)?(?:tests?|validation)\b"
    r"|\bran\s+nothing\b|\bnothing\s+(?:was\s+)?(?:run|executed)\b",
    re.IGNORECASE,
)
REPORTS_FAILURE_RE = re.compile(
    # "no failures" / "0 failed" / "without failures" report success, not failure.
    r"(?<!\bno\s)(?<!\bno\stests\s)(?<!\bzero\s)(?<!\bwithout\s)(?<!\b0\s)"
    r"\b(?:failed|fails?|failing|failures?|not met|not checked|not run|unverified|broken|does not pass|"
    r"FAIL\b|errors?\s+(?:remain|found|reported)|[1-9]\d*\s+errors?)\b",
    re.IGNORECASE,
)
# A conditional shortly before the word makes it a plan rather than a report - "retry if it fails", "ping me
# when it fails", "it should fail loudly". A fixed-width lookbehind cannot reach past the words between, so the
# window is checked in code (the technique no-punt uses for negations).
CONDITIONAL_BEFORE = re.compile(
    r"\b(?:if|when|whenever|unless|should|in case|so that|to see whether)\b[^.\n]{0,24}$", re.IGNORECASE
)


def reports_failure(text: str) -> bool:
    """True when the text states a failure, not merely a condition under which one would occur."""
    return any(
        not CONDITIONAL_BEFORE.search(text[max(0, m.start() - 40) : m.start()])
        for m in REPORTS_FAILURE_RE.finditer(text)
    )


@dataclass
class Run:
    command: str
    kind: str
    ok: bool
    snippet: str


@dataclass
class Evidence:
    edited: list[str] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)


def classify(command: str) -> list[tuple[str, str]]:
    """Validation segments of a shell command as (segment, kind); kind in test/lint/type."""
    found: list[tuple[str, str]] = []
    for segment in SEGMENT_SPLIT.split(HEREDOC.sub(" ", command)):
        segment = segment.strip()
        if not segment:
            continue
        segment = RUNNER_PREFIX.sub("", ENV_PREFIX.sub("", segment))
        m = PYTHON_M.match(segment)
        if m:
            tool, rest = m.group(1).lower(), m.group(2)
        elif m := PYTHON_SCRIPT.match(segment):
            tool = "pycheck.py" if m.group("script").lower().endswith("pycheck.py") else "test-script"
            rest = m.group("rest")
        else:
            m = TOOL_TOKEN.match(segment)
            if not m:
                continue
            tool, rest = m.group("tool").lower(), m.group("rest")
        if NOT_A_RUN.search(rest):
            continue
        kind: str | None = None
        if tool in ("pytest", "py.test", "unittest", "tox", "nox", "jest", "vitest", "test-script"):
            kind = "test"
        elif tool == "ty":
            kind = "type" if re.match(r"\s*check\b", rest) else None
        elif tool in ("mypy", "pyright", "tsc"):
            kind = "type"
        elif tool == "ruff":
            if re.match(r"\s*check\b", rest) or (re.match(r"\s*format\b", rest) and "--check" in rest):
                kind = "lint"
        elif tool in ("eslint", "pycheck.py"):
            kind = "lint"
        elif tool in ("npm", "pnpm", "yarn"):
            kind = "test" if re.match(r"\s*(?:run\s+)?(?:test|lint|typecheck|check)\b", rest) else None
        elif tool == "cargo":
            kind = "test" if re.match(r"\s*(?:test|check|clippy)\b", rest) else None
        elif tool == "go":
            kind = "test" if re.match(r"\s*(?:test|vet)\b", rest) else None
        elif tool == "make":
            kind = "test" if re.match(r"\s*(?:test|check|lint)\b", rest) else None
        elif tool in ("dotnet", "gradle", "gradlew", "mvn"):
            kind = "test" if re.match(r"\s*test\b", rest) else None
        if kind:
            found.append((segment, kind))
    return found


def run_failed(is_error: Any, text: str) -> bool:
    return bool(is_error) or text.lstrip().startswith("Exit code") or bool(FAIL_TEXT_RE.search(text))


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict)]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def read_transcript(path: Path) -> Evidence:
    return evidence_from(iter_records(path))


def evidence_from(records: Iterable[dict[str, Any]]) -> Evidence:
    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    evidence = Evidence()
    for record in records:
        message = record.get("message")
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
                segments = classify(command)
                if not segments:
                    continue
                text = _text_of(item.get("content"))
                ok = not run_failed(item.get("is_error"), text)
                for segment, run_kind in segments:
                    evidence.runs.append(Run(segment.strip(), run_kind, ok, text.strip()[:300]))
    return evidence


def witness_path(event: dict[str, Any]) -> Path | None:
    """The witness file this stop's tool calls were appended to, or None when the payload cannot name one.

    Mirrors witness.py's layout and validates the ids the same way: a value that is not a clean identifier
    means "no file", never a sanitised guess, because these become path parts and the caller can still read
    the transcript. No `agent_id` in the payload is the lead's own calls, which witness routes to lead.jsonl.
    """
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not ID_OK.match(session_id):
        return None
    raw_agent = event.get("agent_id")
    agent_id = raw_agent if isinstance(raw_agent, str) and raw_agent else None
    if agent_id is not None and not ID_OK.match(agent_id):
        return None
    root = Path(os.environ.get(EVIDENCE_ROOT_ENV) or DEFAULT_EVIDENCE_ROOT)
    return root / session_id / (f"agent-{agent_id}.jsonl" if agent_id else "lead.jsonl")


def iter_witness(path: Path) -> Iterator[dict[str, Any]]:
    """Witness lines that parse and carry the schema keys; anything else is skipped, never fatal.

    Concurrent hook processes append to this file, so the last line can be torn: one unreadable line must
    not cost the verdict every other line supports.
    """
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or "v" not in record:
                continue
            if isinstance(record.get("event"), str) and isinstance(record.get("tool"), str):
                yield record


def evidence_from_witness(lines: Iterable[dict[str, Any]], prompt_id: str | None = None) -> Evidence:
    """Evidence from witness lines - all of them, or only one turn's when prompt_id is given.

    Pass/fail comes from the EVENT (see WITNESS_OK), then from the output: a PostToolUse line is a pass
    unless the call was interrupted or its own text reports a failure, because `pytest ... | tail` hands
    the shell the exit code of `tail`. A PostToolUseFailure line is a failed run whose text is its error.
    An edit line needs no confirming result the way a transcript `tool_use` does - the hook fires after
    the tool succeeded - so a PostToolUse edit IS a confirmed edit.
    """
    evidence = Evidence()
    seen: set[str] = set()
    for line in lines:
        if prompt_id is not None and line.get("prompt_id") != prompt_id:
            continue
        succeeded = line.get("event") == WITNESS_OK
        tool = line.get("tool")
        if tool in EDIT_TOOLS:
            target = line.get("file_path")
            if succeeded and isinstance(target, str) and target not in seen:
                seen.add(target)
                evidence.edited.append(target)
            continue
        if tool not in RUN_TOOLS:
            continue
        command = line.get("command")
        if not isinstance(command, str):
            continue
        segments = classify(command)
        if not segments:
            continue
        error = line.get("error")
        raw = line.get("text") if succeeded or not isinstance(error, str) or not error else error
        text = raw if isinstance(raw, str) else ""
        ok = succeeded and not bool(line.get("interrupted")) and not run_failed(False, text)
        for segment, run_kind in segments:
            evidence.runs.append(Run(segment.strip(), run_kind, ok, text.strip()[:300]))
    return evidence


def witness_lines(event: dict[str, Any]) -> list[dict[str, Any]] | None:
    """This stop's witness lines, or None when there is no readable file - the signal to fall back."""
    path = witness_path(event)
    if path is None or not path.is_file():
        return None
    try:
        return list(iter_witness(path))
    except OSError:
        return None


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
    project_dir = parent_path.parent
    if project_dir.is_dir():
        for candidate in project_dir.rglob(f"agent-{agent_id}.jsonl"):
            return candidate
    return None


def gather(event: dict[str, Any]) -> tuple[Evidence | None, str]:
    """A subagent's evidence: its witness file when one exists, its transcript otherwise; (None, why) = fail open.

    The fallback is silent on purpose (see the module docstring); `why` is only ever a broken-payload notice.
    """
    lines = witness_lines(event)
    if lines is not None:
        return evidence_from_witness(lines), ""
    transcript = subagent_transcript(event)
    if transcript is None:
        return None, (
            f"subagent transcript not found for agent_id={event.get('agent_id')!r}; allowing (claims unverified)."
        )
    try:
        return read_transcript(transcript), ""
    except OSError as exc:
        return None, f"cannot read {transcript} ({exc}); allowing."


def strip_negative_lines(message: str) -> str:
    return "\n".join(line for line in message.splitlines() if not (reports_failure(line) or DISCLAIMER_RE.search(line)))


def decide(message: str, evidence: Evidence) -> str | None:
    """Return the block reason, or None to allow."""
    if not evidence.edited:
        return None

    last_by_kind: dict[str, Run] = {}
    for run in evidence.runs:
        last_by_kind[run.kind] = run
    failed = [r for r in last_by_kind.values() if not r.ok]
    if failed and not reports_failure(message):
        r = failed[0]
        return (
            f"The last {r.kind} run in your transcript failed, but your message does not say so:\n"
            f"  $ {r.command}\n  {r.snippet[:200]}\n"
            "Fix and re-run, or report the failure honestly."
        )

    claims = bool(CLAIM_RE.search(strip_negative_lines(message)))
    if claims and not evidence.runs:
        return (
            "Your final message claims validation, but the transcript contains no "
            "test/typecheck/lint command. Run the project's checks now and report "
            "the exact command and result, or remove the claim and state plainly "
            "that you did not verify."
        )
    if not evidence.runs and not DISCLAIMER_RE.search(message):
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


# --- the lead's own turn ------------------------------------------------------------------


def is_user_prompt(record: dict[str, Any]) -> bool:
    message = record.get("message")
    return record.get("type") == "user" and isinstance(message, dict) and isinstance(message.get("content"), str)


def turn_records(records: list[dict[str, Any]], prompt_id: str | None) -> list[dict[str, Any]]:
    """The lead's current turn: from the first record carrying prompt_id to the next user prompt, or the end.

    Assistant records carry no promptId; everything between one prompt and the next belongs to that turn. Without
    a prompt_id, the last prompt's turn.
    """
    turn: list[dict[str, Any]] = []
    collecting = False
    for record in records:
        if prompt_id:
            pid = record.get("promptId")
            if not collecting and pid == prompt_id:
                collecting = True
            elif collecting and is_user_prompt(record) and pid not in (None, prompt_id):
                break
            if collecting:
                turn.append(record)
        elif is_user_prompt(record):
            turn = [record]
        elif turn:
            turn.append(record)
    return turn


def last_failed(runs: list[Run]) -> Run | None:
    last_by_kind: dict[str, Run] = {}
    for run in runs:
        last_by_kind[run.kind] = run
    failed = [r for r in last_by_kind.values() if not r.ok]
    return failed[0] if failed else None


def decide_lead(message: str, turn: Evidence, session: Evidence) -> str | None:
    """Block reason for the lead's final message, or None. Turn evidence for A and C, session runs for claims."""
    if turn.edited:
        failed = last_failed(turn.runs)
        if failed is not None and not reports_failure(message):
            return (
                f"The last {failed.kind} run in this turn failed, but your message does not say so:\n"
                f"  $ {failed.command}\n  {failed.snippet[:200]}\n"
                "Fix and re-run, or report the failure honestly."
            )
        if not turn.runs and not DISCLAIMER_RE.search(message):
            files = sorted(set(turn.edited))
            shown = "\n".join(f"  - {f}" for f in files[:8])
            more = f"\n  ... {len(files) - 8} more" if len(files) > 8 else ""
            return (
                f"You changed {len(files)} file(s) this turn but ran no validation:\n{shown}{more}\n"
                "Run the project's checks (or /validate) and report the command and result - or say "
                "explicitly that validation was not run and why."
            )
    if CLAIM_RE.search(strip_negative_lines(message)):
        if not session.runs:
            return (
                "Your message claims validation passed, but no test/typecheck/lint command has run in this "
                "session. Run the project's checks now (or /validate) and report the exact command and result, "
                "or remove the claim and say plainly that you did not verify."
            )
        failed = last_failed(session.runs)
        if failed is not None and not reports_failure(message):
            return (
                f"Your message claims validation passed, but the last {failed.kind} run in this session failed:\n"
                f"  $ {failed.command}\n  {failed.snippet[:200]}\n"
                "Re-run it and report the result, or report the failure honestly."
            )
    return None


def last_prompt_id(lines: list[dict[str, Any]]) -> str | None:
    """The turn a Stop payload without a `prompt_id` is about: the last one the witness file saw."""
    found: str | None = None
    for line in lines:
        pid = line.get("prompt_id")
        if isinstance(pid, str) and pid:
            found = pid
    return found


def gather_lead(event: dict[str, Any], prompt_id: str | None) -> tuple[tuple[Evidence, Evidence] | None, str]:
    """The lead's (turn, session) evidence: its witness file when one exists, the session transcript otherwise.

    Witness lines carry the `prompt_id` of the turn they happened in - the same id the Stop payload carries -
    so the turn is a filter here rather than the transcript's slice between two user prompts. Silent fallback,
    as for a subagent; `why` is only a broken-payload notice.
    """
    lines = witness_lines(event)
    if lines is not None:
        turn = evidence_from_witness(lines, prompt_id or last_prompt_id(lines))
        return (turn, evidence_from_witness(lines)), ""
    path = Path(str(event.get("transcript_path") or ""))
    if not path.is_file():
        return None, f"session transcript not found ({path}); allowing (claims unverified)."
    try:
        records = list(iter_records(path))
    except OSError as exc:
        return None, f"cannot read {path} ({exc}); allowing."
    return (evidence_from(turn_records(records, prompt_id)), evidence_from(records)), ""


def main_lead(event: dict[str, Any]) -> int:
    if event.get("stop_hook_active") is True:
        return 0
    message = event.get("last_assistant_message")
    if not isinstance(message, str):
        print("check-evidence: --lead payload lacks last_assistant_message; allowing.", file=sys.stderr)
        return 0
    prompt_id = event.get("prompt_id") if isinstance(event.get("prompt_id"), str) else None
    gathered, why = gather_lead(event, prompt_id)
    if gathered is None:
        print(f"check-evidence: {why}", file=sys.stderr)
        return 0
    turn, session = gathered
    reason = decide_lead(message, turn, session)
    if reason is None:
        return 0
    print(f"Evidence check failed (lead).\n{reason}", file=sys.stderr)
    return 2


def read_stdin_json() -> Any:
    """Bytes + utf-8-sig: PowerShell prepends a BOM when piping to a native exe."""
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read().decode("utf-8-sig", "replace") if buffer is not None else sys.stdin.read()
    return json.loads(raw.strip())


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except ValueError:
                pass
    try:
        event = read_stdin_json()
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"check-evidence: unreadable hook payload ({exc}); allowing.", file=sys.stderr)
        return 0
    if not isinstance(event, dict):
        print("check-evidence: payload was not an object; allowing.", file=sys.stderr)
        return 0
    if "--lead" in sys.argv[1:] or (event.get("hook_event_name") == "Stop" and not event.get("agent_id")):
        return main_lead(event)
    if event.get("stop_hook_active") is True:
        return 0

    agent_id = event.get("agent_id")
    message = event.get("last_assistant_message")
    missing = [k for k, v in (("agent_id", agent_id), ("last_assistant_message", message)) if not isinstance(v, str)]
    if missing:
        print(
            f"check-evidence: payload lacks {missing} (keys: {sorted(event)}); allowing (claims unverified).",
            file=sys.stderr,
        )
        return 0

    evidence, why = gather(event)
    if evidence is None:
        print(f"check-evidence: {why}", file=sys.stderr)
        return 0

    reason = decide(message, evidence)
    if reason is None:
        return 0
    print(f"Evidence check failed ({event.get('agent_type', 'subagent')}).\n{reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
