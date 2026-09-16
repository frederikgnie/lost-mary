# Model policy

Copy to `~/.claude/agent-library/model-policy.md` and edit. The SessionStart hook in the
example settings prints it into every session, where it wins over the roles'
frontmatter defaults - so keep it short: it is re-injected on every compaction.

Routing: `explore` (investigation and the PLAN) and `review` on fable - errors
there multiply or ship. `implement` on opus - execution, caught by the spawn
contract, the hooks and the tests. Never sonnet for code. Pass `model:` on a
spawn only to move off those defaults, and say why.

The lead runs on opus (`/model opus`, persists). Measured 2026-09-15/16 over
2,174 messages: a fable lead was 90-97% of all fable tokens - 302 messages at
337K context each - and the explore/review spawns 3-10%. Moving the lead is the
whole saving; the spawns stay where judgment concentrates. `/model fable` for a
session where the framing itself is the hard part - it persists, so switch back.

Nothing switches model when an allowance runs out: `fallbackModel` covers
overload and unavailable only, and no hook can set a model. With the lead on
opus the Fable cap can only hit a spawn, which ends in an API error - respawn
it with `model: opus`. A fable lead at the cap raises a usage-credits consent
prompt that Remote Control cannot display, so the session just stops answering
until `/model opus` (works from the phone).
