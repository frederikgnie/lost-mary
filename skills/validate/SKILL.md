---
name: validate
description: Run the project's lint, type check and tests on what changed, and report the exact commands with their results. Use before declaring any Python change done, before /pr, and whenever asked whether something passes.
argument-hint: [paths or packages - optional]
---

# Validate what changed

Working tree right now:

!`git status --porcelain --untracked-files=all 2>&1 | head -40`

Target from the user (may be empty): $ARGUMENTS

## Steps

1. **Commands come from the project.** Read the repository's `CLAUDE.md` /
   `AGENT.md` for the interpreter, the verified lint / typecheck / test commands
   and the known test roots. Use those. Fall back to `ruff`, `ty` and `pytest`
   from the project's venv only when nothing is documented.
2. **Lint and type-check the changed files** with the library checker. It finds
   the venv that owns each file, including a shared `<workspace>/<env>/.venv`
   beside several package repos:

   ```bash
   python ~/.claude/agent-library/scripts/pycheck.py --changed      # the repo containing the cwd, from any directory in it
   python ~/.claude/agent-library/scripts/pycheck.py <paths...>     # explicit paths
   python ~/.claude/agent-library/scripts/pycheck.py --all <paths>  # include pre-existing findings, not just new ones
   ```

   Use the project's own interpreter if plain `python` is not a real
   installation. In a multi-repo workspace (the root is not a git repo), run
   `--changed` once per package that has changes; the first output line names
   the repository it looked at.
3. **Run the tests that cover the touched packages** - the documented test roots
   for those packages, not the whole world, unless the whole suite is small.
   Discover first with `--collect-only -q` when unsure.
4. **Report** a table: `command -> result` with counts (`N passed`, `ty: 0
   errors`, `ruff: clean`). Quote failures verbatim. If something could not run,
   say so; never write "passes" for a command you did not run.

If a check fails and the fix is inside the current task's scope, fix it and
re-run; otherwise report it and stop.
