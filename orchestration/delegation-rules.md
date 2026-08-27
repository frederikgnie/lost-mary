# Delegation Rules

## First question: does parallelism help?

Before spawning anything, the lead should identify:

- the desired outcome;
- the major workstreams;
- dependencies between workstreams;
- shared files and shared state;
- what must remain in the lead's context.

If the work is sequential, highly coupled, or mostly concentrated in one file/module, do not create a team just for the sake of parallelism.

## Subagent vs team

Choose a **subagent** when:

- only the result needs to return to the lead;
- the worker does not need peer-to-peer discussion;
- the task is focused research, review, or an isolated side operation;
- minimizing context and token usage matters.

Choose an **agent team** when:

- multiple independent Claude Code sessions add real value;
- teammates benefit from direct communication;
- work spans separable layers or modules;
- competing hypotheses or independent reviews are useful.

## Team sizing

Default to 2–4 teammates for meaningful engineering work.

Use more only when the problem naturally decomposes into independent workstreams. More teammates increase coordination and token cost.

## Spawn prompts

Every implementation teammate prompt should state:

1. goal;
2. scope;
3. files/directories owned;
4. files/directories explicitly off-limits;
5. dependencies;
6. acceptance criteria;
7. validation expected;
8. required handoff information.

Bad:

> Work on billing.

Good:

> Implement subscription lifecycle handling. Own `src/billing/**` and `tests/billing/**`. Do not modify frontend code, migrations, or package dependencies. Preserve existing public API compatibility. Add regression tests and run the billing test suite. Report files changed, commands run, results, and integration concerns.

## Dependency management

Use the shared task list and explicit dependencies when workstreams are genuinely dependent.

Do not ask one teammate to wait in conversation when a task dependency can express the relationship more cleanly.

## Review

For risky changes, require plan approval before implementation. The lead should define the approval criterion, such as requiring tests, forbidding a schema change, or requiring backward compatibility.

After implementation, perform an independent review. For high-risk areas, use the security-reviewer as a separate review lens rather than asking the implementer to certify its own work.

## Completion

The lead should not declare success until:

- all required tasks are complete;
- relevant tests pass;
- material review findings are resolved;
- integration conflicts are addressed;
- the final diff is inspected.

## Structured handoffs

Every implementation, test, debug, review, research, and architecture teammate must emit a final JSON handoff per `orchestration/handoff.md`.

Spawn prompts should include:

- the `task_id` the worker must put in the handoff
- explicit owned and off-limits globs (these reappear in the handoff payload)
- the expectation that the final message is JSON only

The lead validates required fields and proof tokens before accepting the result. Free-text reports without a conforming JSON object are incomplete.
