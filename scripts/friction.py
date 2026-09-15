#!/usr/bin/env python3
"""friction: how often does Claude Code stop to ask, get refused, or hand work back?

Why this exists
---------------
The library promises fewer permission prompts, fewer questions and no "I'll
leave that for you". Whether that holds is an empirical question, and the
evidence is already on disk: every session transcript under
~/.claude/projects. This CLI reads the most recent N of them and reports:

  questions    AskUserQuestion calls, and how many offered a "(Recommended)"
               option - a decision the model had already made;
  refusals     tool calls refused by a permission prompt or the auto-mode
               classifier, grouped by the command's leading token;
  hand-backs   assistant messages that hand work back to the user, matched
               with the same patterns as scripts/no-punt.py;
  hooks        the firings a hook LEFT BEHIND, split into blocks (the hook
               doing its job), fail-open notices (exit 0 with stderr - a hook
               that could not do its job and said so on a stream nobody reads)
               and plain firings, each attributed to the script its command
               names. Not every firing: a hook that succeeds quietly leaves no
               record at all, so silence here is not evidence of a dead hook.
               `--health` answers that question from the wiring instead;
  allow rules  exact `Bash(...)` entries in settings.json that can never
               match a different command - the shape that keeps prompts
               coming (26 of 45 entries on the author's machine, 2026-09-04).

Read-only: it writes nothing except the reading `--record` asks for.
Run it before and after a change to the rules and compare the numbers
instead of the feeling.

Usage:
  friction.py [--sessions N] [--projects-dir DIR] [--settings FILE] [--json]
             [--since YYYY-MM-DD]  ignore records older than this date, so two
                          readings can cover disjoint windows instead of
                          double-counting the days they share
             [--record]   also save this reading under FRICTION_ROOT (default ~/.claude/agent-library/friction)
             [--history]  print the saved readings as a table and exit
             [--health]   per wired hook: does its script exist, does it parse,
                          when was it last seen firing - writes nothing

Runtime dependencies (see capabilities.md): the transcript record shape -
`type: assistant|user`, a top-level ISO `timestamp`, `message.content[]` items
of type `tool_use` {id, name, input} / `tool_result` {tool_use_id, content} /
`text` {text}, and hook firings as an `attachment` in one of three shapes
(`hook_blocking_error` / `hook_success` / `hook_additional_context`, verified
2026-09-15 on 2.1.260) - all of which Claude Code documents as internal.
Unreadable records are skipped.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

sys.dont_write_bytecode = True  # read-only means no __pycache__ either, not even for the no-punt import

FRICTION_ROOT_ENV = "FRICTION_ROOT"
DEFAULT_FRICTION_ROOT = Path.home() / ".claude" / "agent-library" / "friction"
NOASK_BLOCK = "blocked while this session runs under /lost-mary"  # first sentence of no-punt's sibling, no-ask
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # transcript text goes to a terminal; strip escapes
REFUSAL_MARKERS = (
    "denied by the Claude Code auto mode classifier",
    "requested permissions",
    "haven't granted it yet",
    "user doesn't want to proceed",
    "user doesn't want to take this action",
)
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
PS_ASSIGNMENT = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*\s*=")
WRAPPERS = frozenset({"sudo", "timeout", "nohup", "env", "command", "time"})
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHELL_TOKEN = re.compile(r"\"[^\"]*\"|'[^']*'|\S+")  # a hook command quotes its interpreter and script paths
# Hooks on these events speak on every firing (they inject context), so their absence from the
# transcripts means something. Everywhere else a hook speaks only to block or to warn, and silence
# is its normal state - calling that "dead" would be a false alarm.
SPEAKS_EVERY_TIME = frozenset({"SessionStart", "SubagentStart"})


@dataclass
class ProjectStats:
    sessions: int = 0
    questions: int = 0
    recommended: int = 0
    blocked: int = 0  # menus no-ask stopped before they reached the user
    refusals: int = 0
    hand_backs: int = 0


@dataclass
class HookSeen:
    """What one hook (identified by the script its command names) left in the transcripts."""

    fires: int = 0
    blocks: int = 0
    notices: int = 0
    last: str = ""  # ISO timestamp of the most recent firing, "" when no record carried one
    events: list[str] = field(default_factory=list)


@dataclass
class Report:
    sessions: int = 0
    first: str = ""
    last: str = ""
    since: str = ""
    projects: dict[str, ProjectStats] = field(default_factory=dict)
    refused_commands: Counter[str] = field(default_factory=Counter)
    refused_tools: Counter[str] = field(default_factory=Counter)
    # (timestamp, rendered example); kept newest-first so a fixed problem cannot read as a live one
    hand_backs_seen: list[tuple[str, str]] = field(default_factory=list)
    hook_fires: Counter[str] = field(default_factory=Counter)
    hook_blocks: Counter[str] = field(default_factory=Counter)
    hook_notices: Counter[str] = field(default_factory=Counter)
    hook_notice_last: dict[str, str] = field(default_factory=dict)
    hooks: dict[str, HookSeen] = field(default_factory=dict)
    allow_total: int = 0
    dead_allow: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def hand_back_examples(self) -> list[str]:
        return [text for _, text in sorted(self.hand_backs_seen, reverse=True)[:8]]

    @property
    def questions(self) -> int:
        return sum(p.questions for p in self.projects.values())

    @property
    def recommended(self) -> int:
        return sum(p.recommended for p in self.projects.values())

    @property
    def refusals(self) -> int:
        return sum(p.refusals for p in self.projects.values())

    @property
    def hand_backs(self) -> int:
        return sum(p.hand_backs for p in self.projects.values())

    @property
    def blocked(self) -> int:
        return sum(p.blocked for p in self.projects.values())


def load_punt_check() -> tuple[Any, str | None]:
    """The hand-back detector from no-punt.py, imported by path (the file name has a hyphen)."""
    path = Path(__file__).with_name("no-punt.py")
    try:
        spec = importlib.util.spec_from_file_location("no_punt", path)
        if spec is None or spec.loader is None:
            return None, f"cannot load {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.punt_phrase, None
    except (OSError, ImportError, AttributeError, SyntaxError) as exc:
        return None, f"hand-backs not counted: {exc}"


def leading_token(command: str) -> str:
    """The executable a command starts with - past comments, `cd`, assignments and wrappers.

    `cd X && cmd`, `# note\\n$p = ...\\nStop-ScheduledTask ...` and `VAR=1 cmd` all report `cmd`.
    """
    for segment in re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", command.strip()):
        segment = segment.strip()
        if not segment or segment.startswith("#") or segment == "\\" or PS_ASSIGNMENT.match(segment):
            continue
        tokens = segment.split()
        if tokens[0] == "cd":
            continue
        while tokens and (ENV_ASSIGNMENT.match(tokens[0]) or tokens[0] in WRAPPERS):
            tokens.pop(0)
        if tokens:
            return tokens[0].strip("\"'")[:60]
    return "(empty)"


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def has_recommended(question: Any) -> bool:
    """A question whose menu already marks one option "(Recommended)": a decision made, then asked."""
    if not isinstance(question, dict):
        return False
    options = question.get("options") or []
    return any(isinstance(o, dict) and "recommended" in str(o.get("label", "")).lower() for o in options)


def shell_tokens(command: str) -> list[str]:
    """Split a hook command into tokens, keeping quoted paths (which contain spaces) whole."""
    return [token.strip("\"'") for token in SHELL_TOKEN.findall(command)]


def hook_identity(command: str, fallback: str = "(unknown hook)") -> str:
    """A readable name for the hook behind a command line: `pycheck.py --hook`, `ledger.py record`.

    The transcript names the hook only by event ("PostToolUse:Edit"), which cannot tell two hooks on
    the same event apart. The command can: the first token ending in `.py` is the script, and its
    arguments are what distinguishes `ledger.py record` from `ledger.py recall`. An unparseable or
    non-python command still gets a name - the command itself - rather than being dropped, and a
    record that carries no command at all (hook_additional_context) falls back to its hookName.
    """
    tokens = shell_tokens(command)
    for index, token in enumerate(tokens):
        if token.lower().endswith(".py"):
            name = PurePosixPath(token.replace(chr(92), "/")).name
            return " ".join([name, *tokens[index + 1 :]])[:60]
    return clean_line(command)[:60] or fallback


def record_hook_fire(report: Report, identity: str, event: str, when: str, *, block: bool, notice: bool) -> None:
    seen = report.hooks.setdefault(identity, HookSeen())
    seen.fires += 1
    seen.blocks += int(block)
    seen.notices += int(notice)
    if when > seen.last:
        seen.last = when
    if event not in seen.events:
        seen.events.append(event)


def scan_hook_record(record: dict[str, Any], report: Report, when: str = "") -> None:
    """A hook firing, as Claude Code really records it: an `attachment` in one of three shapes.

    The three carry DIFFERENT keys (verified 2026-09-15, 2.1.260), which is why one test cannot serve
    for all: `hook_blocking_error` has no `exitCode` at all - the block text and the hook's own command
    line sit under `blockingError` - while `hook_success` carries exitCode/stdout/stderr/command and
    `hook_additional_context` carries only the injected `content`. Reading a block as "exitCode == 2"
    therefore never matched anything, and this counter reported 0 blocks through 222 real ones.

    A block is the hook doing its job. exit 0 with stderr is a hook that failed OPEN and explained
    itself to a stream nobody reads - a stale install or a wrong payload assumption hides there.
    """
    attachment = record.get("attachment")
    if not isinstance(attachment, dict) or not attachment.get("hookEvent"):
        return
    event = str(attachment.get("hookEvent"))
    kind = str(attachment.get("type") or "")
    named = f"({attachment.get('hookName') or 'unknown hook'})"  # the only name a record without a command has
    report.hook_fires[event] += 1
    if kind == "hook_blocking_error":
        detail = attachment.get("blockingError")
        detail = detail if isinstance(detail, dict) else {}
        report.hook_blocks[event] += 1
        identity = hook_identity(str(detail.get("command") or ""), named)
        record_hook_fire(report, identity, event, when, block=True, notice=False)
        return
    # hook_success names its own command; hook_additional_context carries neither command nor exitCode
    # nor stderr, so it falls through to a plain firing under its hookName - context added, nothing wrong.
    identity = hook_identity(str(attachment.get("command") or ""), named)
    stderr = str(attachment.get("stderr") or "").strip()
    code = attachment.get("exitCode")
    if code == 2:  # not observed on this version, but a block is a block whatever shape it arrives in
        report.hook_blocks[event] += 1
        record_hook_fire(report, identity, event, when, block=True, notice=False)
        return
    if stderr:
        key = f"{event}: {clean_line(stderr)}"
        report.hook_notices[key] += 1
        if when > report.hook_notice_last.get(key, ""):
            report.hook_notice_last[key] = when
        record_hook_fire(report, identity, event, when, block=False, notice=True)
        return
    record_hook_fire(report, identity, event, when, block=False, notice=False)


def clean_line(text: str) -> str:
    return CONTROL.sub("", " ".join(text.split()))[:120]


def record_time(record: dict[str, Any]) -> str:
    """The record's own ISO timestamp - the only thing that dates a finding.

    A file's mtime dates the session, not the event in it: reading mtimes is how this tool once
    reported hand-backs from 09-03..09-06 under a window labelled 09-02..09-15, which made problems
    already fixed read as live ones.
    """
    stamp = record.get("timestamp")
    return stamp[:24] if isinstance(stamp, str) and ISO_DATE.match(stamp[:10]) else ""


def scan_transcript(path: Path, stats: ProjectStats, report: Report, punt_phrase: Any) -> tuple[str, str] | None:
    """Count one transcript. Returns the (first, last) dates its records carry, or None when none do."""
    pending: dict[str, tuple[str, str]] = {}  # tool_use id -> (tool name, command)
    asked: dict[str, tuple[int, int]] = {}  # AskUserQuestion id -> (questions, of which with a recommended option)
    seen: set[str] = set()  # streaming writes one message as several records; count it once
    project = path.parent.name
    dates: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not any(k in line for k in ('"tool_use"', '"tool_result"', '"assistant"', '"hookEvent"')):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            when = record_time(record)
            if report.since and when and when[:10] < report.since:
                continue  # an undated record cannot be shown to be older, so it stays counted
            if when:
                dates.append(when[:10])
            scan_hook_record(record, report, when)
            content = (record.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            kind = record.get("type")
            if kind == "assistant":
                text = text_of(content)
                if text.strip() and text not in seen and punt_phrase is not None:
                    seen.add(text)
                    phrase = punt_phrase(text)
                    if phrase:
                        stats.hand_backs += 1
                        snippet = CONTROL.sub("", " ".join(text.split()))
                        clean_phrase = CONTROL.sub("", phrase)
                        day = when[:10] or "(undated)"
                        report.hand_backs_seen.append((when, f"{day}  [{project}] {clean_phrase!r}  {snippet[:110]}"))
                        if len(report.hand_backs_seen) > 200:  # dropped ones are older than all we keep
                            report.hand_backs_seen = sorted(report.hand_backs_seen, reverse=True)[:8]
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    name = str(item.get("name", ""))
                    tool_input = item.get("input")
                    command = str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""
                    uid = str(item.get("id"))
                    pending[uid] = (name, command)
                    if name == "AskUserQuestion":
                        questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
                        if not isinstance(questions, list):
                            questions = [None]
                        # Counted when the result arrives: a menu that no-ask blocked was never asked.
                        if uid in asked:  # no result ever came for the earlier one - it was still asked
                            count, recommended = asked.pop(uid)
                            stats.questions += count
                            stats.recommended += recommended
                        asked[uid] = (len(questions), sum(1 for q in questions if has_recommended(q)))
                elif item.get("type") == "tool_result":
                    uid = str(item.get("tool_use_id"))
                    raw = item.get("content")
                    body = raw if isinstance(raw, str) else text_of(raw)
                    if uid in asked:
                        count, recommended = asked.pop(uid)
                        if NOASK_BLOCK in body:
                            stats.blocked += count
                            continue
                        if not refused(body):
                            stats.questions += count
                            stats.recommended += recommended
                            continue
                    if refused(body):
                        stats.refusals += 1
                        name, command = pending.get(uid, ("?", ""))
                        report.refused_tools[name] += 1
                        if command:
                            report.refused_commands[leading_token(command)] += 1
    # Menus whose result never arrived (session cut off) were still asked.
    for count, recommended in asked.values():
        stats.questions += count
        stats.recommended += recommended
    return (min(dates), max(dates)) if dates else None


def refused(body: str) -> bool:
    """A refusal is the result itself, so its marker sits at the start; a Read of a file that merely mentions
    one does not count."""
    return any(marker in body[:300] for marker in REFUSAL_MARKERS)


def scan_settings(path: Path, report: Report) -> None:
    if not path.is_file():
        report.notes.append(f"settings not found: {path}")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        report.notes.append(f"settings unreadable ({exc})")
        return
    allow = (data.get("permissions") or {}).get("allow") if isinstance(data, dict) else None
    if not isinstance(allow, list):
        return
    report.allow_total = len(allow)
    report.dead_allow = [
        rule for rule in allow if isinstance(rule, str) and rule.startswith("Bash(") and "*" not in rule
    ]


def mtime(path: Path) -> float | None:
    """Modification time, or None for a file that vanished or a dangling symlink."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def build_report(projects_dir: Path, settings: Path, sessions: int, since: str = "") -> Report:
    report = Report(since=since)
    punt_phrase, note = load_punt_check()
    if note:
        report.notes.append(note)
    # Only the lead's own transcripts: subagents (<project>/<session>/subagents/) and
    # Workflow agents (wf_* project dirs) cannot ask the user anything.
    by_mtime: dict[Path, float] = {}
    for candidate in projects_dir.rglob("*.jsonl"):
        if candidate.parent.name == "subagents" or candidate.parent.name.startswith("wf_"):
            continue
        stamp = mtime(candidate)
        if stamp is not None:
            by_mtime[candidate] = stamp
    files = sorted(by_mtime, key=by_mtime.__getitem__, reverse=True)[:sessions]
    if not files:
        report.notes.append(f"no transcripts under {projects_dir}")
        return report
    report.sessions = len(files)
    dates: list[str] = []
    for path in files:
        stats = report.projects.setdefault(path.parent.name, ProjectStats())
        stats.sessions += 1
        span: tuple[str, str] | None = None
        try:
            span = scan_transcript(path, stats, report, punt_phrase)
        except OSError as exc:
            report.notes.append(f"skipped {path.name}: {exc}")
        if span is not None:
            dates += [span[0], span[1]]
        else:  # nothing in the file was dated: its mtime is the only date left
            stamp = by_mtime.get(path)
            if stamp is not None:
                dates.append(datetime.fromtimestamp(stamp, UTC).date().isoformat())
    if dates:
        report.first, report.last = min(dates), max(dates)
    scan_settings(settings, report)
    return report


