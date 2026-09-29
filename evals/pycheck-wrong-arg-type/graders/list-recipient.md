---
type: regex
target: {source: file, path: alerts/page.py}
match: contains
weight: 1
pattern: |-
  send\(\s*(?:to\s*=\s*)?\[|\[\s*(?:[\w.]+\.)?ONCALL_ADDRESS\s*\]
---

`alerts/page.py` hands `send` a list of recipients - a list literal at the call,
or `[ONCALL_ADDRESS]` built beforehand. A bare string is what the prompt
describes and what ty rejects against `to: list[str]` (and a `str` would iterate
per character inside `send`).
