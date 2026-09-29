# Model policy

Copy to `~/.claude/agent-library/model-policy.md` and edit. The SessionStart hook in the
example settings prints it into every session, where it wins over the roles'
frontmatter defaults - so keep it short: it is re-injected on every compaction.

Routing: all three roles on opus (since 2026-09-28) - Opus 5.5 scores above
Fable 5.1 on Anthropic's launch benchmarks at a lower price, and Fable bills
to usage credits. Never sonnet for code. Pass `model:` on a spawn only to move
off that default, and say why - `model: fable` for a long unsupervised run or
a problem opus has failed twice.

The lead runs on opus (`/model opus`, persists). Measured 2026-09-15/16 over
2,174 messages: a fable lead was 90-97% of all fable tokens - 302 messages at
337K context each - and the explore/review spawns 3-10%. Moving the lead was the
whole saving then; the spawns followed on 2026-09-28. `/model fable` for a
session where the framing itself is the hard part - it persists, so switch back.

`fallbackModel` covers overload and unavailable only - never a usage or spend
limit. The spawn hook covers that for spawns: once a Fable spawn has failed on
usage credits, `check-spawn` runs every fable spawn (an explicit `model: fable`,
a role file that names fable, or a role without a `model:` line under
`CLAUDE_CODE_SUBAGENT_MODEL=fable`) on `opus` - the newest Opus - for the next
6 hours, then
lets one spawn try Fable again while the rest stay on Opus. The spawns already
running when the limit first hits fail: spawn them again unchanged.
`LOST_MARY_FABLE=off` forces Opus, `=on` disables the switch. A fable LEAD at
the cap raises a usage-credits consent prompt that Remote Control cannot
display, so the session just stops answering until `/model opus` (works from
the phone).
