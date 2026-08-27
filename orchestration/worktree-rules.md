# Worktree Rules

Agent Teams provide separate sessions and shared coordination. Git worktrees provide filesystem and branch isolation. Treat them as complementary, not interchangeable.

## When to isolate

Use worktree isolation for parallel implementation when:

- multiple workers are writing code concurrently;
- workers need independent branches;
- a failed experiment should not disturb the lead's checkout;
- changes will be integrated after review.

For read-only research or review, isolation is usually unnecessary.

## Naming convention

Use a predictable pattern so the lead and teammates can find each other:

```text
worktree path:  ../<repo>-<task_id>-<role>
branch name:    agent/<task_id>/<role>
```

Examples:

```text
../billing-api-billing-backend-implementer
branch: agent/billing-backend/implementer

../billing-api-billing-frontend-implementer
branch: agent/billing-frontend/implementer
```

Keep `task_id` and `role` short, lowercase, hyphenated. Avoid spaces and shell-special characters.

## Creating a worktree (example commands)

From the main repository checkout:

```bash
# Create a branch and worktree for a backend implementer
git fetch origin
git worktree add -b agent/billing-backend/implementer \
  ../$(basename "$PWD")-billing-backend-implementer \
  origin/main   # or the integration branch

# Later, list worktrees
git worktree list

# After merge, remove the worktree and delete the local branch
git worktree remove ../$(basename "$PWD")-billing-backend-implementer
git branch -d agent/billing-backend/implementer
```

If Claude Code provides a built-in worktree isolation command, prefer that; the pattern above is the fallback convention this library expects the lead to follow.

## Ownership

Each implementation worker should receive an ownership statement such as:

```text
OWNED
- src/billing/**
- tests/billing/**

OFF-LIMITS
- src/auth/**
- database/migrations/**
- package.json
```

The narrower and more concrete the ownership boundary, the safer the parallelism.

Surface the branch and worktree path to the teammate in the spawn prompt:

```text
Work in worktree: ../billing-api-billing-backend-implementer
Branch: agent/billing-backend/implementer
OWNED: src/billing/**, tests/billing/**
OFF-LIMITS: src/ui/**, package.json, database/migrations/**
```

## Shared files

Treat these as high-conflict by default:

- lockfiles;
- package manifests;
- generated files;
- global configuration;
- schema/migration directories;
- top-level routing or registration files.

Prefer assigning shared-file changes to one worker or to the lead after the independent work is complete.

## Integration

Workers should leave a concise integration handoff (via the structured JSON). The lead owns the final merge/reconciliation decision.

Suggested lead merge order:

1. Inspect each workstream's diff and handoff.
2. Merge the least-conflicting or foundational workstream first (often schema/backend).
3. Resolve conflicts intentionally; never silently discard another worker's changes.
4. Run the broadest practical validation on the integrated tree.
5. Remove worktrees only after the integration branch is green.

## Worktree hygiene

Before handoff:

- inspect the diff;
- remove debugging artifacts;
- ensure intended files are the only changed files;
- run relevant tests;
- report anything that could conflict with another workstream.
