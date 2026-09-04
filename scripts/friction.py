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
  allow rules  exact `Bash(...)` entries in settings.json that can never
               match a different command - the shape that keeps prompts
               coming (26 of 45 entries on the author's machine, 2026-09-04).

Read-only: it writes nothing. Run it before and after a change to the rules
and compare the numbers instead of the feeling.

Usage:
  friction.py [--sessions N] [--projects-dir DIR] [--settings FILE] [--json]

Runtime dependencies (see capabilities.md): the transcript record shape -
`type: assistant|user`, `message.content[]` items of type `tool_use`
{id, name, input} / `tool_result` {tool_use_id, content} / `text` {text} -
which Claude Code documents as internal. Unreadable records are skipped.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True  # read-only means no __pycache__ either, not even for the no-punt import

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


@dataclass
class ProjectStats:
    sessions: int = 0
    questions: int = 0
    recommended: int = 0
    blocked: int = 0  # menus no-ask stopped before they reached the user
    refusals: int = 0
    hand_backs: int = 0


@dataclass
class Report:
    sessions: int = 0
    first: str = ""
    last: str = ""
    projects: dict[str, ProjectStats] = field(default_factory=dict)
    refused_commands: Counter[str] = field(default_factory=Counter)
    refused_tools: Counter[str] = field(default_factory=Counter)
    hand_back_examples: list[str] = field(default_factory=list)
    allow_total: int = 0
    dead_allow: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

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


def scan_transcript(path: Path, stats: ProjectStats, report: Report, punt_phrase: Any) -> None:
    pending: dict[str, tuple[str, str]] = {}  # tool_use id -> (tool name, command)
    asked: dict[str, tuple[int, int]] = {}  # AskUserQuestion id -> (questions, of which with a recommended option)
    seen: set[str] = set()  # streaming writes one message as several records; count it once
    project = path.parent.name
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"tool_use"' not in line and '"tool_result"' not in line and '"assistant"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
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
                        if len(report.hand_back_examples) < 8:
                            snippet = CONTROL.sub("", " ".join(text.split()))
                            clean_phrase = CONTROL.sub("", phrase)
                            report.hand_back_examples.append(f"[{project}] {clean_phrase!r}  {snippet[:110]}")
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


def build_report(projects_dir: Path, settings: Path, sessions: int) -> Report:
    report = Report()
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
    stamps = sorted(by_mtime[f] for f in files)
    report.first = datetime.fromtimestamp(stamps[0], UTC).date().isoformat()
    report.last = datetime.fromtimestamp(stamps[-1], UTC).date().isoformat()
    for path in files:
        stats = report.projects.setdefault(path.parent.name, ProjectStats())
        stats.sessions += 1
        try:
            scan_transcript(path, stats, report, punt_phrase)
        except OSError as exc:
            report.notes.append(f"skipped {path.name}: {exc}")
    scan_settings(settings, report)
    return report


def as_json(report: Report) -> dict[str, Any]:
    return {
        "sessions": report.sessions,
        "first": report.first,
        "last": report.last,
        "questions": report.questions,
        "recommended": report.recommended,
        "blocked": report.blocked,
        "refusals": report.refusals,
        "hand_backs": report.hand_backs,
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


def pct(part: int, whole: int) -> str:
    return f"{100 * part // whole}%" if whole else "-"


def as_text(report: Report) -> str:
    lines = [f"friction report - {report.sessions} session(s), {report.first}..{report.last}", ""]
    lines.append(
        f"questions     {report.questions} asked via menus (AskUserQuestion), "
        f'{report.recommended} with a "(Recommended)" option ({pct(report.recommended, report.questions)}); '
        f"{report.blocked} blocked by no-ask"
    )
    tools = ", ".join(f"{n} {c}" for n, c in report.refused_tools.most_common(4))
    lines.append(f"refusals      {report.refusals} tool call(s) refused" + (f"  ({tools})" if tools else ""))
    lines.append(f"hand-backs    {report.hand_backs} assistant message(s) hand work back to the user")
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
    if report.notes:
        lines += ["", "notes"]
        lines += [f"  {note}" for note in report.notes]
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
    args = parser.parse_args(argv)
    if not args.projects_dir.is_dir():
        print(f"friction: no transcripts - {args.projects_dir} is not a directory", file=sys.stderr)
        return 0
    report = build_report(args.projects_dir, args.settings, max(1, args.sessions))
    print(json.dumps(as_json(report), indent=2, ensure_ascii=False) if args.json else as_text(report))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a CLI, not a hook: say what broke, exit non-zero, no traceback
        print(f"friction: unexpected error ({exc!r})", file=sys.stderr)
        sys.exit(1)
