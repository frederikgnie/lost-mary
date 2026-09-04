#!/usr/bin/env python3
"""no-punt: Stop / SubagentStop hook that bounces a final message which hands work back to the user.

Why this exists
---------------
"Nothing is left on the table" is a rule in global-CLAUDE.md: a defect the
agent finds is fixed in place, delegated to its own `implement`, or pinned by a
failing test on a branch - and reported as fixed or in flight. Rules get
forgotten; the lead still writes "I noticed X, I'll leave that for you". This
hook reads the final message of the turn and blocks the stop once when it
contains that kind of hand-back, telling the model to close the item and
report again. One strike: Claude Code re-invokes the hook on the re-emitted
stop with `stop_hook_active: true`, and that stop is allowed, so a message that
legitimately needs the phrase goes through on the second try.

Quoted text is ignored (straight or curly double quotes, backticks, markdown
`>` lines), so restating the rule - never "I'll leave that for you" - is not a
violation.

Wiring: Stop (the lead) and optionally SubagentStop (implement should write
`FOUND: <path> - <one line>`, not hand back). Exit 2 blocks the stop and
returns stderr to the model; exit 0 allows. Fails OPEN, with a notice on
stderr, when the payload cannot be read or no final message can be found.

Runtime dependencies (see capabilities.md): stdin fields `stop_hook_active`,
`last_assistant_message` (preferred); otherwise the last `type: assistant`
record of `agent_transcript_path` / `transcript_path` is used.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# A hand-back names the user as the one who will act. Each pattern needs the
# "you" (or a stand-in) so that "left as a follow-up in PR #12" does not match.
# "leave/left <thing> for you" accepts a pronoun or a short noun phrase
# ("the flaky test", "that one"), up to four words.
THING = r"(?:[\w'`./-]+\s+){1,4}?"
PUNT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:i(?:'ll| will|'d| would)?\s+)?leav(?:e|ing)\s+" + THING + r"(?:for|to|up to|with)\s+you\b",
        r"\bleft\s+" + THING + r"(?:for|to|with)\s+you\b",
        r"\bfor\s+you\s+to\s+(?:fix|handle|address|resolve|decide|sort\s+out|look\s+(?:at|into)|follow\s+up(?:\s+on)?|take\s+(?:a\s+look|care\s+of))\b",
        r"\b(?:you|someone|somebody)\s+(?:may|might|should|could|will)\s+want\s+to\s+(?:fix|address|resolve|handle|look\s+(?:at|into)|follow\s+up|take\s+a\s+look)\b",
        r"\b(?:did\s+not|didn't|haven't|have\s+not|won't|will\s+not)\s+(?:fix|address|touch)\s+(?:that|this|it|them)\b[^.\n]{0,60}\b(?:for\s+you|yourself|on\s+your\s+(?:side|end))\b",
        r"\b(?:over|up)\s+to\s+you\s+(?:to\s+)?(?:fix|decide|handle|address)\b",
    )
)

QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`')
QUOTE_LINE = re.compile(r"(?m)^\s*>.*$")

BLOCK_HEADER = "Nothing is left on the table (no-punt)."
BLOCK_BODY = (
    "Your final message hands work back to the user: {phrase!r}. Close it instead: fix it in place "
    "when it is small; otherwise spawn an `implement` with its own OWNED (isolation: worktree) or "
    "commit a failing test on a branch. Then report it as fixed or in flight - never as left for you. "
    "If the phrase is genuinely required, re-emit the message unchanged; the second stop is allowed."
)


def notice(text: str) -> None:
    print(f"no-punt: {text}", file=sys.stderr)


def read_payload() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def last_assistant_text(transcript: Path) -> str | None:
    """The text of the last assistant record in a JSONL transcript, or None."""
    found: str | None = None
    with transcript.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"assistant"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "assistant":
                continue
            text = text_of((record.get("message") or {}).get("content"))
            if text.strip():
                found = text
    return found


def final_message(payload: dict[str, Any]) -> str | None:
    message = payload.get("last_assistant_message")
    if isinstance(message, str) and message.strip():
        return message
    for key in ("agent_transcript_path", "transcript_path"):
        path = payload.get(key)
        if isinstance(path, str) and path:
            transcript = Path(path)
            if transcript.is_file():
                try:
                    return last_assistant_text(transcript)
                except OSError as exc:
                    notice(f"cannot read {transcript} ({exc})")
                    return None
    return None


def strip_quoted(text: str) -> str:
    return QUOTED.sub(" ", QUOTE_LINE.sub(" ", text))


# "Nothing is left for you", "nothing for you to decide": a negated hand-back is
# the opposite claim. Look a few words back from the match for the negation.
NEGATED = re.compile(
    r"\b(?:nothing|no|not|never|without|isn't|aren't|wasn't|is not|are not)\s+(?:[\w'-]+\s+){0,3}$", re.IGNORECASE
)


def punt_phrase(text: str) -> str | None:
    """The first non-negated hand-back phrase in the unquoted text, or None."""
    clean = strip_quoted(text)
    for pattern in PUNT_PATTERNS:
        for match in pattern.finditer(clean):
            if NEGATED.search(clean[max(0, match.start() - 48) : match.start()]):
                continue
            return " ".join(match.group(0).split())
    return None


def main() -> int:
    payload = read_payload()
    if payload is None:
        notice("unreadable payload - allowing")
        return 0
    if payload.get("stop_hook_active") is True:
        return 0
    message = final_message(payload)
    if message is None:
        notice("no final message in payload or transcript - allowing")
        return 0
    phrase = punt_phrase(message)
    if phrase is None:
        return 0
    print(f"{BLOCK_HEADER}\n{BLOCK_BODY.format(phrase=phrase)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
