#!/usr/bin/env python3
"""witness: one JSON line per tool call, so evidence does not have to be read out of a transcript.

Why this exists
---------------
`check-evidence.py` and `ledger.py` answer "what did this agent actually run
and edit?" by parsing the session transcript JSONL. Claude Code's own
documentation calls that file internal and version-unstable, and says it is
"written asynchronously" so it "may not yet include the current turn's most
recent messages when a hook fires" - a Stop hook can therefore judge a turn
whose last tool call has not landed in the file yet. This hook is the supported
producer of the same facts: Claude Code hands it every tool call as the call
completes, and it appends one self-describing line to a file this library owns.

Classification does NOT happen here. What counts as a validation run, what
counts as an edit, which runs belong to which turn - all of that is judgment
that changes, and it belongs to the consumer, at read time. A line written here
is a fact: which tool, in which agent, with which command or file, how it
exited, and its output. So this script imports neither `check-evidence` nor
`ledger`, and the line format below is the whole contract between them.

The line (every key always present, schema `v: 1`):

  v, t (ISO-8601 UTC, seconds), event ("PostToolUse" | "PostToolUseFailure"),
  session_id, prompt_id, agent_id, agent_type, tool_use_id, tool,
  command (Bash/PowerShell `tool_input.command`, else null),
  file_path (edit tools' target, else `tool_response.filePath`, else null),
  exit_code (int or null), interrupted (bool),
  text (the output, bounded head + tail), error (failure event only, or null).

Free text is bounded to a head and a TAIL - a test summary sits at the end of a
run's output - and the whole line is kept under 4000 bytes. `command` is NOT
pre-bounded: the consumer decides from it whether a validation run happened, and
an elided middle would hide the `&& pytest tests -q` of a long compound, turning
a run that happened into "ran nothing". It is shrunk only when the whole line
would otherwise exceed the cap, which a normal line never reaches.

Where. `<EVIDENCE_ROOT>/<session_id>/witness-<agent_id>.jsonl`, or
`witness-lead.jsonl` when the payload carries no `agent_id` (the lead's own
calls). The `witness-` prefix is not decoration: Claude Code names a subagent's
own transcript `agent-<agent_id>.jsonl`, and a consumer searching a project tree
for that must not find one of these instead. EVIDENCE_ROOT defaults to
`~/.claude/agent-library/evidence`; the tests override it. Outside any work tree
on purpose, and never under `~/.claude/projects`, where the transcripts live -
compared on RESOLVED paths, so a `..` segment, a relative root, a symlink or a
Windows 8.3 short name (`C:/Users/FREDER~1/...`) cannot slip past the refusal.
`session_id` and `agent_id` become path parts, so both are matched against a
strict identifier pattern first: a value that fails is a refusal to write, never
a sanitised guess. Creating a session directory prunes sibling directories older
than EVIDENCE_KEEP_DAYS (default 14, minimum 1), and only those that are
provably this script's own output (see `prune`) - evidence only has to outlive
the stop that reads it.

Runtime dependencies (measured 2026-09-15, Claude Code 2.1.272, with probe
hooks; see capabilities.md). `PostToolUse` fires inside subagents and carries
`agent_id` (stable across one subagent's calls) and `agent_type`; on the lead's
own calls both are absent and `session_id` / `prompt_id` are what identify the
turn. `tool_response` for Bash is `{stdout, stderr, interrupted, isImage,
noOutputExpected}` - it carries NO `exit_code`, although the docs claim one, so
`exit_code` is read when present and never required. A non-zero exit does not
arrive as `PostToolUse` at all: it arrives as `PostToolUseFailure`, with no
`tool_response` and an `error` string whose first line is "Exit code N" and
whose remaining lines are the command's output. `Write` answers with
`{type, filePath, content, originalFile, structuredPatch, userModified}` - the
file content is deliberately not recorded; only `filePath` is. A compound
command that swallows its own exit code (`cmd; echo EXIT=$?`) exits 0 and
arrives as `PostToolUse`, so it is recorded as a success: the same blind spot
the transcript has, neither fixed nor worsened here.

How it degrades. Any failure to read, decode, validate or write prints one line
on stderr and exits 0: a hook that wedges every tool call is worse than one
that misses a line. The line is appended in a single `write()` on a file opened
"ab", so a torn write can only leave a partial line at the end of the file,
never interleave two - and the next append glues onto it, which the reader
undoes by re-parsing from the last `{"v":` on the line (see `iter_witness` in
check-evidence.py): the torn line is lost, the one after it is not. Tools other
than the recorded set are ignored in silence (the matcher should already
exclude them). Nothing is ever printed on stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
EVIDENCE_ROOT_ENV = "EVIDENCE_ROOT"
DEFAULT_ROOT = Path.home() / ".claude" / "agent-library" / "evidence"
KEEP_DAYS_ENV = "EVIDENCE_KEEP_DAYS"
KEEP_DAYS_DEFAULT = 14
# Transcripts live here and an auto-mode rule blocks writes near them: never our target, whatever EVIDENCE_ROOT says.
FORBIDDEN_ROOT = Path.home() / ".claude" / "projects"
# Ids become path parts. Strict on purpose: no separators, no dots, so "../evil" cannot be a session.
ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# The lead's own calls carry no agent_id and land here. Runtime agent ids are hex, so nothing collides with "lead".
LEAD_FILE = "witness-lead.jsonl"
# A file name only this script writes - LEAD_FILE included. prune deletes nothing whose name fails this.
WITNESS_FILE = re.compile(r"^witness-[A-Za-z0-9_-]{1,64}\.jsonl$")
EXIT_CODE = re.compile(r"^Exit code (\d+)")
RECORDED_TOOLS = frozenset({"Bash", "PowerShell", "Edit", "Write", "MultiEdit", "NotebookEdit"})
SUCCESS_EVENT = "PostToolUse"
FAILURE_EVENT = "PostToolUseFailure"
TEXT_HEAD, TEXT_TAIL = 1000, 800  # keep the tail: a test summary is at the end of the output
PATH_HEAD, PATH_TAIL = 400, 200
ID_LIMIT = 120
MAX_LINE = 4000


def notice(why: str) -> None:
    print(f"witness: {why}; nothing recorded", file=sys.stderr)


def read_payload() -> dict[str, Any] | None:
    """The hook payload from stdin as bytes, decoded utf-8-sig - a PowerShell pipe prepends a BOM."""
    buffer = getattr(sys.stdin, "buffer", None)
    try:
        raw = buffer.read() if buffer is not None else sys.stdin.read().encode("utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def as_text(value: Any) -> str | None:
    """A non-empty payload string, or None - every optional string field goes through here."""
    return value if isinstance(value, str) and value else None


def as_mapping(value: Any) -> dict[str, Any]:
    """A payload object as a dict; anything else reads as empty, so callers never branch on the shape."""
    return value if isinstance(value, dict) else {}


def as_optional_mapping(value: Any) -> dict[str, Any] | None:
    """Like as_mapping, but "absent" stays distinguishable from "present and empty" (a Failure has no response)."""
    return value if isinstance(value, dict) else None


def clip(value: str, limit: int) -> str:
    return value[:limit]


def bound(value: str, head: int, tail: int) -> str:
    """Head and tail, never the middle: the start says what ran, the end says how it went."""
    if len(value) <= head + tail + 5:
        return value
    return value[:head] + " ... " + value[-tail:]


def event_of(payload: dict[str, Any]) -> str:
    """The event name, trusted when it is one of the two, otherwise inferred from the failure payload's `error`."""
    name = payload.get("hook_event_name")
    if name in (SUCCESS_EVENT, FAILURE_EVENT):
        return str(name)
    return FAILURE_EVENT if isinstance(payload.get("error"), str) else SUCCESS_EVENT


