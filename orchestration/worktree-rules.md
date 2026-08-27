# Worktree Rules

Agent Teams provide separate sessions and shared coordination. Git worktrees provide filesystem and branch isolation. Treat them as complementary, not interchangeable.

## When to isolate

Use worktree isolation for parallel implementation when:

- multiple workers are writing code concurrently;
- workers need independent branches;
- a failed experiment should not disturb the lead's checkout;
- changes will be integrated after review.

For read-only research or review, isolation is usually unnecessary.

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

Workers should leave a concise integration handoff. The lead owns the final merge/reconciliation decision.

Never silently discard another worker's changes to resolve a conflict.

## Worktree hygiene

Before handoff:

- inspect the diff;
- remove debugging artifacts;
- ensure intended files are the only changed files;
- run relevant tests;
- report anything that could conflict with another workstream.
