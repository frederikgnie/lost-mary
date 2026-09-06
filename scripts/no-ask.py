#!/usr/bin/env python3
"""no-ask: PreToolUse hook that blocks AskUserQuestion while a session runs under /lost-mary.

Why this exists
---------------
"Decide, do not ask" is a rule in skills/lost-mary/SKILL.md; rules get
forgotten - the lead still pops a menu whose first option it already prefers.
A permission deny would enforce it, but permission rules are global or per
repository, and the user wants the menus available outside the procedure. So
this hook reads the session transcript: if `/lost-mary` has been invoked since
the last `/clear`, the AskUserQuestion call is blocked and the model is told to
decide, record an `Assumption:` line and continue - or, for a genuinely
irreversible choice, to ask in plain text, which still works.

Wiring: PreToolUse with matcher "AskUserQuestion". Exit 2 blocks the call and
returns stderr to the model; exit 0 allows. Fails OPEN, with a notice on
stderr, when the payload or transcript cannot be read.

Runtime dependencies (see capabilities.md): PreToolUse stdin fields
`tool_name`, `transcript_path`; a slash-command turn is a `type: user` record
whose `message.content` string starts with `<command-name>/<name></command-name>`
(observed on 2.1.260, not documented).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOL = "AskUserQuestion"
ENTER = "<command-name>/lost-mary</command-name>"
RESET = "<command-name>/clear</command-name>"

BLOCK_MESSAGE = (
    "AskUserQuestion is blocked while this session runs under /lost-mary. "
    "Decide: take the option you would have marked recommended, record it as an "
    "`Assumption:` line in your reply, and continue. Only if the choice is irreversible "
    "(production, money, data loss, secrets, history rewrites) or needs data only the user "
    "holds, ask in plain text instead - and keep working on everything that does not depend on it."
)


def notice(text: str) -> None:
    print(f"no-ask: {text}", file=sys.stderr)


def read_payload() -> dict[str, Any] | None:
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read() if buffer is not None else sys.stdin.read().encode("utf-8")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def command_of(line: str) -> str | None:
    """Which of the two commands we track a transcript line invokes, if any."""
    if "<command-name>" not in line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or record.get("type") != "user":
        return None
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return None
    # A slash-command turn starts with the command tags; a built-in puts <command-name> first, a skill puts
    # <command-message> first (observed 2.1.260). Only the tag block at the head counts - a tag quoted later in
    # ordinary prose is not an invocation.
    head = content.lstrip()
    if not head.startswith("<command-"):
        return None
    head = head[:400]
    if ENTER in head:
        return "lost-mary"
    if RESET in head:
        return "clear"
    return None


def under_lost_mary(transcript: Path) -> bool:
    """True when /lost-mary has been invoked since the last /clear."""
    active = False
    with transcript.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match command_of(line):
                case "lost-mary":
                    active = True
                case "clear":
                    active = False
                case _:
                    pass
    return active


def main() -> int:
    payload = read_payload()
    if payload is None:
        notice("unreadable payload - allowing")
        return 0
    if payload.get("tool_name") != TOOL:
        return 0
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        notice("no transcript_path in payload - allowing")
        return 0
    transcript = Path(transcript_path)
    if not transcript.is_file():
        notice(f"transcript not found: {transcript} - allowing")
        return 0
    try:
        active = under_lost_mary(transcript)
    except OSError as exc:
        notice(f"cannot read transcript ({exc}) - allowing")
        return 0
    if not active:
        return 0
    print(BLOCK_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a hook fails open, loudly - never with a traceback
        notice(f"unexpected error ({exc!r}) - allowing")
        sys.exit(0)
