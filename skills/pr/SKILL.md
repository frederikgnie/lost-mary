---
name: pr
description: Push the current branch and open a pull request using the GitHub account that owns this repository (personal vs work), passing that account's token per command so the globally active gh account never changes.
argument-hint: [title or notes - optional]
disable-model-invocation: true
---

# Open a pull request with the right account

Context:

!`git remote get-url origin 2>&1`
!`git status --short --branch 2>&1 | head -20`
!`git config user.name 2>&1; git config user.email 2>&1`
!`gh auth status 2>&1`

Notes from the user (may be empty): $ARGUMENTS

## Steps

1. **Refuse to proceed** - say what is blocking and stop - if the context above
   shows `not a git repository` (a multi-repo workspace root: run every
   `git`/`gh` step with `-C <package>` against the package holding the
   unpushed work; only if several do, name them and stop), if the
   current branch is the default branch, or if the tree has uncommitted
   changes. Never commit or stash on the user's behalf here.
2. **Pick the account** that owns `owner/repo` (from the remote URL; the
   repository's `AGENTS.md` may name it). If it is not the active `gh` account,
   prefix every `gh` and `git push` below with
   `GH_TOKEN=$(gh auth token -u <account>)` - `gh` and its git credential helper
   both serve that token. **Never `gh auth switch`**: the active account is
   global, and other sessions and scheduled tasks act on whichever one is active
   (a switch here once made another session's PR reads 404).
3. **Check the commit identity.** `git config user.email` should belong to the
   same account as the remote owner; warn if it does not. Do not rewrite
   history.
4. **Push** the branch: `git push -u origin <branch>`. The PR targets the
   default branch, or an integration branch whose own PR is still open - never
   a branch already merged (the guard holds `gh pr create --base` on one).
5. **Create the PR** with `gh pr create --title "<title>" --body "<body>"`:
   - Title: imperative mood, under 70 characters; use the user's notes if given.
   - Body: `## Summary` (2-4 bullets), `## Validation` (exact commands and
     results from `/validate` - never invent them; write "not run" if you did
     not run them), `## Risks`, then the footer line
     `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
6. **Print the PR URL.** Nothing to restore: the active account never changed.
