---
type: tool_used
tool: Agent
min: 1
weight: 1
---

At least one `Agent` call, whatever it carries.

This is the precondition, split out from `contract-fields` so that the two ways
of failing stop reading alike. A run that never spawns fails both and scores
0.00; a run that spawns a brief without the four fields fails only
`contract-fields` and scores 0.25; a run that spawns with them scores 1.00. When
one grader carried both, "never delegated" and "delegated badly" were the same
0.00 in both arms, and the with/without delta could not say which one the
library had moved - which is what the 2026-09-15 and 2026-09-16 readings of this
case did not answer.

Low weight on purpose: the case stages a real package and forbids this session
from editing it, so spawning at all is close to forced. What the spawn carries
is the behaviour under test.
