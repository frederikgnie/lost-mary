---
type: regex
target: {source: file, path: report.py}
match: not_contains
flags: m
weight: 0.5
pattern: |-
  ^import os\s*$
---

The pasted `import os` is unused (ruff F401) and nothing in the `--csv` change
needs it. Half weight: removing a line the user did not ask about is
arguably out of scope, so this is secondary to the crash graders. Also fails when `report.py` is missing.
