"""Where the tokens went: sum transcript usage by model and by role since a date.

Every `assistant` record in a Claude Code transcript carries `message.model` and
`message.usage`. One API response is written as several records (thinking, text,
tool_use blocks) that repeat the same usage, so messages are deduplicated on
`message.id`. Subagent transcripts live under `<session>/subagents/`, which is
how lead and subagent are told apart. Reads transcripts only; writes nothing.

    python scripts/usage.py --since 2026-09-15
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

Key = tuple[str, str]  # (role, model)


def collect(root: Path, since: str) -> tuple[defaultdict[Key, Counter[str]], int]:
    """Return per-(role, model) token counters and the number of files read."""
    totals: defaultdict[Key, Counter[str]] = defaultdict(Counter)
    seen: set[str] = set()
    files = 0
    for path in root.rglob("*.jsonl"):
        role = "subagent" if "subagents" in path.parts else "lead"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files += 1
        for line in text.splitlines():
            if '"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            if str(rec.get("timestamp", ""))[:10] < since:
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            mid = msg.get("id")
            usage = msg.get("usage")
            if not isinstance(mid, str) or not isinstance(usage, dict) or mid in seen:
                continue
            seen.add(mid)
            t = totals[(role, str(msg.get("model", "?")))]
            t["msgs"] += 1
            t["out"] += int(usage.get("output_tokens") or 0)
            t["in"] += int(usage.get("input_tokens") or 0)
            t["cache_w"] += int(usage.get("cache_creation_input_tokens") or 0)
            t["cache_r"] += int(usage.get("cache_read_input_tokens") or 0)
    return totals, files


def render(totals: defaultdict[Key, Counter[str]], files: int, since: str) -> str:
    """Format the table plus the share of every column that went to fable."""
    lines = [f"since {since}, {files} transcript files"]
    hdr = f"{'role':9} {'model':26} {'msgs':>6} {'output':>10} {'input':>10} {'cache_w':>12} {'cache_r':>14}"
    lines += ["", hdr, "-" * len(hdr)]
    grand: Counter[str] = Counter()
    for (role, model), t in sorted(totals.items(), key=lambda kv: -kv[1]["out"]):
        lines.append(
            f"{role:9} {model:26} {t['msgs']:>6} {t['out']:>10,} {t['in']:>10,} {t['cache_w']:>12,} {t['cache_r']:>14,}"
        )
        grand.update(t)
    lines += ["-" * len(hdr)]
    lines.append(
        f"{'all':9} {'':26} {grand['msgs']:>6} {grand['out']:>10,} {grand['in']:>10,} "
        f"{grand['cache_w']:>12,} {grand['cache_r']:>14,}"
    )
    lines += ["", "fable share of each column"]
    for col in ("msgs", "out", "cache_w", "cache_r"):
        tot = grand[col] or 1
        lead = sum(t[col] for (r, m), t in totals.items() if r == "lead" and "fable" in m)
        sub = sum(t[col] for (r, m), t in totals.items() if r == "subagent" and "fable" in m)
        lines.append(
            f"  {col:8} lead {lead / tot:6.1%}   subagents {sub / tot:6.1%}   fable total {(lead + sub) / tot:6.1%}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="1970-01-01", help="YYYY-MM-DD; messages before it are ignored")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="transcript root (default: ~/.claude/projects)",
    )
    args = parser.parse_args()
    totals, files = collect(args.root, args.since)
    print(render(totals, files, args.since))


if __name__ == "__main__":
    main()
