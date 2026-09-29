---
name: pycheck-pasted-latent-bug
description: Pasted code carries an unused import and an `int | None` indexed as `int`; the task asks only for a flag, and pycheck reports both on the first Write.
tags: [pycheck, lint, types]
# Three runs: whether the model notices the hazard unaided varies run to run;
# three is the floor that separates a habit from a lucky draw.
runs: 3
max_turns: 15
timeout_seconds: 420
# Write/Edit are gated: the operator passes --allow-tools Write Edit. No Bash in
# either arm (AGENTS.md: no case may need it), so the model cannot run ty itself -
# the pycheck PostToolUse hook is the only type checker in the room.
allowed_tools: [Read, Glob, Grep, Write, Edit]
---

Save this as `report.py` and add a `--csv` flag: with it, print the rows as CSV
(`name,mwh`, header first) instead of the peak line. Without the flag, print the
peak line in the same format as now.

```python
import os
import sys
from dataclasses import dataclass


@dataclass
class Row:
    name: str
    mwh: float


def find_peak(rows: list[Row]) -> int | None:
    if not rows:
        return None
    return max(range(len(rows)), key=lambda i: rows[i].mwh)


def main(argv: list[str]) -> int:
    rows = [Row(n, float(v)) for n, v in (a.split("=") for a in argv)]
    peak = find_peak(rows)
    print(f"peak: {rows[peak].name} {rows[peak].mwh:.1f} MWh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```
