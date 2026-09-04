# Getting started - a 101

What this is: two hooks that make Claude Code check its own Python work, three
lean agent roles with cost routing, three slash commands, and one short page of
rules. Read time ~5 minutes, setup ~3.

---

## 1. Install

```powershell
.\install.ps1                 # Windows
```

```bash
./install.sh                  # macOS / Linux
```

Add `-DryRun` / `--dry-run` to see what it would do first. It copies the agents,
skills and hook scripts into `~/.claude/` and adds one line to
`~/.claude/CLAUDE.md` - `@~/.claude/agent-library/global-CLAUDE.md` - after a
timestamped backup. Your own content is never rewritten. Anything left over from
v1 is renamed `*.retired.<timestamp>`, not deleted.

## 2. Turn on the hooks (this is the part that matters)

The installer never edits `settings.json`. Copy the `hooks` block from
`settings.example.windows.json` (Windows) or `settings.example.json`
(macOS/Linux) into `~/.claude/settings.json`. A running Claude Code normally
picks it up live; restart if it does not.

Windows: replace `C:/ABSOLUTE/PATH/TO/python.exe` with a real interpreter. The
`python` on PATH is often the Microsoft Store stub, and a hook that cannot
start fails open - silently, forever. Keep `$HOME` as is; it expands under both
Git Bash and PowerShell.

Without this step the library is documentation. With it:

- every `Edit`/`Write` of a `.py` file comes back with `ruff` and `ty` findings
  while the change is still fresh;
- an `implement` subagent that claims "tests pass" without a test run in its
  transcript is bounced and told to run them or say it did not.

## 3. Verify it actually works

```powershell
.\install.ps1 -Verify          # files in place, import line present, hooks wired (exit 0)
python tests\test-hooks.py     # from the repo; needs ruff + ty on PATH (pip install ruff ty)
```

```bash
./install.sh --verify
python3 tests/test-hooks.py
```

Then the smoke test the suite cannot do - the live one. In a fresh session,
ask Claude to add `import os` to the top of any `.py` file in a project with a
venv. Within a second of the edit you should see a hook message quoting
`F401 'os' imported but unused`. If you do not, the interpreter path in
`settings.json` is wrong (see §7). Record the date in `capabilities.md`'s
"Verified-live log" so the next upgrade has a baseline.

## 4. Daily use

- **Just ask.** The hook feedback arrives on its own; fix findings in place.
- **`/validate`** before calling anything done: runs the project's lint,
  typecheck and the tests covering what changed, using the commands documented
  in that project's `CLAUDE.md`, and reports `command -> result`. Claude may
  run it by itself.
- **`/pr`** when the branch is ready: pushes and opens the PR with the GitHub
  account that owns the repo, then switches `gh` back.
- **`/lost-mary <task>`** for anything non-trivial: forces the acceptance
  predicate, picks the smallest execution mode, and gates "done" on evidence.

## 5. Delegating (where tokens are won or lost)

| Your situation | Do | Not |
| --- | --- | --- |
| Small, local change | Ask in the main session | Don't spawn anything |
| Need to understand code, history or a library first | `explore` (cheap, read-only, returns `path:line` anchors) | Don't send the expensive model to read |
| A change you won't make yourself | `implement` with the block below | Don't spawn without `DONE MEANS` |
| Want a second opinion before merging | `review` - give it the diff | Don't ask the implementer to review itself |
| Truly separable scopes written concurrently | several `implement`, each `isolation: worktree` | Don't form a team because the task feels big |

Every `implement` spawn:

```text
GOAL:
SCOPE:
OWNED:        globs it may change
OFF-LIMITS:   globs it must not touch
DEPENDS ON:
DONE MEANS:   a check you can re-run
VALIDATION:   exact commands (from the project's CLAUDE.md)
REPORT:       CHANGED / RAN / DONE MEANS / RISKS
```

`DONE MEANS` is the line that decides whether this works. Then re-run the
decisive check yourself - the report is a claim, the re-run is the evidence.