def exit_code_of(response: dict[str, Any] | None, error: str | None) -> int | None:
    """`tool_response.exit_code` when the runtime supplies one (Bash does not), else "Exit code N" from `error`."""
    if response is not None:
        code = response.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool):
            return code
        if isinstance(code, str) and code.strip().lstrip("-").isdigit():
            return int(code.strip())
    if error:
        match = EXIT_CODE.match(error.lstrip())
        if match:
            return int(match.group(1))
    return None


def output_of(payload: dict[str, Any], response: dict[str, Any] | None, error: str | None) -> str:
    """The call's output: the failure event's `error`, else stdout and stderr joined, else nothing.

    A structured response that is not a shell result (Write answers with the whole file content) is
    deliberately NOT serialised into the line - only the fields named in the schema are recorded.
    """
    if error is not None:
        return error
    raw = payload.get("tool_response")
    if isinstance(raw, str):
        return raw
    if response is None:
        return ""
    parts = [str(response.get(key)) for key in ("stdout", "stderr") if as_text(response.get(key))]
    return "\n".join(parts)


def encode(record: dict[str, Any]) -> bytes:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace") + b"\n"


def fit(record: dict[str, Any]) -> bytes | None:
    """One line under MAX_LINE bytes, shrinking the free-text fields in order; None when even that is not enough."""
    line = encode(record)
    for key in ("text", "error", "command", "file_path"):
        if len(line) <= MAX_LINE:
            return line
        value = record.get(key)
        while len(line) > MAX_LINE and isinstance(value, str) and len(value) > 40:
            value = bound(value, max(20, len(value) // 4), max(20, len(value) // 8))
            record[key] = value
            line = encode(record)
    return line if len(line) <= MAX_LINE else None


def build_line(payload: dict[str, Any], tool: str, session_id: str, agent_id: str | None) -> bytes | None:
    event = event_of(payload)
    tool_input = as_mapping(payload.get("tool_input"))
    response = as_optional_mapping(payload.get("tool_response"))
    error = as_text(payload.get("error")) if event == FAILURE_EVENT else None
    command = as_text(tool_input.get("command"))
    # NotebookEdit names its target `notebook_path`; a Write answers with `filePath` even when the input lacked one.
    file_path = as_text(tool_input.get("file_path")) or as_text(tool_input.get("notebook_path"))
    if file_path is None and response is not None:
        file_path = as_text(response.get("filePath"))
    interrupted = bool(response.get("interrupted")) if response is not None else False
    record: dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "t": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "session_id": session_id,
        "prompt_id": clip(as_text(payload.get("prompt_id")) or "", ID_LIMIT) or None,
        "agent_id": agent_id,
        "agent_type": clip(as_text(payload.get("agent_type")) or "", ID_LIMIT) or None,
        "tool_use_id": clip(as_text(payload.get("tool_use_id")) or "", ID_LIMIT),
        "tool": clip(tool, 60),
        # NOT bounded here: the consumer classifies this string, and `&& pytest -q` elided out of the middle of a
        # long compound reads as "ran no validation". fit() shrinks it only if the whole line busts MAX_LINE.
        "command": command,
        "file_path": bound(file_path, PATH_HEAD, PATH_TAIL) if file_path is not None else None,
        "exit_code": exit_code_of(response, error),
        "interrupted": interrupted or payload.get("is_interrupt") is True,
        "text": bound(output_of(payload, response, error), TEXT_HEAD, TEXT_TAIL),
        "error": bound(error, TEXT_HEAD, TEXT_TAIL) if error is not None else None,
    }
    return fit(record)


def evidence_path(session_id: str, agent_id: str | None) -> tuple[Path | None, str]:
    """The file this call belongs in, or (None, why) - the ids are validated before they become path parts."""
    if not ID_OK.match(session_id):
        return None, f"unusable session_id {session_id!r}"
    if agent_id is not None and not ID_OK.match(agent_id):
        return None, f"unusable agent_id {agent_id!r}"
    root = Path(os.environ.get(EVIDENCE_ROOT_ENV) or DEFAULT_ROOT)
    directory = root / session_id
    # Resolved, not as written: `~/.claude/x/../projects`, a relative EVIDENCE_ROOT, a symlink and the Windows
    # 8.3 short name of the home directory all compare unequal to the spelled-out path and would walk straight in.
    resolved = directory.resolve(strict=False)
    forbidden = FORBIDDEN_ROOT.resolve(strict=False)
    if resolved == forbidden or resolved.is_relative_to(forbidden):
        return None, "refusing to write under ~/.claude/projects"
    return directory / (f"witness-{agent_id}.jsonl" if agent_id else LEAD_FILE), ""


def keep_days() -> int:
    try:
        return max(1, int(os.environ.get(KEEP_DAYS_ENV) or KEEP_DAYS_DEFAULT))
    except ValueError:
        return KEEP_DAYS_DEFAULT


def newest_witness_mtime(directory: Path) -> float | None:
    """How old this directory's evidence is, or None when the directory is not provably ours.

    "Provably ours" is the whole safety property of `prune`: the name is a session id, the directory lies
    where it is spelled (not a link into someone else's tree), and EVERY child is a regular, non-symlink file
    named the way this script names its files. One foreign file, one subdirectory or one symlink and the
    answer is None - the caller then leaves the whole directory alone rather than emptying it. EVIDENCE_ROOT
    is operator-set and has been pointed at a source tree by mistake; deleting what we did not write is the
    one failure this hook must not have. The link check is `resolve()`-based rather than `is_symlink()`,
    because a Windows junction - which anyone can create without a privilege - is not reported as a symlink.

    The age is the NEWEST child's mtime, never the directory's: a directory's mtime moves when a file is added
    or removed and NOT when one is appended to, so a long-lived session that is still being written would look
    stale by its directory and have its live evidence unlinked under the reader (on POSIX the unlink would race
    a concurrent `open("ab")`). An empty directory has no child to date, so its own mtime is the best there is.
    """
    if directory.is_symlink() or not ID_OK.match(directory.name):
        return None
    try:
        if directory.resolve(strict=False) != directory.parent.resolve(strict=False) / directory.name:
            return None  # a link, a junction or a reparse point: its contents belong to somewhere else
        newest: float | None = None
        for child in directory.iterdir():
            if child.is_symlink() or not WITNESS_FILE.match(child.name) or not child.is_file():
                return None
            mtime = child.stat().st_mtime
            newest = mtime if newest is None else max(newest, mtime)
        return directory.stat().st_mtime if newest is None else newest
    except OSError:
        return None


def prune(root: Path, keep: Path) -> None:
    """Drop session directories whose newest witness line is older than EVIDENCE_KEEP_DAYS.

    Only directories that are provably this script's output are touched at all (see `newest_witness_mtime`);
    anything else - a subdirectory, a foreign file, a link, a name that is not a session id - is skipped
    whole, with not one file inside it unlinked.
    """
    cutoff = datetime.now(UTC).timestamp() - keep_days() * 86400
    try:
        siblings = list(root.iterdir())
    except OSError:
        return
    for directory in siblings:
        if directory == keep or directory.is_symlink() or not directory.is_dir():
            continue
        age = newest_witness_mtime(directory)
        if age is None or age >= cutoff:
            continue
        try:
            for child in directory.iterdir():
                if WITNESS_FILE.match(child.name) and not child.is_symlink():  # re-checked: the scan is not atomic
                    child.unlink()
            directory.rmdir()  # fails, harmlessly, if anything appeared in the directory meanwhile
        except OSError:
            continue


def ensure_session_dir(directory: Path) -> None:
    """Create the session directory, pruning stale siblings the once - the only moment a sweep is worth its cost."""
    if directory.is_dir():
        return
    directory.mkdir(parents=True, exist_ok=True)
    prune(directory.parent, directory)


def main() -> int:
    payload = read_payload()
    if payload is None:
        notice("unreadable payload")
        return 0
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or tool not in RECORDED_TOOLS:
        return 0  # belt and braces behind the matcher: not evidence, not a problem, nothing to say
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        notice("payload has no session_id")
        return 0
    agent_id = as_text(payload.get("agent_id"))
    path, why = evidence_path(session_id, agent_id)
    if path is None:
        notice(why)
        return 0
    line = build_line(payload, tool, session_id, agent_id)
    if line is None:
        notice(f"{tool} record does not fit one bounded line")
        return 0
    try:
        ensure_session_dir(path.parent)
        with path.open("ab") as handle:
            handle.write(line)  # one call: a torn write leaves a partial line at the end, never two interleaved
    except OSError as exc:
        notice(f"cannot append to {path} ({exc})")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except ValueError:
                pass
    try:
        sys.exit(main())
    except Exception as exc:  # a hook fails open, loudly - never with a traceback
        notice(f"unexpected error ({exc!r})")
        sys.exit(0)
