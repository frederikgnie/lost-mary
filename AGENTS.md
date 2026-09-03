# Claude Code Agent Library - contributor notes

This repository is the canonical source for a personal, reusable Claude Code
operating layer. `README.md` explains what it is; this file is for changing it.

## Design intent (v2)

Four concerns, kept apart:

1. **Mechanisms** - `scripts/` hooks that run checks and compare claims with
   evidence. The only part the runtime enforces. Everything else is text.
2. **Roles** - `agents/`, three of them, each defined by what it *cannot* do
   (`explore` and `review` cannot mutate; `implement` must prove its work) and
   routed to a model/effort that fits.
3. **Procedures** - `skills/`, invoked when needed, kept to one screen.
4. **Project facts** - not here. Each repository's `CLAUDE.md` / `AGENT.md`
   owns its commands, invariants and danger zones; the roles are told those win.

Runtime state (`~/.claude/projects`, `tasks`, `teams`) belongs to Claude Code:
never versioned, never hand-edited.

## Changing things - checklist

- **Verify the runtime surface from the docs, not from memory**, before
  relying on a hook field, frontmatter key or file layout. Every unverified
  assumption in this project's history turned out wrong at least once. Record
  what you checked in `capabilities.md` (date, version, source).
- **Hooks fail open and say so on stderr.** Keep both properties. A hook that
  wedges every edit is worse than one that misses; a hook that goes quiet is
  worse than one that complains.
- **Read stdin as bytes, decode `utf-8-sig`.** PowerShell pipes prepend a BOM.
- **Tests never touch the real `~/.claude`.** Installer scenarios run against a
  sandboxed `HOME` and assert the sandbox is not the real one before writing.
  A CI step run locally without that gate has destroyed a developer's config
  once already.
- **`tests/` must stay green on Linux and Windows** with real `ruff`/`ty`
  (`pip install ruff ty`). `test-hooks.py` pins the transcript shape the
  evidence hook depends on; update the fixtures when the shape changes.
- **Update the installers together** (`install.sh`, `install.ps1`): the managed
  file lists, the retired-file lists and the `settings.json` drift check must
  match. Retire, never delete.
- **Keep `global-CLAUDE.md` short.** It loads into every session.
- **One canonical statement of the procedure** - in `skills/lost-mary/SKILL.md`.
  Do not restate it in the README or the roles.

## GitHub account convention

This repository lives under the personal `frederikgnie` GitHub account, not the
work account. Commits and `gh` operations must use that identity.

**Commit identity** is set per clone, because a repository cannot carry it:

```sh
git config --local user.name  "frederikgnie"
git config --local user.email "60433760+frederikgnie@users.noreply.github.com"
```

**Push and `gh` operations** require the personal account to be active:

```sh
gh auth switch --hostname github.com --user frederikgnie   # before pushing
gh auth switch --hostname github.com --user fgn-odigo      # after, to avoid cross-account surprises
```

An agent working in this repository performs that switch itself rather than
asking, and switches back when finished. `/pr` encodes exactly this procedure.

**Why this is not automatic.** `gh auth git-credential` serves only the token
of the *active* account; it does not select an account from the username in
the remote URL. Rewriting the remote as `https://frederikgnie@github.com/...`
therefore does not work - the helper returns nothing and Git falls through to
an interactive password prompt. With the work account active, this private
repository reads as `Repository not found`. The only mechanisms giving genuinely
per-repository authentication are an SSH host alias with a dedicated key, or a
stored per-repo token. Neither is set up, so the account switch is the
supported path.
