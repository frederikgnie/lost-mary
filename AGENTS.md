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

## Measuring whether the roles and skills change anything

`tests/` proves the hook *scripts* behave. Nothing proved that the agents and
skills change an **outcome** rather than merely reading well. `claude plugin
eval` is what answers that: it runs each case with the plugin and again without
it, and reports the delta.

`.claude-plugin/plugin.json` makes this repository an eval target and
`hooks/hooks.json` carries the wiring from `settings.example*.json` rewritten
to `${CLAUDE_PLUGIN_ROOT}`, so the plugin arm fires the same hooks an installed
copy does. This is **additive**: `install.sh` / `install.ps1` remain the
supported way to use the library and are untouched.

**Stage first; do not run it from the repository root.** The harness refuses a
plugin in which any file has more than one name, it scans the whole plugin root,
and it does not honour `.gitignore` - so a `uv`-created `.venv` (475 hard links
out of its cache here) aborts every run with "a file in the plugin has more than
one name (a hard link)". `evals/stage.ps1` and `evals/stage.sh` copy the six
directories that *are* plugin content to a temp dir, fail loudly if a hard link
survives the copy, and print the path:

```powershell
claude plugin eval (./evals/stage.ps1) --allow-tools Write --no-publish
```

**Read the delta, not the score.** A case that scores 1.0 with the plugin *and*
1.0 without it means the plugin is not what made it pass - the model would have
done that anyway - so the case needs sharpening or retiring. Graders marked
`arm: with-only` are a plugin-fired indicator, not part of the score.

Four cases live in `evals/<case>/prompt.md` with `evals/<case>/graders/*.md`;
each `runs:` value carries a comment saying why it is what it is. Before adding
one:

- **No case may need `Bash`.** Granting it requires an OS sandbox backend and
  native Windows has none, so a Bash-granting case only runs under WSL2.
  `--allow-tools Write` is needed for `evidence-in-report`, which writes files.
- **Tools follow graders.** A grader that implies a side effect only passes if
  the case's `allowed_tools` permits the tool that produces it *and* the
  operator granted it.
- **Hooks need `python3` on `PATH`.** A versioned manifest cannot carry the
  absolute `python.exe` path `settings.example.windows.json` needs, so on a
  machine without `python3` the plugin's hooks fail open and the run measures
  the agents and skills only.
- Results land in `evals/results/` and are git-ignored, like every other piece
  of runtime state.

**First reading, 2026-09-15 (2.1.272), `spawn-contract`, 1 run per arm, $0.73.**
`with 0.00  without 0.00  delta 0.00`, both arms failing `contract-fields` with
"Agent called 0x". The harness works; the case does not yet. The prompt tells
the model the checkout "is not on this machine", which makes delegating work on
files that cannot exist the obviously futile move, so a sensible model describes
the brief instead of spawning - and a grader keyed on `tool_used: Agent` scores
that zero. Either scaffold a real file so delegation is worth doing, or grade
the brief in `last_message` and stop asserting the tool call. Diagnosing the
next iteration needs `--keep-temp` (the run sandbox holding `trace.jsonl` is
deleted on success) or `--verbose --debug-file`. Budget it: one case, one run
per arm, is roughly $0.73, so a four-case suite at the default three runs is
about $9 per full reading.

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