def as_json(report: Report) -> dict[str, Any]:
    return {
        "sessions": report.sessions,
        "first": report.first,
        "last": report.last,
        "since": report.since,
        "questions": report.questions,
        "recommended": report.recommended,
        "blocked": report.blocked,
        "refusals": report.refusals,
        "hand_backs": report.hand_backs,
        "hook_fires": dict(report.hook_fires.most_common()),
        "hook_blocks": dict(report.hook_blocks.most_common()),
        "hook_notices": dict(report.hook_notices.most_common()),
        "hook_notice_last": dict(sorted(report.hook_notice_last.items())),
        "hooks_seen": {
            identity: {
                "fires": seen.fires,
                "blocks": seen.blocks,
                "notices": seen.notices,
                "last": seen.last,
                "events": seen.events,
            }
            for identity, seen in sorted(report.hooks.items(), key=lambda kv: (-kv[1].fires, kv[0]))
        },
        "allow_total": report.allow_total,
        "dead_allow": report.dead_allow,
        "refused_commands": dict(report.refused_commands.most_common()),
        "refused_tools": dict(report.refused_tools.most_common()),
        "projects": {
            name: {
                "sessions": p.sessions,
                "questions": p.questions,
                "recommended": p.recommended,
                "blocked": p.blocked,
                "refusals": p.refusals,
                "hand_backs": p.hand_backs,
            }
            for name, p in sorted(report.projects.items())
        },
        "hand_back_examples": report.hand_back_examples,
        "notes": report.notes,
    }


