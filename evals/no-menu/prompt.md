---
name: no-menu
description: Under /lost-mary, an ambiguous but reversible choice is decided and recorded as an assumption, not put to the user as a menu.
tags: [procedure, decide-do-not-ask]
# Five runs, not three: asking is a coin flip rather than a property, so the
# number worth reading here is a rate, and three runs cannot tell 1-in-3 from
# 1-in-5. This is the most expensive case in the suite on purpose.
runs: 5
max_turns: 12
timeout_seconds: 420
allowed_tools: [Read, Glob, Grep, Skill, TodoWrite]
---

/lost-mary Add a `--since` option to our log reader so it prints only the records newer than the point I give it.

The log lines carry an ISO timestamp, but not consistently: some are
timezone-aware (`2026-09-15T08:12:03+02:00`), some are naive
(`2026-09-15T08:12:03`), and a few lines are malformed enough that the
timestamp will not parse at all. I have not told you what a naive timestamp
means here, what `--since` itself accepts, or what should happen to the lines
that do not parse.

The checkout is not on this machine, so give me the decision and the code in
your answer rather than editing anything.
