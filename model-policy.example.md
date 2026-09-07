# Model policy

Copy to `~/.claude/agent-library/model-policy.md` and edit. The SessionStart hook in the
example settings prints it into every session, where it wins over the roles'
frontmatter defaults - so keep it short: it is re-injected on every compaction.

Routing: `explore` (investigation and the PLAN) and `review` on fable - errors
there multiply or ship. `implement` on opus - execution, caught by the spawn
contract, the hooks and the tests. Never sonnet for code. Pass `model:` on a
spawn only to move off those defaults, and say why.

If fable runs out it does NOT fall back: `fallbackModel` excludes usage limits,
and the Fable cap raises a usage-credits consent prompt that Remote Control
cannot display, so the session just stops answering. Recovery: `/model opus`
first (works from the phone), then answering at the desktop, then the weekly
reset. While fable is out, spawn explore and review with `model: opus`.

The lead session, not the subagents, consumes the weekly budget.
