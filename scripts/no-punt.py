#!/usr/bin/env python3
"""no-punt: Stop / SubagentStop hook that bounces a final message which hands the turn back to the user.

Why this exists
---------------
Two rules in global-CLAUDE.md say when a turn is over: "Nothing is left on the
table" (a defect the agent finds is fixed in place, delegated to its own
`implement`, or pinned by a failing test - never "I'll leave that for you") and
"Decide, do not ask" (the option you would mark recommended is the decision).
Rules get forgotten. Measured over every lead transcript on this machine since
2026-09-01: of 1,646 turn-ending messages, 279 stopped to ask for a go-ahead -
"Say the word and I'll...", "Want me to...?", "Standing by", "your call", a
closing menu question - and the user's replies were "then do it?", "why do you
ask for my permission?", "go". The first version of this hook knew only the
first rule's phrasing and had bounced 5 of them.

What it does
------------
Reads the final message of the turn and blocks the stop when the message

  parked     hands a defect to the user - "I'll leave that for you", "left
             staging to you", "you may want to fix" - anywhere in the message;
  go-ahead   stops for the user's word in its closing paragraphs - "say the
             word", "want me to", "if you'd like", "standing by", "your call",
             "let me know", "not going to ... unprompted";
  remaining  closes on a label that lists work as still open - "Remaining:",
             "Next steps:", "Pending:" - a queue presented as a report;
  question   ends on a question, which at the end of a turn is asked of the
             user - "Which first?", "Commit it or bin it?";
  unclosed   under /lost-mary only: a turn that edited, ran or delegated
             something and ends without a closing line. The procedure ends a
             turn in one of three states, each an explicit, auditable line -
             `DONE: <the check re-run and what it showed>`, `IN FLIGHT: <the
             named agent or branch that carries the rest>`, `BLOCKED: <what
             only the user can supply, or the irreversible action>`. Requiring
             the line is what the working designs share (a completion token,
             not phrase matching): a message that merely lists leftovers cannot
             pass by rewording.

`BLOCKED:` always ends the turn - it is the lead's twin of a subagent's
`MISSING: <field>` - and friction.py counts such stops, and what else they
carried, so they can be audited. The bounce quotes the phrase, restates the
rule, names the way out and carries back the turn's own task and its `Done
means` line, so the model re-reads the whole ask rather than the last item.

Loop guard, from the transcript rather than a state file: this hook's own
feedback records in the current turn (the records carrying the payload's
`prompt_id`; when that matches nothing, or without one, since the last real
prompt). Two bounces in a row with no tool call between them mean the model
only rephrased - the third stop is allowed, with a notice; with work in between
it is bounced again, up to six in one turn (Claude Code's own override is eight
without progress, and every bounce re-bills the whole context). When no turn can
be counted at all - no transcript, an unreadable one, one without a single
prompt - `stop_hook_active` keeps the old one-strike rule: the re-emitted stop
is allowed.

Quoted text is neutralised (double quotes, backtick spans and fenced blocks
become the word QUOTED; markdown `>` lines are dropped), so restating a rule -
never "I'll leave that for you" - is not a violation, while "I'll leave
`loader.py` for you" still is. A negated phrase ("nothing is left for you",
"without waiting for your go-ahead") is the opposite claim and is allowed.

Wiring: Stop (the lead). SubagentStop only with a matcher on roles that report
rather than plan - an `explore` brief legitimately ends on a `Next steps:`
section, which is its deliverable, not a hand-back. Exit 2 blocks the stop and
returns stderr to the model; exit 0 allows. Fails OPEN, with a notice on stderr,
when the payload cannot be read or no final message can be found.

Runtime dependencies (see capabilities.md): stdin fields `last_assistant_message`
(preferred), `prompt_id`, `stop_hook_active`, `agent_id` (present on a
subagent); the transcript at `agent_transcript_path` / `transcript_path` for
the fallback message and for the turn's records - `promptId` on user records
(a Stop block's feedback record carries the same one as its prompt, observed
2026-09-17), a Stop block recorded as a `user` record whose string content
starts `Stop hook feedback:\\n[<hook command>]: ` and carries this hook's own
header, and a slash command recorded as a `user` record whose string content
starts with the command tags.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- parked defects ---------------------------------------------------------------------------
# A hand-back names the user as the one who will act. Each pattern needs the "you" (or a stand-in)
# so that "left as a follow-up in PR #12" does not match. "leave/left <thing> for you" accepts a
# pronoun, the QUOTED placeholder or a short noun phrase (up to six words, ticket numbers
# included) - but not a note or a message, and not "for you to inspect/review": showing something
# is not handing work back.
THING = (
    r"(?!(?:a|an|the|this)\s+(?:note|message|summary|link|comment|write-?up|report|list|pointer)\b)"
    r"(?:[\w'`./#:,()-]+\s+){1,6}?"
)
YOU = r"you\b(?!\s+to\s+(?:inspect|review|read|see|check|verify|compare|confirm|skim|browse)\b)"
PUNT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:i(?:'ll| will|'d| would)?\s+)?leav(?:e|ing)\s+" + THING + r"(?:for|to|up to|with)\s+" + YOU,
        r"\bleft\s+" + THING + r"(?:for|to|with)\s+" + YOU,
        r"\bfor\s+you\s+to\s+(?:fix|handle|address|resolve|decide|sort\s+out|look\s+(?:at|into)|follow\s+up(?:\s+on)?|take\s+(?:a\s+look|care\s+of))\b",
        r"\b(?:you|someone|somebody)\s+(?:may|might|should|could|will)\s+want\s+to\s+(?:fix|address|resolve|handle|follow\s+up)\b",
        r"\b(?:did\s+not|didn't|haven't|have\s+not|won't|will\s+not)\s+(?:fix|address|touch|start|open|commit|push|merge|run|delete)\s+(?:that|this|it|them)\b[^.\n]{0,60}\b(?:for\s+you|yourself|on\s+your\s+(?:side|end))\b",
        r"\b(?:over|up)\s+to\s+you\s+(?:to\s+)?(?:fix|decide|handle|address)\b",
    )
)

# --- go-ahead requests ------------------------------------------------------------------------
# Judged on the closing paragraphs only: an offer in the middle of a report is narrative, one at
# the end is where the turn stops. Every shape here was read off real transcripts; every exclusion
# is a real closing sentence that is not a hand-back ("Kept your choice of Europe/Berlin", "the
# tests say so", "standing by the decision", "ready for your review").
IMPERATIVE = r"(?:^|[.,;:!?-]\s+|\b(?:please|just|and|or|so|then)\s+)"
GO_AHEAD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE | re.MULTILINE)
    for p in (
        # the user saying: "say the word", ", say so and I'll", "until you say go" - not "the tests say so"
        r"(?<!I'll )(?<!I will )(?<!will )(?<!to )(?<!would )(?<!should )(?<!can )(?<!could )(?<!might )(?<!'d )"
        r"\bsay\s+the\s+word\b",
        IMPERATIVE + r"say\s+(?:go|so|yes|which|when|merge|push|the\s+go)\b",
        r"\byou\s+say\s+(?:go|so|yes|which|when|merge|push|the\s+word)\b",
        r"\b(?:give\s+me\s+(?:the\s+|a\s+)?(?:go|go-?ahead|green\s+light|nod|word)|with\s+one\s+word|one\s+word\s+from\s+you|your\s+say-?so)\b",
        r"\bif\s+you(?:'d|\s+would)?\s+(?:like|want|prefer|wish)\b"
        r"(?!\s+(?:the|a|an|more|details|to\s+(?:run|read|see|check|look|verify|inspect|reproduce|compare|try))\b)",
        r"\b(?:do\s+you\s+)?want\s+me\s+to\b|\bwould\s+you\s+(?:like|prefer|rather)\b|\bdo\s+you\s+(?:want|prefer)\b",
        r"\b(?:shall|should|may|can|could)\s+I\b[^.\n?]{0,100}\?",
        r"\blet\s+me\s+know\b",
        IMPERATIVE + r"tell\s+me\s+(?:if|when|whether|which|what|where|how)\b",
        r"\bstanding\s+by\b(?!\s+(?:the|my|that|this|our|its|what|it)\b)",
        r"\b(?:await(?:ing)?|wait(?:ing)?\s+(?:for|on))\s+(?:you\b|the\s+user\b"
        r"|your\s+(?:call|word|go-?ahead|go|confirmation|approval|decision|directions?|instructions?|input"
        r"|say-?so|reply|answer|nod|signal|okay|ok|green\s+light|sign-?off)\b"
        r"|(?:the|a)\s+(?:go-?ahead|green\s+light|nod|sign-?off|word\s+from\s+you)\b)",
        r"\b(?:your|the\s+user's)\s+(?:call|decision|choice|go-?ahead|confirmation|approval|sign-?off|nod|permission|blessing)\b"
        r"(?!\s+(?:of|to|rules?|model|list|from|in|is|was|were|has|had|for|on|at|that|which)\b)",
        r"\b(?:up|over|down)\s+to\s+you\b|\byours\s+to\s+(?:decide|release|make|call|run|delete|merge|trigger|start|approve|pull|push|choose)\b",
        r"\b(?:when|once|until|after)\s+you(?:'re|\s+are)?\s+(?:ready|back|happy|sure|confirm|say|approve|decide|okay|ok|agree)\b",
        r"\bhold(?:ing)?\s+off\b[^.\n]{0,60}\b(?:you|your|word|go-?ahead|confirm)\b"
        r"|\bI(?:'d|\s+would)\s+rather\s+you\s+(?:confirm|decide|check|say|tell|choose|approve|run|look|verify|pick|call|do|test|try)\b",
        r"\b(?:not\s+going\s+to|won't|will\s+not|shouldn't|should\s+not|rather\s+not|don't\s+want\s+to|not\s+about\s+to|refuse\s+to|hesitate\s+to)\b"
        r"[^.\n]{0,60}\b(?:unprompted|off\s+my\s+own\s+bat|on\s+my\s+own\s+(?:authority|initiative))\b",
        r"\bready\s+(?:to\s+\w+\s+)?(?:when|once|whenever)\s+you\b|\bready\s+for\s+your\b(?!\s+(?:review|inspection|eyes|reading)\b)",
    )
)

# A closing label that presents leftovers as a report. The text after the colon (or on the next
# line) is captured so that "Remaining: none" is not one; "Remaining risk:" is this repository's
# own idiom for what could not be verified, not a queue.
REMAINING_LABEL = re.compile(
    r"(?im)^[ \t*#>_-]*(?:remaining|left-?overs?|pending|deferred|parked|backlog"
    r"|still\s+(?:to\s+do|open|outstanding|left)|left\s+to\s+do|next\s+steps?|to-?dos?|follow-?ups?|open\s+items?"
    r"|not\s+(?:yet\s+)?done|outstanding|unfinished)\b"
    r"(?!\s+(?:risks?|budget|balance|time|effort|cost|capacity|questions?|context|tokens|allowance|credit|debt)\b)"
    r"[^\n:]{0,20}:[ \t]*(.*)$"
)
NOTHING = re.compile(r"^\W*(?:none|nothing|no\b|n/a|nil|empty|0\b|-\s*$)", re.IGNORECASE)

QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`')
FENCED = re.compile(r"```.*?```", re.DOTALL)
QUOTE_LINE = re.compile(r"(?m)^\s*>.*$")
PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
CLOSING_PARAGRAPHS = 3

# A negative subject or "without / rather than / instead of" up to three words before the match, or
# a negated verb right before it, negates the hand-back itself: "Nothing is left for you", "without
# waiting for your go-ahead", "will not wait for your call" are allowed; "the fix is not small so
# I'll leave that for you" is still a hand-back.
NEGATED = re.compile(
    r"(?:\b(?:nothing|none|no\s+one|nobody|without|rather\s+than|instead\s+of)\s+(?:[\w'-]+\s+){0,3}"
    r"|\b(?:is|are|was|were|will|would|do|did|does|need)\s+not\s+(?:[\w'-]+\s+)?"
    r"|\b(?:isn't|aren't|wasn't|weren't|won't|don't|didn't|doesn't|needn't)\s+(?:[\w'-]+\s+)?)$",
    re.IGNORECASE,
)

# The closing lines: a line that starts with the token, optionally as a bullet, in bold or in backticks,
# with something after the colon - a bare `DONE:` closes nothing (seen live 2026-09-17). Upper case
# only - `blocked: none` or `done: 3 of 5` in a status list is not a declaration.
CLOSING_LINE = re.compile(r"(?m)^\s*(?:[-*]\s+)?[`*]*(DONE|IN[ -]FLIGHT|BLOCKED)[`*]*\s*:[`*]*(?:[ 	]+\S|[^\s`*])")
HOOK_FEEDBACK = re.compile(r"^(\w+) hook feedback:\n\[(.*?)\]: ", re.DOTALL)
OWN_HEADER = "(no-punt)."  # every bounce this hook writes carries it, whatever the wiring calls the script
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
TAG_BLOCK = re.compile(r"<(system-reminder|ide_selection|command-message|command-name)>.*?</\1>", re.DOTALL)
DONE_MEANS = re.compile(r"(?im)^[ \t*#>_-]*done\s+means\b[^\w\n]*(.+?)\s*$")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
ENTER = "<command-name>/lost-mary</command-name>"
RESET = "<command-name>/clear</command-name>"
# Tools whose use makes a turn "work" that must close on DONE / IN FLIGHT / BLOCKED; a turn that only
# read files to answer a question may end however it likes.
WORK_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "Agent"})

IDLE_CAP = 2  # bounces in a row with no tool call after them: the model only rephrased
TURN_CAP = 6  # bounces in one turn, progress or not; Claude Code overrides at eight
TASK_CHARS = 240

PARKED_HEADER = "Nothing is left on the table (no-punt)."
PARKED_BODY = (
    "Your final message hands work back to the user: {phrase!r}. Close it instead: fix it in place "
    "when it is small; otherwise spawn an `implement` with its own OWNED (isolation: worktree) or "
    "commit a failing test on a branch. Then report it as fixed or in flight - never as left for you. "
    "If it genuinely cannot be yours (an irreversible action, or data only the user holds), end with one "
    "line `BLOCKED: <what and why>`; that stop is allowed."
)
OPEN_HEADER = "The turn is not over (no-punt)."
GO_AHEAD_BODY = (
    "Your final message stops to ask for a go-ahead: {phrase!r}. Nobody is answering - the turn is yours "
    "until the task is done. Take the option you would recommend, state it in one line as `Assumption:`, "
    "and do it now; then the next item, until the list is empty. Look things up before asking anything: "
    "this repository, its docs, the web. Stop early only for an irreversible action (production, money, "
    "data loss, secrets, history rewrite) or for data only the user holds - then end with one line "
    "`BLOCKED: <what you need and why nothing here can supply it>`; that stop is allowed."
)
REMAINING_BODY = (
    "Your final message lists work as still open: {phrase!r}. A list of leftovers is a queue, not a report: "
    "do the remaining items now, or hand each to its own `implement` (isolation: worktree) or pin it with a "
    "failing test on a branch, and report it as in flight. If an item genuinely cannot be yours (an "
    "irreversible action, or data only the user holds), end with one line `BLOCKED: <what and why>`."
)
UNCLOSED_BODY = (
    "This turn edited, ran or delegated something and ends without closing: no `DONE:`, `IN FLIGHT:` or "
    "`BLOCKED:` line. Under /lost-mary a turn ends in one of three states - `DONE: <the Done-means check "
    "you re-ran and what it showed>`, `IN FLIGHT: <the named agent or branch carrying the rest, and what "
    "resumes you>`, or `BLOCKED: <what only the user can supply, or the irreversible action>`. If anything "
    "remains, do it now - all of it - then close with the line that is true."
)
LOST_MARY_CLOSE = "Close the turn with `DONE:`, `IN FLIGHT:` or `BLOCKED:` - the line that is true."
SUBAGENT_HEADER = "The report is not finished (no-punt)."
SUBAGENT_BODY = (
    "Your final message stops to ask: {phrase!r}. A subagent cannot ask anyone - the lead reads only your "
    "final message and no user is here. If the brief lacks something you need, make your whole message "
    "`MISSING: <field>`; otherwise finish the task and report CHANGED / RAN / DONE MEANS / RISKS."
)


@dataclass(frozen=True)
class HandBack:
    kind: str  # "parked" | "go-ahead" | "remaining" | "question" | "unclosed"
    phrase: str


@dataclass
class Turn:
    """What the transcript says about the current turn and this hook's part in it."""

    found: bool = False  # a real prompt was seen, so the counts below mean something
    bounces: int = 0  # feedback records this hook wrote in the turn
    idle: int = 0  # of those, the trailing run with no tool call after them
    worked: bool = False  # the turn edited, ran or delegated something (WORK_TOOLS)
    lost_mary: bool = False  # /lost-mary invoked since the last /clear
    task: str = ""  # the user's own words that started the turn
    done_means: str = ""  # the `Done means` line the lead wrote in this turn, if any
    seen: set[str] = field(default_factory=set)  # feedback record uuids counted (a resume repeats them)


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


