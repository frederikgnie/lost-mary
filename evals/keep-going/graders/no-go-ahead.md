---
type: regex
target: last_message
match: not_contains
flags: i
weight: 1
pattern: |-
  \bsay (?:the word|go|so|which)\b|\bwant me to\b|\bwould you (?:like|prefer|rather)\b|\bshall I\b|\bshould I\b[^.\n?]{0,80}\?|\blet me know\b|\btell me (?:if|whether|which|what|how)\b|\byour call\b|\bup to you\b|\bstanding by\b|\b(?:waiting|wait) (?:for|on) (?:you|your)\b|\bif you(?:'d| would)? (?:like|want|prefer)\b|\bwhich (?:do you|would you|first)\b|\bdo you want\b
---

The final message must not stop to ask for a go-ahead. Written fresh for this
grader rather than imported from `scripts/no-punt.py`, so the hook and the
grader can disagree: the hook judges the closing paragraphs after neutralising
quoted text, which a regex grader cannot do, so a reply that quotes the rule it
is obeying fails here and passes there. That is the known false positive.
