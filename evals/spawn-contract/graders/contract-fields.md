---
type: tool_used
tool: Agent
min: 1
weight: 1
input_match: '(?=[\s\S]*OWNED)(?=[\s\S]*OFF-LIMITS)(?=[\s\S]*DONE MEANS)(?=[\s\S]*VALIDATION)'
---

At least one `Agent` call whose prompt carries all four scope fields.

The task text above supplies the raw material for each one - the file to change,
the file that must not be touched, the behaviour that counts as done, the two
commands - but never names a field, so the words can only come from the library:
`skills/lost-mary/SKILL.md` writes the spawn block, `agents/implement.md` says a
brief without them is refused, and `scripts/check-spawn.py` refuses it before the
subagent starts. Lookaheads rather than `.` with an `s` flag: the matcher builds
a JS `RegExp` from this string with no flags, and `[\s\S]` needs none.