def blocks_of(content: Any, kind: str) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict) and item.get("type") == kind]


def command_of(content: Any) -> str | None:
    """Which tracked slash command a user record invokes: the tag block at the head of its content."""
    if not isinstance(content, str):
        return None
    head = content.lstrip()
    if not head.startswith("<command-"):
        return None
    head = head[:400]
    if ENTER in head:
        return "lost-mary"
    if RESET in head:
        return "clear"
    return None


def is_prompt(record: dict[str, Any]) -> bool:
    """A real prompt: a user record with text that is not a tool result, hook feedback or a meta record."""
    if record.get("type") != "user" or record.get("isMeta") is True:
        return False
    message = record.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return not HOOK_FEEDBACK.match(content)
    return bool(text_of(content).strip()) and not blocks_of(content, "tool_result")


def own_feedback(content: Any) -> bool:
    """A Stop block this hook wrote: the bracketed command names the script, or the text carries its header."""
    if not isinstance(content, str):
        return False
    feedback = HOOK_FEEDBACK.match(content)
    return bool(feedback) and ("no-punt" in feedback.group(2) or OWN_HEADER in content[:600])


def prompt_text(record: dict[str, Any]) -> str:
    """The user's own words in a prompt record: a skill's arguments, else the text without tag blocks."""
    message = record.get("message")
    raw = text_of(message.get("content")) if isinstance(message, dict) else ""
    args = COMMAND_ARGS.search(raw)
    text = args.group(1) if args else TAG_BLOCK.sub(" ", raw)
    text = " ".join(CONTROL.sub("", text).split())
    return text if len(text) <= TASK_CHARS else text[: TASK_CHARS - 3] + "..."


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


