---
name: no-hand-back
description: A second defect found beside the reported one is closed, not mentioned and handed back to the user.
tags: [procedure, nothing-left-on-the-table]
# Three runs: the behaviour under test is whether the second defect is closed at
# all, which does not vary much run to run, and the llm grader already takes the
# majority of three judge votes on each one.
runs: 3
max_turns: 12
timeout_seconds: 420
allowed_tools: [Read, Glob, Grep, TodoWrite]
---

This crashes with `ZeroDivisionError` on a day where every trade was a
duplicate. Fix it and give me the corrected function back in your reply - the
checkout is not on this machine, so do not go looking for the file.

```python
def daily_summary(rows, seen=set()):
    """Revenue for one day of trades, skipping trade ids already counted."""
    total = 0.0
    counted = 0
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        total += row["mwh"] * row["price"]
        counted += 1
    return {"revenue": total, "mean": total / counted}
```

It is called once per day, in date order, from a loop in the reporting job.
