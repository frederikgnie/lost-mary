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

Quoted text is neutralised (straight or curly double quotes and backtick spans
become the word QUOTED; markdown `>` lines are dropped), so restating the rule -
never "I'll leave that for you" - is not a violation, while "I'll leave
`loader.py` for you" still is. A negated hand-back ("nothing is left for you",
"nothing for you to decide") is the opposite claim and is allowed.

Wiring: Stop (the lead) and optionally SubagentStop (implement should write
`FOUND: <path> - <one line>`, not hand back). Exit 2 blocks the stop and
returns stderr to the model; exit 0 allows. Fails OPEN, with a notice on
stderr, when the payload cannot be read or no final message can be found.

Runtime dependencies (see capabilities.md): stdin fields `stop_hook_active`,
`last_assistant_message` (preferred); otherwise the last assistant message of
`agent_transcript_path` / `transcript_path`, joining the records a streamed
message is split into (same `message.id`).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# A hand-back names the user as the one who will act. Each pattern needs the
# "you" (or a stand-in) so that "left as a follow-up in PR #12" does not match.
# "leave/left <thing> for you" accepts a pronoun, the QUOTED placeholder or a
# short noun phrase (up to four words) - but not a note or a message, and not
# "for you to inspect/review": showing something is not handing work back.
THING = (
    r"(?!(?:a|an|the|this)\s+(?:note|message|summary|link|comment|write-?up|report|list|pointer)\b)"
    r"(?:[\w'`./-]+\s+){1,4}?"
)
YOU = r"you\b(?!\s+to\s+(?:inspect|review|read|see|check|verify|compare|confirm|skim|browse)\b)"
PUNT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:i(?:'ll| will|'d| would)?\s+)?leav(?:e|ing)\s+" + THING + r"(?:for|to|up to|with)\s+" + YOU,
        r"\bleft\s+" + THING + r"(?:for|to|with)\s+" + YOU,
        r"\bfor\s+you\s+to\s+(?:fix|handle|address|resolve|decide|sort\s+out|look\s+(?:at|into)|follow\s+up(?:\s+on)?|take\s+(?:a\s+look|care\s+of))\b",
        r"\b(?:you|someone|somebody)\s+(?:may|might|should|could|will)\s+want\s+to\s+(?:fix|address|resolve|handle|follow\s+up)\b",
        r"\b(?:did\s+not|didn't|haven't|have\s+not|won't|will\s+not)\s+(?:fix|address|touch)\s+(?:that|this|it|them)\b[^.\n]{0,60}\b(?:for\s+you|yourself|on\s+your\s+(?:side|end))\b",
        r"\b(?:over|up)\s+to\s+you\s+(?:to\s+)?(?:fix|decide|handle|address)\b",
    )
)

QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`')
QUOTE_LINE = re.compile(r"(?m)^\s*>.*$")

# Only a negative subject (with its verb, if any) or a negated copula right
# before the match counts as negating the hand-back itself: "Nothing is left for
# you" and "is not left to you" are allowed, "the fix is not small so I'll leave
# that for you" is still a hand-back.
NEGATED = re.compile(
    r"(?:\b(?:nothing|none|no\s+one|nobody)\s+(?:(?:is|was|are|were|remains|gets|else)\s+)?"
    r"|\b(?:is|are|was|were)\s+not\s+|\b(?:isn't|aren't|wasn't|weren't)\s+)$",
    re.IGNORECASE,
)

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
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read() if buffer is not None else sys.stdin.read().encode("utf-8")
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
    """The text of the last assistant message, joining the records a streamed message is split into."""
    current_id: object = None
    parts: list[str] = []
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
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            # Records of one streamed message share message.id; without ids each record stands alone.
            message_id: object = message.get("id") or record.get("uuid") or object()
            if message_id != current_id:
                if any(part.strip() for part in parts):
                    found = "\n".join(parts)
                current_id, parts = message_id, []
            parts.append(text_of(message.get("content")))
    if any(part.strip() for part in parts):
        found = "\n".join(parts)
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
    """Quoted spans become the word QUOTED - still an object for "leave ... for you", never a match themselves."""
    return QUOTED.sub(" QUOTED ", QUOTE_LINE.sub(" ", text))


def punt_phrase(text: str) -> str | None:
    """The first non-negated hand-back phrase in the neutralised text, or None."""
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
    try:
        sys.exit(main())
    except Exception as exc:  # a hook fails open, loudly - never with a traceback
        notice(f"unexpected error ({exc!r}) - allowing")
        sys.exit(0)