def transcript_of(payload: dict[str, Any]) -> Path | None:
    for key in ("agent_transcript_path", "transcript_path"):
        path = payload.get(key)
        if isinstance(path, str) and path:
            transcript = Path(path)
            if transcript.is_file():
                return transcript
    return None


def final_message(payload: dict[str, Any], transcript: Path | None) -> str | None:
    message = payload.get("last_assistant_message")
    if isinstance(message, str) and message.strip():
        return message
    if transcript is None:
        return None
    try:
        return last_assistant_text(transcript)
    except OSError as exc:
        notice(f"cannot read {transcript} ({exc})")
        return None


def turn_so_far(transcript: Path, prompt_id: str | None) -> Turn:
    """Read the turn from the transcript: the records carrying prompt_id, else those since the last real prompt.

    The /lost-mary flag is read from the whole file (it is a session property); everything else from the turn.
    `found` stays False when no real prompt was seen, and the caller falls back or lets the stop through.
    """
    turn = Turn()
    collecting = prompt_id is None
    with transcript.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"user"' not in line and '"assistant"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            kind = record.get("type")
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            match command_of(content) if kind == "user" else None:
                case "lost-mary":
                    turn.lost_mary = True
                case "clear":
                    turn.lost_mary = False
                case _:
                    pass
            prompt = is_prompt(record)
            if prompt_id is not None:
                pid = record.get("promptId")
                if not collecting:
                    if pid != prompt_id:
                        continue
                    collecting = True
                elif prompt and pid not in (None, prompt_id):
                    break
                if prompt and not turn.found:
                    turn.found, turn.task = True, prompt_text(record)
            elif prompt:  # without an id every real prompt starts the count afresh
                turn = Turn(found=True, lost_mary=turn.lost_mary, task=prompt_text(record))
                continue
            if kind == "user" and own_feedback(content):
                uid = record.get("uuid")
                if isinstance(uid, str) and uid:
                    if uid in turn.seen:
                        continue  # a resume writes the same record twice
                    turn.seen.add(uid)
                turn.bounces += 1
                turn.idle += 1
            elif kind == "assistant":
                tools = blocks_of(content, "tool_use")
                if tools:
                    turn.idle = 0
                    if any(str(tool.get("name")) in WORK_TOOLS for tool in tools):
                        turn.worked = True
                if not turn.done_means:
                    framed = DONE_MEANS.search(text_of(content))
                    if framed:
                        turn.done_means = " ".join(CONTROL.sub("", framed.group(1)).replace("**", "").split())[:200]
    return turn


