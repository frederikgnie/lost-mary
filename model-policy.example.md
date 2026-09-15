# Model policy

Copy to `~/.claude/agent-library/model-policy.md` and edit. The SessionStart hook in the
example settings prints it into every session, where it wins over the roles'
frontmatter defaults - so keep it short: it is re-injected on every compaction.

Routing: `explore` (investigation and the PLAN) and `review` on fable - errors
there multiply or ship. `implement` on opus - execution, caught by the spawn
contract, the hooks and the tests. Never sonnet for code. Pass `model:` on a
spawn only to move off those defaults, and say why.

The lead consumes the weekly budget, not the subagents - so the lead runs on
fable too (`/model fable` persists as the new default). It frames, routes and
judges evidence; it executes nothing.

Nothing switches model when an allowance runs out. `fallbackModel` covers
overload and unavailable only, no hook can set a model, and the Fable cap
raises a usage-credits consent prompt that Remote Control cannot display - so
the session just stops answering. Recovery is manual: `/model opus` (works from
the phone). While fable is out, spawn explore and review with `model: opus`.
