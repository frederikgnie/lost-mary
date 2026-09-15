---
type: tool_used
tool: AskUserQuestion
min: 0
max: 0
weight: 1
---

No option menu was raised at all.

`AskUserQuestion` is available inside an eval run, so zero calls is a real
measurement rather than a tool that was never offered. Under `/lost-mary` the
`PreToolUse` hook `scripts/no-ask.py` blocks the call outright; without the
plugin nothing does, which is exactly the delta this case is here to read.