def strip_quoted(text: str) -> str:
    """Quoted spans become the word QUOTED - still an object for "leave ... for you", never a match themselves."""
    return QUOTED.sub(" QUOTED ", QUOTE_LINE.sub(" ", FENCED.sub(" QUOTED ", text)))


def paragraphs_of(clean: str) -> list[str]:
    return [p.strip() for p in PARAGRAPH_BREAK.split(clean.strip()) if p.strip()]


def closing(clean: str) -> str:
    return "\n\n".join(paragraphs_of(clean)[-CLOSING_PARAGRAPHS:])


def first_match(patterns: tuple[re.Pattern[str], ...], clean: str) -> str | None:
    """The first non-negated match of any pattern, whitespace collapsed."""
    for pattern in patterns:
        for match in pattern.finditer(clean):
            if NEGATED.search(clean[max(0, match.start() - 48) : match.start()]):
                continue
            return " ".join(match.group(0).split())
    return None


def remaining_label(clean: str) -> str | None:
    """A closing 'Remaining:' / 'Next steps:' label with something after it - on the line or the next one."""
    tail = closing(clean)
    for match in REMAINING_LABEL.finditer(tail):
        rest = match.group(1).strip()
        if not rest:
            following = [ln.strip() for ln in tail[match.end() :].splitlines() if ln.strip()]
            rest = following[0] if following else ""
        if not rest or NOTHING.match(rest):
            continue
        return " ".join(match.group(0).split())[:120]
    return None