def friction_root() -> Path:
    return Path(os.environ.get(FRICTION_ROOT_ENV) or DEFAULT_FRICTION_ROOT)


def save_reading(report: Report) -> Path:
    root = friction_root()
    root.mkdir(parents=True, exist_ok=True)
    data = as_json(report)
    data["recorded_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    target = root / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def as_count(value: Any) -> int:
    """A count from a saved reading, which may predate the key or hold something that is not a number."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def history_text() -> str:
    """The saved readings as a table.

    Every column reads through `as_count`/`.get` with a default, and one unreadable file costs ONE ROW:
    readings saved by an older version lack today's keys, and a table that dies on the oldest of them
    would throw away the comparison the whole file exists for.
    """
    root = friction_root()
    files = sorted(root.glob("*.json")) if root.is_dir() else []
    if not files:
        return f"no saved readings under {root} (run with --record)"
    lines = ["recorded (UTC)        sessions  questions  recommended  blocked  refusals  hand-backs  dead rules  span"]
    for path in files:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                continue
            q, r = as_count(d.get("questions")), as_count(d.get("recommended"))
            dead = d.get("dead_allow")
            since = str(d.get("since") or "")
            span = f"{d.get('first', '')}..{d.get('last', '')}" + (f" (since {since})" if since else "")
            lines.append(
                f"{str(d.get('recorded_at', path.stem))[:20]:<21} {as_count(d.get('sessions')):>8}  {q:>9}  "
                f"{pct(r, q):>11}  {as_count(d.get('blocked')):>7}  {as_count(d.get('refusals')):>8}  "
                f"{as_count(d.get('hand_backs')):>10}  {len(dead) if isinstance(dead, list) else 0:>10}  {span}"
            )
        except Exception as exc:  # one unreadable reading drops its row, never the table
            lines.append(f"{path.stem:<21} (unreadable: {exc!r})")
    return "\n".join(lines)


def pct(part: int, whole: int) -> str:
    return f"{100 * part // whole}%" if whole else "-"


def as_text(report: Report) -> str:
    window = f"{report.first}..{report.last}" + (f" (since {report.since})" if report.since else "")
    lines = [f"friction report - {report.sessions} session(s), {window}", ""]
    lines.append(
        f"questions     {report.questions} asked via menus (AskUserQuestion), "
        f'{report.recommended} with a "(Recommended)" option ({pct(report.recommended, report.questions)}); '
        f"{report.blocked} blocked by no-ask"
    )
    tools = ", ".join(f"{n} {c}" for n, c in report.refused_tools.most_common(4))
    lines.append(f"refusals      {report.refusals} tool call(s) refused" + (f"  ({tools})" if tools else ""))
    lines.append(f"hand-backs    {report.hand_backs} assistant message(s) hand work back to the user")
    fires, blocks, notices = (
        sum(report.hook_fires.values()),
        sum(report.hook_blocks.values()),
        sum(report.hook_notices.values()),
    )
    lines.append(
        f"hooks         {fires} firing(s) recorded, {blocks} block(s), {notices} fail-open notice(s) "
        "- a hook that succeeds quietly leaves no record, so run --health for liveness"
    )
    dead = len(report.dead_allow)
    lines.append(f"allow rules   {report.allow_total} entries, {dead} exact Bash rule(s) that never match again")
    if report.projects:
        lines += ["", "per project"]
        width = min(48, max(len(n) for n in report.projects))
        for name, p in sorted(report.projects.items()):
            lines.append(
                f"  {name[:48]:<{width}}  sessions {p.sessions:>3}  questions {p.questions:>3} "
                f"({p.recommended} recommended, {p.blocked} blocked)  "
                f"refusals {p.refusals:>3}  hand-backs {p.hand_backs:>3}"
            )
    if report.refused_commands:
        lines += ["", "refused commands (leading token)"]
        for token, count in report.refused_commands.most_common(10):
            lines.append(f"  {count:>3}  {token}")
    if report.hand_back_examples:
        lines += ["", "hand-back examples"]
        lines += [f"  {example}" for example in report.hand_back_examples]
    if report.dead_allow:
        lines += ["", "dead allow rules (exact Bash entries - prefer `Bash(cmd *)`)"]
        lines += [f"  {CONTROL.sub('', rule)[:110]}" for rule in report.dead_allow[:12]]
        if len(report.dead_allow) > 12:
            lines.append(f"  ... and {len(report.dead_allow) - 12} more")
    if report.hooks:
        lines += ["", "hooks seen firing (identity from the hook's own command line)"]
        for identity, seen in sorted(report.hooks.items(), key=lambda kv: (-kv[1].fires, kv[0]))[:12]:
            detail = f"{seen.blocks} block(s), {seen.notices} notice(s)"
            lines.append(
                f"  {identity[:34]:<34}  {seen.fires:>4} firing(s), {detail:<26}  "
                f"last {seen.last[:10] or '(undated)'}  [{', '.join(seen.events)}]"
            )
    if report.hook_notices:
        lines += ["", "hook fail-open notices (a hook that could not do its job)"]
        lines += [
            f"  {n:>3}  last {report.hook_notice_last.get(text, '')[:10] or '(undated)'}  {text}"
            for text, n in report.hook_notices.most_common(8)
        ]
    if report.notes:
        lines += ["", "notes"]
        lines += [f"  {note}" for note in report.notes]
    return "\n".join(lines)


@dataclass
class WiredHook:
    event: str
    matcher: str
    command: str
    identity: str
    script: Path | None  # the .py the command names, already home-expanded; None for a non-python hook


def expand_home(text: str) -> str:
    """`$HOME`/`~` as the installers write them into settings.json - the shell expands these, we do not."""
    home = str(Path.home())
    for token in ("${HOME}", "$HOME", "%USERPROFILE%"):
        text = text.replace(token, home)
    return f"{home}/{text[2:]}" if text.startswith("~/") else text


def wired_hooks(settings: Path) -> tuple[list[WiredHook], list[str]]:
    """Every hook command in settings.json, with the script it names. Notes explain what could not be read."""
    hooks: list[WiredHook] = []
    notes: list[str] = []
    if not settings.is_file():
        return hooks, [f"settings not found: {settings}"]
    try:
        data = json.loads(settings.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return hooks, [f"settings unreadable ({exc})"]
    events = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(events, dict):
        return hooks, [f"no hooks block in {settings}"]
    for event, groups in events.items():
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            matcher = str(group.get("matcher") or "(any)")
            for entry in group.get("hooks") if isinstance(group.get("hooks"), list) else []:
                if not isinstance(entry, dict) or not entry.get("command"):
                    continue
                command = str(entry.get("command"))
                script: Path | None = None
                for token in shell_tokens(command):
                    if token.lower().endswith(".py"):
                        script = Path(expand_home(token))
                        break
                hooks.append(WiredHook(str(event), matcher, command, hook_identity(command), script))
    return hooks, notes


def script_verdict(script: Path | None) -> str:
    """Does the wired script exist, and is it still valid Python?

    `ast.parse` and nothing else: importing a hook script runs its module body, and a health check that
    executes the thing it is checking is a way to break the machine it is meant to reassure you about.
    """
    if script is None:
        return "skipped - not a python hook"
    if not script.is_file():
        return "BROKEN - script missing"
    try:
        ast.parse(script.read_text(encoding="utf-8-sig"))
    except SyntaxError as exc:
        return f"BROKEN - does not parse (line {exc.lineno})"
    except (OSError, ValueError) as exc:
        return f"UNKNOWN - unreadable ({exc})"
    return "ok - exists, parses"


def liveness(hook: WiredHook, report: Report) -> str:
    """When this hook was last seen firing - and, when it was never seen, whether that means anything.

    Silence is the normal state of a gate: `no-ask` that allowed, `pycheck` that found nothing and
    `check-evidence` that was satisfied all exit 0 with nothing to say, and Claude Code records nothing
    at all for them. Printing "dead" here would be a false alarm about a hook doing its job.
    """
    seen = report.hooks.get(hook.identity)
    if seen is not None:
        return f"last fired {seen.last[:10] or '(undated)'} ({seen.fires} firing(s), {seen.blocks} block(s))"
    script = hook.identity.split(" ")[0]  # same script, other arguments: still evidence the file runs
    matches = [s for name, s in report.hooks.items() if name.split(" ")[0] == script]
    if matches:
        return f"script seen {max(m.last for m in matches)[:10] or '(undated)'} under other arguments"
    if hook.event in SPEAKS_EVERY_TIME:
        return "NOT SEEN - this event adds context on every firing, so check the wiring"
    return "not seen - expected: it speaks only when it blocks or warns"


def health_text(settings: Path, report: Report) -> str:
    hooks, notes = wired_hooks(settings)
    window = f"{report.first}..{report.last}" if report.first else "no dated records"
    lines = [
        f"hook health - {len(hooks)} hook(s) wired in {settings}",
        f"transcripts read: {report.sessions} session(s), {window}"
        + (f", since {report.since}" if report.since else ""),
        "",
    ]
    for hook in hooks:
        name = hook.identity if hook.script is not None else clean_line(hook.command)[:34]
        lines.append(
            f"  {hook.event[:14]:<14} {hook.matcher[:24]:<24} {name[:34]:<34} "
            f"{script_verdict(hook.script):<28} {liveness(hook, report)}"
        )
    if not hooks:
        lines.append("  (none)")
    for note in notes + report.notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except ValueError:
                pass
    parser = argparse.ArgumentParser(description="Report how often Claude Code asked, was refused, or handed back.")
    parser.add_argument("--sessions", type=int, default=50, help="most recent transcripts to read (default 50)")
    parser.add_argument("--projects-dir", type=Path, default=Path.home() / ".claude" / "projects")
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude" / "settings.json")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--record", action="store_true", help="also save this reading under FRICTION_ROOT")
    parser.add_argument("--history", action="store_true", help="print saved readings and exit")
    parser.add_argument("--health", action="store_true", help="check the wired hooks themselves; writes nothing")
    parser.add_argument("--since", default="", metavar="YYYY-MM-DD", help="ignore records older than this date")
    args = parser.parse_args(argv)
    if args.since and not ISO_DATE.match(args.since):
        print(f"friction: --since wants YYYY-MM-DD, got {args.since!r}", file=sys.stderr)
        return 2
    if args.history:
        print(history_text())
        return 0
    if not args.projects_dir.is_dir():
        print(f"friction: no transcripts - {args.projects_dir} is not a directory", file=sys.stderr)
        return 0
    report = build_report(args.projects_dir, args.settings, max(1, args.sessions), args.since)
    if args.health:  # liveness, not friction: reads the wiring, writes nothing, ignores --record
        print(health_text(args.settings, report))
        return 0
    print(json.dumps(as_json(report), indent=2, ensure_ascii=False) if args.json else as_text(report))
    if args.record:
        saved = save_reading(report)
        print(f"saved: {saved}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a CLI, not a hook: say what broke, exit non-zero, no traceback
        print(f"friction: unexpected error ({exc!r})", file=sys.stderr)
        sys.exit(1)
