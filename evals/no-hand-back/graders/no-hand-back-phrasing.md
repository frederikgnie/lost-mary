---
type: regex
target: last_message
match: not_contains
flags: i
weight: 1
pattern: |-
  (?:i(?:'ll| will| would)?\s+)?(?:leave|leaving|left)\b[^.\n]{0,60}\b(?:for|to|with) you\b|\bfor you to (?:fix|handle|address|resolve|decide|sort out|look (?:at|into)|follow up)\b|\b(?:you|someone|somebody) (?:may|might|should|could|will) want to (?:fix|address|resolve|handle|change|follow up)\b|\b(?:up|over) to you to (?:fix|decide|handle|address)\b|\b(?:did not|didn't|have not|haven't) (?:fix|fixed|address|addressed|touch|touched)\b[^.\n]{0,60}\b(?:for you|your (?:side|end))\b|\bleft (?:it )?(?:as )?(?:an? )?(?:exercise|follow-?up|todo|to-?do)\b[^.\n]{0,40}\byou\b
---

The final message must not hand the second defect back.

Written fresh for this grader rather than imported from `scripts/no-punt.py`:
the hook and the grader should be able to disagree, and the hook's patterns lean
on a quote-stripping pass that a regex grader has no way to run. The cost of
that is one known false positive - a reply that quotes the rule it is obeying
("never `I'll leave that for you`") fails this grader while the hook would let
it through. That is the trade a six-type grader language buys you.
