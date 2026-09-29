---
type: regex
target: last_message
match: not_contains
flags: i
weight: 1
pattern: |-
  \bruff\b|\bty\s+check\b|\blint(?:s|ed|ing|-clean)?\b|\btype[- ]check(?:s|ed|er|ing)?\b
---

No ruff, ty, lint or type-check talk on a task with no Python edit: the hook
has nothing to say here, and a claim about checks that never ran is exactly the
over-reach this case guards against.