def closing_question(clean: str) -> str | None:
    """The last sentence when the message ends on a question mark - at the end of a turn, asked of the user."""
    paragraphs = paragraphs_of(clean)
    if not paragraphs:
        return None
    last = paragraphs[-1].rstrip(" \t*_)")
    if not last.endswith("?"):
        return None
    sentence = SENTENCE_END.split(last)[-1]
    return " ".join(sentence.split())[-120:]


def hand_back(text: str) -> HandBack | None:
    """The hand-back a final message carries - parked defect, go-ahead request, leftover list or closing question."""
    clean = strip_quoted(text)
    phrase = first_match(PUNT_PATTERNS, clean)
    if phrase is not None:
        return HandBack("parked", phrase)
    phrase = first_match(GO_AHEAD_PATTERNS, closing(clean))
    if phrase is not None:
        return HandBack("go-ahead", phrase)
    phrase = remaining_label(clean)
    if phrase is not None:
        return HandBack("remaining", phrase)
    question = closing_question(clean)
    if question is not None:
        return HandBack("question", question)
    return None


def closing_states(text: str) -> set[str]:
    """The closing lines a message carries: any of DONE, IN FLIGHT, BLOCKED."""
    return {m.group(1).replace("-", " ") for m in CLOSING_LINE.finditer(text)}


