---
name: pr
description: Push the current branch and open a pull request using the GitHub account that owns this repository (personal vs work), then switch gh back to the previously active account.
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
2. **Pick the account.** Derive `owner/repo` from the remote URL and note the
   currently active `gh` account. Run `gh repo view <owner/repo>`. If it exits
   non-zero (typically `Could not resolve to a Repository`), the active account
   cannot see the repo: switch with
   `gh auth switch --hostname github.com --user <account that owns this repo>`
   and remember which account to switch back to. `gh` serves only the active
   account's token, so the switch is the supported path (the repository's
   `AGENTS.md` may say which account to use).
3. **Check the commit identity.** `git config user.email` should belong to the
   same account as the remote owner; warn if it does not. Do not rewrite
   history.
4. **Push** the branch: `git push -u origin <branch>`.
5. **Create the PR** with `gh pr create --title "<title>" --body "<body>"`:
   - Title: imperative mood, under 70 characters; use the user's notes if given.
   - Body: `## Summary` (2-4 bullets), `## Validation` (exact commands and
     results from `/validate` - never invent them; write "not run" if you did
     not run them), `## Risks`, then the footer line
     `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
6. **Switch `gh` back** to the account that was active in step 2 - also when a
   step failed, before reporting the error - then print the PR URL.
