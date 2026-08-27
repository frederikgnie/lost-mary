# Playbooks

Lightweight routing. The lead matches the task to a playbook, then spawns the smallest set of roles that cover the steps. Playbooks do not replace roles; they order gates.

Every playbook requires an **acceptance predicate** in the lead’s own notes and in each implementation spawn prompt:

```text
Done means: <observable check the lead can re-run>
```

Examples: `billing tests green and POST /subscribe rejects null planId`, `old parser has zero callers and fixtures pass`, `export CSV has no duplicate rows on retry`.

---

## Feature

Use for new behavior with a clear product outcome.

1. State acceptance predicate and constraints.
2. Pre-spawn trust check if input is external.
3. `architect` or lead partitions scope → `plan_ready` when cross-module or risky.
4. `implementer` (one or more with explicit OWNED / OFF-LIMITS).
5. Targeted validation + proof token.
6. Deslop pass (shrink diff, no drive-by refactors).
7. `reviewer` (and `security-reviewer` if trust boundaries touched).
8. Lead integrates; re-check acceptance predicate.

**Default mode:** lead-only or one implementer. Team only if layers are separable.

## Bugfix

Use when something is wrong and the cause may be unclear.

1. State acceptance predicate (often: repro gone + regression test).
2. `debugger` reproduces, finds root cause, minimal fix, regression test.
3. If cause is clear and local, lead or implementer may fix without a team.
4. `reviewer` for non-trivial fixes.
5. Lead confirms predicate.

Do not “fix” by deleting or weakening tests.

## Refactor

Use for structure change without intended behavior change.

1. Acceptance predicate: behavior-preserving checks (tests, golden outputs).
2. Subtract dead weight first (principle: subtract before add).
3. `architect` if the shape is ambiguous → `plan_ready`.
4. `implementer` in tight scope; no feature creep.
5. Broaden validation; `reviewer` on large diffs.
6. Lead integrates.

## Investigation

Use for read-only questions (how/why, API behavior, incident).

1. Question and what would count as an answer.
2. `researcher` and/or `architect` only (no Write/Edit).
3. Handoff with sourced findings, options, recommendation, caveats.
4. Lead answers the user; no code change unless a follow-up playbook starts.

## Ship

Use when implementation is done and the goal is a clean integration or PR-ready tree.

1. Acceptance predicate includes review posture and validation commands.
2. Independent `reviewer` / `security-reviewer` as risk warrants.
3. Lead resolves findings, runs broadest practical validation.
4. Inspect final diff for scope creep.
5. Worktree cleanup per `worktree-rules.md` when isolation was used.

## Optional: design bakeoff (not a default)

Only for high-ambiguity architecture where two shapes are plausible:

1. Acceptance predicate and constraints shared by both arms.
2. Two short plans (architect variants or two implementer spikes in separate scopes/worktrees).
3. Lead picks a **base** and grafts useful pieces; does not merge both wholesale.
4. Continue under Feature or Refactor.

Do not use for routine features. Keep team size minimal.

---

## Mapping to roles

| Playbook step | Typical role |
|---------------|--------------|
| Partition / plan | architect, lead |
| Evidence gathering | researcher |
| Code change | implementer, debugger |
| Tests as focus | tester |
| Independent critique | reviewer, security-reviewer |
| Integration | lead only |

See also README role-selection table and `delegation-rules.md`.