## 6. Make it excellent on *your* repo

The library knows nothing about your code on purpose. The highest-leverage
thing you can do is put these in each repository's `CLAUDE.md` / `AGENT.md`:

- the interpreter and the **verified** lint / typecheck / test commands, with
  the date you checked them;
- the test roots and how many tests they collect;
- the architecture in five lines and the dependency direction between packages;
- danger zones (anything that writes to production, anything without auth);
- the domain review lens (for power markets: look-ahead leakage, DST 23/25-hour
  days, MW vs MWh, NaN through joins, timezone-naive timestamps).

`implement` and `review` read those files first and are told they win over the
library's defaults.

## 7. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| No hook message after editing a `.py` file | interpreter path wrong, `settings.json` not merged, or the hook not yet picked up (a running Claude Code normally picks up a `settings.json` change live; restart if it did not) | pipe a payload into the hook by hand: `echo '{"tool_name":"Edit","tool_input":{"file_path":"tests/fixtures/pycheck/bad.py"}}' \| python scripts/pycheck.py --hook` from the repo; want exit 2 |
| Hook says `ruff: not found` / `ty: not found` | tools not in the file's venv, PATH, or `uvx` | install them in the project venv (`uv add --dev ruff ty`) or globally |
| `pycheck` uses the wrong venv | the terminal that launched Claude Code has another env active | the file's own `.venv` wins; the ambient `VIRTUAL_ENV` is only a fallback - check for a stray `.venv` above the file |
| `check-evidence` never bounces anything | matcher does not include the agent type, or the transcript layout changed | `--verify` shows the wiring; `capabilities.md` lists the layout the hook expects |
| The lead still shows option menus under `/lost-mary` | `no-ask` not wired (`--verify` shows it), or `PreToolUse` does not fire for `AskUserQuestion` on this version | pipe a payload by hand: `echo '{"tool_name":"AskUserQuestion","transcript_path":"<session .jsonl>"}' \| python scripts/no-ask.py`; want exit 2 when that transcript holds a `/lost-mary` turn. Fallback: `"deny": ["AskUserQuestion"]` under `permissions` |
| The lead still ends with "I'll leave that for you" | `no-punt` not wired (`--verify` shows it), or the phrasing is one the patterns miss | pipe the message by hand: `echo '{"last_assistant_message":"I will leave that for you"}' \| python scripts/no-punt.py`; want exit 2. Add the missed phrasing to `PUNT_PATTERNS` with a test |
| `--verify` prints `NOTE: N exact Bash allow rules ...` | you clicked "always allow" on exact commands; those rules never fire again | replace them with prefix rules (`Bash(git push *)`); `scripts/friction.py` lists them |
| `--verify` prints `DRIFT: autoMode.allow replaces the built-in classifier rules` | your `autoMode.allow` list has no `"$defaults"` entry, so it replaced the built-ins | add `"$defaults"` as the first entry (see `settings.example.json`) |
| `/validate` or `/pr` missing from the `/` menu | skills normally register live, but discovery can lag | start a new session; restart if it still does not appear |
| A retired v1 role still appears | `~/.claude/agents/<role>.md` re-created by hand or by another tool | `--verify` flags it as `STALE`; re-run the installer |

## 8. Honest limits

- Hooks **fail open** on any payload they cannot read. A Claude Code upgrade
  can switch them off silently; redo §3 afterwards.
- `check-evidence` checks that validation commands ran and how they exited,
  not that the tests were meaningful. It honours `stop_hook_active`, so the
  second, re-emitted stop after a bounce goes through and the lead decides.
- `pycheck` is Python-only.
- `no-ask` keys on the `/lost-mary` turn in the session transcript - an
  undocumented shape - and only removes the option-menu tool; a plain-text
  question still reaches you.
- `no-punt` matches phrasing, not intent: a hand-back worded in a way the
  patterns do not cover goes through, and it bounces only once per turn.
- On Windows there is no Bash sandbox, so read-only roles have no Bash; when
  `explore` needs `git log`, the lead runs it.