def declares_blocked(text: str) -> bool:
    """True when the message carries a `BLOCKED:` line - the explicit, auditable way to end a turn early."""
    return "BLOCKED" in closing_states(text)


def bounce_text(found: HandBack, subagent: bool, turn: Turn) -> str:
    if subagent:
        return f"{SUBAGENT_HEADER}\n{SUBAGENT_BODY.format(phrase=found.phrase)}"
    if found.kind == "parked":
        return f"{PARKED_HEADER}\n{PARKED_BODY.format(phrase=found.phrase)}"
    body = {"remaining": REMAINING_BODY, "unclosed": UNCLOSED_BODY}.get(found.kind, GO_AHEAD_BODY)
    lines = [OPEN_HEADER, body.format(phrase=found.phrase)]
    if turn.lost_mary and found.kind != "unclosed":
        lines.append(LOST_MARY_CLOSE)
    if turn.done_means:
        lines.append(f"Done means, as you framed it: {turn.done_means}")
    if turn.task:
        lines.append(f"The task for this turn: {turn.task}")
    return "\n".join(lines)


def main() -> int:
    payload = read_payload()
    if payload is None:
        notice("unreadable payload - allowing")
        return 0
    transcript = transcript_of(payload)
    message = final_message(payload, transcript)
    if message is None:
        notice("no final message in payload or transcript - allowing")
        return 0
    states = closing_states(message)
    if "BLOCKED" in states:
        return 0
    turn = Turn()
    if transcript is not None:
        prompt_id = payload.get("prompt_id")
        prompt_id = prompt_id if isinstance(prompt_id, str) and prompt_id else None
        try:
            turn = turn_so_far(transcript, prompt_id)
            if prompt_id is not None and not turn.found:  # the id matched nothing: count since the last prompt
                turn = turn_so_far(transcript, None)
        except OSError as exc:
            notice(f"cannot read {transcript} ({exc})")
    subagent = isinstance(payload.get("agent_id"), str)
    found = hand_back(message)
    if found is None and turn.lost_mary and turn.worked and not subagent and not states:
        found = HandBack("unclosed", "no DONE:, IN FLIGHT: or BLOCKED: line")
    if found is None:
        return 0
    if payload.get("stop_hook_active") is True and not turn.found:
        notice("no turn to count bounces from - one strike; allowing")
        return 0
    if turn.idle >= IDLE_CAP:
        notice(f"{turn.idle} bounces without progress in this turn - the model only rephrased; allowing")
        return 0
    if turn.bounces >= TURN_CAP:
        notice(f"{turn.bounces} bounces in this turn - allowing")
        return 0
    print(bounce_text(found, subagent, turn), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a hook fails open, loudly - never with a traceback
        notice(f"unexpected error ({exc!r}) - allowing")
        sys.exit(0)
