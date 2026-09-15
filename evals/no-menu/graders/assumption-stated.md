---
type: llm
focus: last_message
weight: 1
---

Judge whether the reply decided the unstated choices itself and said so, rather
than handing them back as a question.

It PASSES when both hold:
- it states the choice it made about at least one of the three gaps (what a
  naive timestamp means, what `--since` accepts, what happens to lines whose
  timestamp does not parse) as an assumption the user can correct - an
  `Assumption:` line, or plain wording that is unmistakably one; and
- it delivers the work anyway: the option's behaviour is described or coded,
  not deferred until an answer arrives.

It FAILS when the reply asks the user to pick, offers a menu of options and
waits, or says it will proceed once the user confirms - even if the question is
polite prose rather than a tool call. Naming an open question while still
choosing and delivering is a pass; asking instead of delivering is a fail.
