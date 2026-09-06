---
name: ledger
description: Show what agents actually edited and ran in this project - the ledger's latest entries, taken from transcripts, not from reports. Use when you need to know what changed recently, who changed it, or whether a report matches the record.
argument-hint: [number of entries, default 8]
---

# Ledger

Run the interpreter your `settings.json` hooks use (the absolute `python.exe`
path on Windows; `python3` elsewhere), with N = `$ARGUMENTS` or 8 when empty:

```text
<python> ~/.claude/agent-library/scripts/ledger.py recall --latest --count N
```

Show the output verbatim, then one line on which entries matter for the task
at hand. `edited` and `ran` are evidence; `claimed` is the agent's own words;
when a report and the ledger disagree, the ledger is right.

The ledger lives in `~/.claude/agent-library/ledger/<project>/`, one file per
subagent stop and per lead turn that changed something. `--latest` picks the
most recently active project - normally the one this session runs in. Empty
output means no agent has recorded anything for this project yet.
