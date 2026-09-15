#!/usr/bin/env python3
"""Behavioural tests for the hooks. Exit 0 = all passed.

pycheck (PostToolUse) needs `ruff`, `ty` and `git` on PATH (or ruff/ty via
`uvx`); CI installs the tools. Scratch projects live under <repo>/.tmp-pycheck,
deliberately NOT the system temp dir, because pycheck skips scratch files there.

check-evidence (SubagentStop) runs against synthetic transcripts that mirror the
on-disk shape observed in Claude Code 2.1.259:
  <project>/<session>.jsonl                          parent transcript
  <project>/<session>/subagents/agent-<id>.jsonl     the subagent's own
with `message.content[]` items of type `tool_use` {id, name, input} and
`tool_result` {tool_use_id, content, is_error}; a failing Bash result carries
`is_error: true` and text starting with "Exit code N", but a piped command
(`pytest ... | tail`) reports success with the failure only in the text.

Usage: python tests/test-hooks.py
"""

from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYCHECK = ROOT / "scripts" / "pycheck.py"
EVIDENCE = ROOT / "scripts" / "check-evidence.py"
NOASK = ROOT / "scripts" / "no-ask.py"
SPAWN = ROOT / "scripts" / "check-spawn.py"
PERMIT = ROOT / "scripts" / "permit.py"
NOPUNT = ROOT / "scripts" / "no-punt.py"
FRICTION = ROOT / "scripts" / "friction.py"
LEDGERPY = ROOT / "scripts" / "ledger.py"
WITNESS = ROOT / "scripts" / "witness.py"
FIXTURES = ROOT / "tests" / "fixtures" / "pycheck"
SCRATCH = ROOT / ".tmp-pycheck"

ALLOW, BLOCK = 0, 2
failures: list[str] = []
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def sh(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", env=GIT_ENV
    )


def git(cwd: Path, *args: str) -> None:
    r = sh(["git", "-c", "core.autocrlf=false", *args], cwd)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")


def run_hook(
    script: Path, event: object, *args: str, bom: bool = False, env: dict[str, str] | None = None
) -> tuple[int, str]:
    payload = json.dumps(event)
    if bom:
        payload = chr(0xFEFF) + payload
    result = subprocess.run(
        [sys.executable, str(script), *args], input=payload.encode("utf-8"), capture_output=True, env=env
    )
    return result.returncode, result.stderr.decode("utf-8", "replace")


def cli(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(PYCHECK), *args], cwd=str(cwd) if cwd else None, capture_output=True, env=env
    )
    return result.returncode, result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")


def expect(name: str, actual: int, wanted: int, detail: str = "") -> None:
    ok = actual == wanted
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        failures.append(f"{name}: expected exit {wanted}, got {actual}\n{detail.strip()[:700]}")


def expect_true(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        failures.append(f"{name}\n{detail.strip()[:700]}")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def make_venv(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    return path


def make_pkg(path: Path) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "pyproject.toml").write_text("[project]\nname='p'\n", encoding="utf-8")
    return path / "src"


NO_VENV_ENV = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
BAD = (FIXTURES / "bad.py").read_text(encoding="utf-8")
GOOD = (FIXTURES / "good.py").read_text(encoding="utf-8")
RUFF_CFG = "[project]\nname = 'p'\nversion = '0'\nrequires-python = '>=3.11'\n\n[tool.ruff]\nline-length = 100\n"


def nuke(path: Path) -> None:
    """rmtree that also removes read-only files (git objects are read-only on Windows)."""
    if not path.exists():
        return
    for p in path.rglob("*"):
        with contextlib.suppress(OSError):
            p.chmod(0o700)
    shutil.rmtree(path, ignore_errors=True)


nuke(SCRATCH)
SCRATCH.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- pycheck
print("pycheck.py - PostToolUse lint/type feedback")
tools_present = {t: shutil.which(t) is not None for t in ("ruff", "ty", "git")}
if not all(tools_present.values()) and shutil.which("uvx") is None:
    failures.append(f"ruff/ty/git not runnable (PATH: {tools_present}, no uvx). Install: pip install ruff ty")
    print("  FAIL ruff, ty and git must be available for these tests")
else:

    def edit_event(path: Path, tool: str = "Edit") -> dict[str, object]:
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"},
            "tool_result": "ok",
            "cwd": str(ROOT),
        }

    # Untracked scratch project: everything is new.
    proj = SCRATCH / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(RUFF_CFG, encoding="utf-8")
    (proj / "bad.py").write_text(BAD, encoding="utf-8")
    (proj / "good.py").write_text(GOOD, encoding="utf-8")

    rc, err = run_hook(PYCHECK, edit_event(proj / "bad.py"), "--hook")
    expect("Edit on untracked bad.py -> findings (exit 2)", rc, BLOCK, err)
    expect_true("  ... ruff finding reported (F401)", "F401" in err, err)
    expect_true("  ... ty finding reported (error[...])", "error[" in err, err)
    expect_true("  ... header counts new findings", "2 new finding(s)" in err, err)
    expect_true("  ... ruff summary noise filtered", "fixable with" not in err, err)
    expect_true("  ... re-run hint quotes the real interpreter", f'Re-run: "{sys.executable}"' in err, err)

    rc, err = run_hook(PYCHECK, edit_event(proj / "good.py", "Write"), "--hook")
    expect("Write on good.py -> clean (exit 0)", rc, ALLOW, err)

    rc, err = run_hook(PYCHECK, edit_event(ROOT / "README.md"), "--hook")
    expect("non-Python file -> ignored", rc, ALLOW, err)
    rc, err = run_hook(PYCHECK, edit_event(proj / "missing.py"), "--hook")
    expect("deleted/missing file -> ignored", rc, ALLOW, err)
    ev = edit_event(proj / "bad.py")
    ev["tool_name"] = "Bash"
    rc, err = run_hook(PYCHECK, ev, "--hook")
    expect("non-edit tool -> ignored", rc, ALLOW, err)
    rc, err = run_hook(PYCHECK, edit_event(proj / "bad.py"), "--hook", bom=True)
    expect("BOM-prefixed payload still checks (PowerShell pipe)", rc, BLOCK, err)
    proc = subprocess.run([sys.executable, str(PYCHECK), "--hook"], input=b"not json", capture_output=True)
    expect("garbage payload fails open", proc.returncode, ALLOW, proc.stderr.decode())
    rc, err = run_hook(PYCHECK, {"toolName": "Edit", "tool_input": {"file_path": str(proj / "bad.py")}}, "--hook")
    expect("renamed payload field fails open ...", rc, ALLOW, err)
    expect_true("  ... but says so (fail open, loudly)", "no tool_name" in err, err)
    rc, err = run_hook(PYCHECK, {"tool_name": "Edit", "tool_input": {"filePath": str(proj / "bad.py")}}, "--hook")
    expect_true("renamed file_path field is announced", "no tool_input.file_path" in err, err)

    # Non-ASCII file name: stderr must still be valid UTF-8.
    (proj / "bäd.py").write_text(BAD, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(PYCHECK), "--hook"],
        input=json.dumps(edit_event(proj / "bäd.py")).encode("utf-8"),
        capture_output=True,
    )
    try:
        text = proc.stderr.decode("utf-8")
        expect_true("non-ASCII path: stderr is valid UTF-8 and names the file", "bäd.py" in text, text)
    except UnicodeDecodeError as exc:
        expect_true("non-ASCII path: stderr is valid UTF-8", False, str(exc))

    rc, out = cli(str(proj / "bad.py"))
    expect("CLI on bad.py -> exit 1", rc, 1, out)
    rc, out = cli(str(proj / "good.py"))
    expect("CLI on good.py -> exit 0", rc, 0, out)

    # No ruff config anywhere above the file: format is not the hook's business.
    # (Placed in the system temp dir - outside this repo's ruff.toml - with the
    # temp skip disabled for the test.)
    nocfg = Path(tempfile.gettempdir()) / f"pycheck-nocfg-{uuid.uuid4().hex[:8]}"
    nocfg.mkdir()
    try:
        (nocfg / "loose.py").write_text("x  =  1\n", encoding="utf-8")
        allow_temp = {**os.environ, "PYCHECK_ALLOW_TEMP": "1"}
        rc, err = run_hook(PYCHECK, edit_event(nocfg / "loose.py"), "--hook", env=allow_temp)
        expect("no ruff config -> no format nag", rc, ALLOW, err)
    finally:
        nuke(nocfg)

    # Project excludes are honoured even though the file is passed explicitly.
    excl = SCRATCH / "excl"
    excl.mkdir()
    (excl / "pyproject.toml").write_text(
        RUFF_CFG + 'extend-exclude = ["gen.py"]\n\n[tool.ty.src]\nexclude = ["gen.py"]\n', encoding="utf-8"
    )
    (excl / "gen.py").write_text(BAD, encoding="utf-8")
    rc, err = run_hook(PYCHECK, edit_event(excl / "gen.py"), "--hook")
    expect("file excluded by the project's ruff/ty config -> clean", rc, ALLOW, err)

    # HEAD baseline: only what the edit introduced is reported.
    repo = SCRATCH / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "pyproject.toml").write_text(RUFF_CFG, encoding="utf-8")
    (repo / "mod.py").write_text(BAD, encoding="utf-8")  # F401 + invalid-assignment already at HEAD
    (repo / "fmt.py").write_text("x  =  1\n", encoding="utf-8")  # unformatted at HEAD
    (repo / "ok.py").write_text("x = 1\n", encoding="utf-8")  # formatted at HEAD
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "mod.py").write_text(BAD + "y: str = 1\n", encoding="utf-8")
    rc, err = run_hook(PYCHECK, edit_event(repo / "mod.py"), "--hook")
    expect("tracked file: only the NEW diagnostic is reported", rc, BLOCK, err)
    expect_true("  ... the new error is shown", "assignable to `str`" in err, err)
    expect_true("  ... pre-existing F401 is not replayed", "F401" not in err, err)
    expect_true("  ... pre-existing count is stated", "2 pre-existing not shown" in err, err)
    rc, out = cli("--all", str(repo / "mod.py"))
    expect_true("CLI --all shows pre-existing too", rc == 1 and "F401" in out, out)
    (repo / "mod.py").write_text(BAD, encoding="utf-8")
    rc, err = run_hook(PYCHECK, edit_event(repo / "mod.py"), "--hook")
    expect("tracked file unchanged vs HEAD -> silent", rc, ALLOW, err)
    (repo / "fmt.py").write_text("x  =  2\n", encoding="utf-8")
    rc, err = run_hook(PYCHECK, edit_event(repo / "fmt.py"), "--hook")
    expect("format drift that existed at HEAD is not nagged", rc, ALLOW, err)
    (repo / "ok.py").write_text("x  =  1\n", encoding="utf-8")
    rc, err = run_hook(PYCHECK, edit_event(repo / "ok.py"), "--hook")
    expect("format drift introduced by the edit is reported", rc, BLOCK, err)
    expect_true("  ... as a reformat hint", "would reformat" in err, err)
    leftovers = list(repo.glob("__pycheck_head_*"))
    expect_true("no baseline temp files left behind", not leftovers, str(leftovers))

    # --changed: anchored on the repo root, from any cwd, renames and non-ASCII names.
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "a.py").write_text(GOOD, encoding="utf-8")
    (repo / "src" / "pkg" / "old.py").write_text(GOOD, encoding="utf-8")
    (repo / "src" / "pkg" / "gone.py").write_text(GOOD, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "pkg")
    (repo / "src" / "pkg" / "a.py").write_text(BAD, encoding="utf-8")
    (repo / "src" / "pkg" / "nëw.py").write_text(BAD, encoding="utf-8")
    git(repo, "mv", "src/pkg/old.py", "src/pkg/renamed.py")
    (repo / "src" / "pkg" / "renamed.py").write_text(BAD, encoding="utf-8")
    git(repo, "rm", "-q", "src/pkg/gone.py")
    (repo / "fmt.py").write_text("x  =  1\n", encoding="utf-8")
    (repo / "ok.py").write_text("x = 1\n", encoding="utf-8")
    rc, out = cli("--changed", cwd=repo / "src" / "pkg")
    expect("--changed from a subdirectory finds the repo's changes (exit 1)", rc, 1, out)
    expect_true("  ... modified file found", "a.py" in out, out)
    expect_true("  ... non-ASCII untracked file found", "nëw.py" in out, out)
    expect_true("  ... renamed file found", "renamed.py" in out, out)
    expect_true("  ... deleted file skipped", "gone.py" not in out, out)
    expect_true("  ... states which repository it looked at", "pycheck: repository" in out, out)
    rc, out = cli("--changed", cwd=SCRATCH)
    expect_true("--changed outside a repo but inside the outer one still resolves a toplevel", rc in (0, 1), out)

    # Venv discovery.
    pycheck = load_module(PYCHECK, "_pycheck")
    saved = os.environ.pop("VIRTUAL_ENV", None)
    try:
        t1_env = make_venv(SCRATCH / "t1" / "ws" / "env" / ".venv")
        t1_src = make_pkg(SCRATCH / "t1" / "ws" / "pkg")
        found, how = pycheck.find_venv(t1_src)
        expect_true("sibling venv without ownership evidence is NOT adopted", found is None, f"{found} ({how})")
        sp = t1_env / "Lib" / "site-packages"
        sp.mkdir(parents=True)
        (sp / "__editable__.p-0.0.1.pth").write_text(str(t1_src.resolve()) + "\n", encoding="utf-8")
        found, how = pycheck.find_venv(t1_src)
        expect_true(
            "sibling venv that owns the project (editable .pth) IS adopted", found == t1_env, f"{found} ({how})"
        )

        make_venv(SCRATCH / "t2" / ".venv")
        t2_src = make_pkg(SCRATCH / "t2" / "ws" / "pkg")
        found, how = pycheck.find_venv(t2_src)
        expect_true("does not climb above the workspace root", found is None, f"{found} ({how})")
        os.environ["VIRTUAL_ENV"] = str(SCRATCH / "t2" / ".venv")
        found, how = pycheck.find_venv(t2_src)
        expect_true(
            "ambient VIRTUAL_ENV is the fallback",
            found == SCRATCH / "t2" / ".venv" and "ambient" in how,
            f"{found} ({how})",
        )
        local_env = make_venv(SCRATCH / "t2" / "ws" / "pkg" / ".venv")
        found, how = pycheck.find_venv(t2_src)
        expect_true("the file's own .venv beats VIRTUAL_ENV", found == local_env, f"{found} ({how})")
        os.environ.pop("VIRTUAL_ENV", None)

        # Linked worktree: the env lives in the main checkout.
        wt_main = SCRATCH / "wt" / "main"
        wt_main.mkdir(parents=True)
        git(wt_main, "init", "-q")
        make_venv(wt_main / ".venv")
        (wt_main / "p").mkdir()
        (wt_main / "p" / "m.py").write_text(GOOD, encoding="utf-8")
        (wt_main / ".gitignore").write_text(".venv/\n", encoding="utf-8")
        git(wt_main, "add", ".")
        git(wt_main, "commit", "-q", "-m", "i")
        wt_linked = SCRATCH / "wt" / "linked"
        git(wt_main, "worktree", "add", "-q", str(wt_linked), "-b", "wt")
        found, how = pycheck.find_venv(wt_linked / "p")
        expect_true(
            "linked worktree resolves the main checkout's .venv", found == wt_main / ".venv", f"{found} ({how})"
        )
        git(wt_main, "worktree", "remove", "--force", str(wt_linked))
    finally:
        os.environ.pop("VIRTUAL_ENV", None)
        if saved is not None:
            os.environ["VIRTUAL_ENV"] = saved

# --------------------------------------------------------------------- check-evidence
print()
print("check-evidence.py - SubagentStop claims vs transcript")
EV = SCRATCH / "evidence"
PROJECT = EV / "proj"
PROJECT.mkdir(parents=True)
# EVIDENCE_ROOT override for every hook child spawned from here on (run_hook inherits this process's
# environment): check-evidence prefers the witness file over the transcript, and must never read - or let
# witness.py write - under the real ~/.claude. Empty until a case fills it, so every existing case below
# is the "no witness file -> transcript" fallback.
CEROOT = EV / "witness-root"
assert not str(CEROOT).startswith(str(Path.home() / ".claude")), "check-evidence tests must stay out of ~/.claude"
os.environ["EVIDENCE_ROOT"] = str(CEROOT)


def tool_use(uid: str, name: str, inp: dict[str, object]) -> dict[str, object]:
    return {"type": "tool_use", "id": uid, "name": name, "input": inp}


def assistant(*items: dict[str, object]) -> dict[str, object]:
    return {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": list(items)}}


def result(uid: str, text: str, is_error: bool = False) -> dict[str, object]:
    return {
        "type": "user",
        "isSidechain": True,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": uid, "content": text, "is_error": is_error}],
        },
    }


def edit(path: str) -> list[dict[str, object]]:
    uid = f"toolu_{uuid.uuid4().hex[:12]}"
    return [
        assistant(tool_use(uid, "Edit", {"file_path": path, "old_string": "a", "new_string": "b"})),
        result(uid, "ok"),
    ]


def bash(cmd: str, out: str, ok: bool = True) -> list[dict[str, object]]:
    uid = f"toolu_{uuid.uuid4().hex[:12]}"
    return [assistant(tool_use(uid, "Bash", {"command": cmd})), result(uid, out, is_error=not ok)]


def make_session(records: list[dict[str, object]]) -> tuple[Path, str]:
    session = uuid.uuid4().hex
    agent_id = f"a{uuid.uuid4().hex[:16]}"
    parent = PROJECT / f"{session}.jsonl"
    parent.write_text("", encoding="utf-8")
    sub = PROJECT / session / "subagents" / f"agent-{agent_id}.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return parent, agent_id


def stop_event(parent: Path, agent_id: str, message: str, **extra: object) -> dict[str, object]:
    return {
        "hook_event_name": "SubagentStop",
        "session_id": parent.stem,
        "transcript_path": str(parent),
        "cwd": str(ROOT),
        "agent_id": agent_id,
        "agent_type": "implement",
        "last_assistant_message": message,
        **extra,
    }


CLAIM = "CHANGED: src/x.py (+3/-1)\nRAN: `pytest tests -q` -> 12 passed\nDONE MEANS: met\nRISKS: none"
EU_TY = "EU_env/.venv/Scripts/ty.exe check --python EU_env/.venv OBF/src"
EU_RUFF = 'cd "c:\\repo\\EU\\OBF_ops" && "C:\\repo\\EU\\EU_env\\.venv\\Scripts\\ruff.exe" check src/'
EU_PYTEST = (
    "PYTHONIOENCODING=utf-8 ./EU_env/.venv/Scripts/python.exe -m pytest OBF/src/OBF/Tests/unit_tests -q 2>&1 | tail -5"
)

p, a = make_session([*edit("src/x.py"), *bash("pytest tests -q", "12 passed in 0.4s")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("edit + passing pytest + claim -> allow", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("claim without any validation run -> block", rc, BLOCK, err)
expect_true("  ... reason names the missing run", "no test/typecheck/lint command" in err, err)

p, a = make_session([*edit("src/x.py"), *bash("pytest tests -q", "Exit code 1\n1 failed, 11 passed", ok=False)])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("claim contradicted by a failed run -> block", rc, BLOCK, err)
expect_true("  ... reason quotes the failing command", "pytest tests -q" in err, err)

p, a = make_session(
    [*edit("src/x.py"), *bash(EU_PYTEST, "FAILED tests/test_a.py::test_x\n1 failed, 11 passed in 2.1s")]
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("piped pytest (exit code lost) with failures in the text -> block", rc, BLOCK, err)

p, a = make_session([*edit("src/x.py"), *bash("pytest tests -q", "Exit code 1\n1 failed, 11 passed", ok=False)])
rc, err = run_hook(
    EVIDENCE,
    stop_event(
        p,
        a,
        "CHANGED: src/x.py\nRAN: `pytest tests -q` -> 1 failed, 11 passed\n"
        "DONE MEANS: not met\nRISKS: test_x still failing",
    ),
)
expect("honest failure report -> allow", rc, ALLOW, err)

for phrasing in (
    "RAN: `pytest tests -q` -> 12/12",
    "RAN: pytest -> no failures",
    "RAN: pytest -> exit 0",
    "Everything works and is ready to merge.",
):
    p, a = make_session([*edit("src/x.py"), *bash("pytest tests -q", "Exit code 1\n1 failed, 11 passed", ok=False)])
    rc, err = run_hook(EVIDENCE, stop_event(p, a, "CHANGED: src/x.py\n" + phrasing))
    expect(f"failed run + evasive wording {phrasing[:34]!r} -> block", rc, BLOCK, err)

p, a = make_session(
    [
        *edit("src/x.py"),
        *bash("pytest tests -q", "Exit code 1\n1 failed", ok=False),
        *bash("ruff check src", "All checks passed!"),
    ]
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("failed pytest then passing ruff: last TEST run still failed -> block", rc, BLOCK, err)

p, a = make_session(
    [*edit("src/x.py"), *bash("ruff check src", "Exit code 1\nF401", ok=False), *bash("pytest tests -q", "12 passed")]
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect(
    "failed lint then passing tests, message claims tests -> allow (lint failure must be reported though)",
    rc,
    BLOCK,
    err,
)
rc, err = run_hook(
    EVIDENCE, stop_event(p, a, CLAIM + "\nruff check still failing: F401 in src/x.py", stop_hook_active=False)
)
expect("  ... same transcript, lint failure reported -> allow", rc, ALLOW, err)

p, a = make_session(
    [*edit("OBF/src/OBF/x.py"), *bash(EU_TY, "All checks passed!"), *bash(EU_RUFF, "All checks passed!")]
)
rc, err = run_hook(
    EVIDENCE,
    stop_event(
        p,
        a,
        "CHANGED: OBF/src/OBF/x.py\nRAN: ty.exe check -> clean; ruff.exe check -> clean\nDONE MEANS: met\nRISKS: none",
    ),
)
expect("EU's documented ty.exe / ruff.exe commands count as validation -> allow", rc, ALLOW, err)

p, a = make_session(
    [
        *edit("src/x.py"),
        *bash("grep -rn pytest pyproject.toml", "pyproject.toml:12: pytest"),
        *bash("cat tox.ini", "[tox]"),
        *bash("pytest --collect-only -q", "12 tests collected"),
        *bash("pytest --version", "pytest 8.3"),
    ]
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("mentioning or inspecting a tool is not running it -> block", rc, BLOCK, err)

p, a = make_session([*edit("src/x.py"), *bash("ruff format src", "3 files reformatted")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, "CHANGED: src/x.py\nRAN: `ruff format src` -> ok\nDONE MEANS: met"))
expect("`ruff format` (a rewrite, not a check) is not validation -> block", rc, BLOCK, err)

p, a = make_session(
    [
        *edit("src/x.py"),
        *bash("uv run pytest -q", "12 passed"),
        *bash("uvx ty check src", "All checks passed!"),
        *bash('"C:/py/python.exe" -m ruff check src', "All checks passed!"),
    ]
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("uv run / uvx / python.exe -m forms are recognised -> allow", rc, ALLOW, err)

# A test script or the lint hook run directly with an interpreter is a validation run
# (this repo's own suites are `python tests/test-*.py`); an arbitrary script is not.
p, a = make_session(
    [
        *edit("src/x.py"),
        *bash('"C:/py/python.exe" tests/test-hooks.py', "All hook tests passed."),
        *bash("python scripts/pycheck.py src/x.py", "pycheck: no findings"),
    ]
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("python tests/test-*.py and python .../pycheck.py count as runs -> allow", rc, ALLOW, err)
p, a = make_session([*edit("src/x.py"), *bash("python scripts/build_docs.py", "done")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("python <arbitrary script>.py is not validation -> block", rc, BLOCK, err)
p, a = make_session([*edit("src/x.py"), *bash("python tests/test-hooks.py", "Exit code 1\nFAILED (1):", ok=False)])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("failed python tests/test-*.py run + success claim -> block", rc, BLOCK, err)

p, a = make_session([*edit("src/x.py"), *edit("src/y.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, "Refactored the loader. CHANGED: src/x.py, src/y.py"))
expect("edits, no run, no disclaimer -> block", rc, BLOCK, err)
expect_true("  ... reason lists the files", "src/x.py" in err and "src/y.py" in err, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(
    EVIDENCE,
    stop_event(p, a, "CHANGED: src/x.py\nRAN: nothing - validation not run, no test env here\nRISKS: unverified"),
)
expect("edits, no run, explicit disclaimer -> allow", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(
    EVIDENCE, stop_event(p, a, "CHANGED: src/x.py\nRAN: nothing\nDONE MEANS: tests pass -> not checked (no env)")
)
expect("restated predicate with a negative outcome is not a claim -> allow", rc, ALLOW, err)

p, a = make_session([*edit("docs/README.md")])
rc, err = run_hook(
    EVIDENCE,
    stop_event(
        p, a, "CHANGED: docs/README.md\nRAN: nothing to run - documentation-only change\nDONE MEANS: met by inspection"
    ),
)
expect("doc-only edit with 'nothing to run' -> allow", rc, ALLOW, err)

p, a = make_session([*bash("git log --oneline -3", "abc fix\ndef feat")])
rc, err = run_hook(
    EVIDENCE,
    stop_event(
        p, a, "I verified the load-bearing claims against the code; tests pass on main per CI. Found it at src/x.py:42."
    ),
)
expect("read-only brief that edited nothing -> allow, whatever it says", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, "Done. No type errors."))
expect("'no type errors' after edits without a run -> block", rc, BLOCK, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM, stop_hook_active=True))
expect("stop_hook_active (the runtime's re-stop after a block) -> allow", rc, ALLOW, err)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("  ... and no state leaks: the next plain stop is judged again -> block", rc, BLOCK, err)

p, a = make_session([*edit("src/x.py")])
sub = PROJECT / p.stem / "subagents" / f"agent-{a}.jsonl"
explicit = PROJECT / "elsewhere.jsonl"
shutil.copy(sub, explicit)
sub.unlink()
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM, agent_transcript_path=str(explicit)))
expect("agent_transcript_path is preferred over the derived path -> block", rc, BLOCK, err)

rc, err = run_hook(EVIDENCE, stop_event(PROJECT / "nope.jsonl", "a0000000000000000", CLAIM))
expect("transcript missing -> fail open", rc, ALLOW, err)
expect_true("  ... but says so on stderr", "transcript not found" in err, err)

rc, err = run_hook(EVIDENCE, {"hook_event_name": "SubagentStop", "agentId": "x", "last_assistant_message": CLAIM})
expect("renamed payload field -> fail open", rc, ALLOW, err)
expect_true("  ... and says which field is missing", "payload lacks ['agent_id']" in err, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM), bom=True)
expect("BOM-prefixed payload still blocks", rc, BLOCK, err)

proc = subprocess.run([sys.executable, str(EVIDENCE)], input=b"\xef\xbb\xbfnot json", capture_output=True)
expect("garbage payload fails open", proc.returncode, ALLOW, proc.stderr.decode())

# List-form tool_result content (the API's other shape) is understood.
uid = "toolu_list"
p, a = make_session(
    [
        *edit("src/x.py"),
        assistant(tool_use(uid, "Bash", {"command": "pytest -q"})),
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": uid,
                        "content": [{"type": "text", "text": "Exit code 1\n2 failed"}],
                        "is_error": True,
                    }
                ],
            },
        },
    ]
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("list-form tool_result with a failure -> block", rc, BLOCK, err)

# A heredoc body is data, not commands: writing a test file that mentions pytest is not a pytest run.
heredoc_cmd = (
    "cat > tests/test_new.py <<'EOF'\nimport subprocess\n\n\ndef test_x():\n"
    "    assert subprocess.run(['pytest', '-q'])\nEOF\necho written"
)
p, a = make_session([*edit("src/x.py"), *bash(heredoc_cmd, "written")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("pytest inside a heredoc body is not a run -> claim of a run is bounced", rc, BLOCK, err)
heredoc_then_run = heredoc_cmd + " && pytest tests -q"
p, a = make_session([*edit("src/x.py"), *bash(heredoc_then_run, "12 passed in 0.3s")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("... but a real pytest after the heredoc counts", rc, ALLOW, err)


# --lead: the lead's own final message - its turn's edits and runs decide rules A and C, the session's runs back claims.
def lead_session(*turns: tuple[str, str, list[dict[str, object]]]) -> Path:
    path = PROJECT / f"lead-{uuid.uuid4().hex[:8]}.jsonl"
    records: list[dict[str, object]] = []
    for pid, prompt, body in turns:
        records.append({"type": "user", "promptId": pid, "message": {"role": "user", "content": prompt}})
        for r in body:
            if r.get("type") == "user":
                r["promptId"] = pid
        records.extend(body)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def lead_event(path: Path, pid: str | None, message: str, **extra: object) -> dict[str, object]:
    ev: dict[str, object] = {
        "hook_event_name": "Stop",
        "session_id": "s",
        "transcript_path": str(path),
        "cwd": str(ROOT),
        "last_assistant_message": message,
        **extra,
    }
    if pid:
        ev["prompt_id"] = pid
    return ev


ls = lead_session(("t1", "fix it", [*edit("src/x.py"), *bash("pytest tests -q", "Exit code 1\n1 failed", ok=False)]))
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "Fixed the loader; all good."), "--lead")
expect("lead: turn edited, last run failed, message silent -> block", rc, BLOCK, err)
rc, err = run_hook(
    EVIDENCE, lead_event(ls, "t1", "Fixed the loader; pytest still fails on test_x, investigating."), "--lead"
)
expect("lead: same, failure reported -> allow", rc, ALLOW, err)
ls = lead_session(("t1", "fix it", [*edit("src/x.py")]))
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "Done, updated the loader."), "--lead")
expect("lead: turn edited, ran nothing, no disclaimer -> block", rc, BLOCK, err)
rc, err = run_hook(
    EVIDENCE, lead_event(ls, "t1", "Updated the loader; I did not run the tests (docs-only change)."), "--lead"
)
expect("lead: same with a disclaimer -> allow", rc, ALLOW, err)
ls = lead_session(("t1", "how are the tests?", []))
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "All tests pass."), "--lead")
expect("lead: conversation-only turn claiming a pass, no run in the session -> block", rc, BLOCK, err)
ls = lead_session(("t0", "run tests", [*bash("pytest tests -q", "12 passed")]), ("t1", "so?", []))
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "All 12 tests pass."), "--lead")
expect("lead: claim backed by an earlier run in the session -> allow", rc, ALLOW, err)
ls = lead_session(("t0", "run tests", [*bash("pytest tests -q", "Exit code 1\n2 failed", ok=False)]), ("t1", "so?", []))
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "Tests pass now."), "--lead")
expect("lead: claim while the session's last run failed -> block", rc, BLOCK, err)
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "Here is a summary of the design."), "--lead")
expect("lead: conversation-only turn, no claim -> allow", rc, ALLOW, err)
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "Tests pass now.", stop_hook_active=True), "--lead")
expect("lead: re-emitted stop -> allow", rc, ALLOW, err)
rc, err = run_hook(EVIDENCE, lead_event(PROJECT / "missing.jsonl", "t1", "All tests pass."), "--lead")
expect("lead: missing transcript fails open", rc, ALLOW, err)
ls = lead_session(("t1", "fix it", [*edit("src/x.py")]))
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "Done."))
expect("lead: a Stop payload without agent_id is judged as the lead even without --lead", rc, BLOCK, err)

# reports_failure(): a conditional near the word makes it a plan, not a report of a failure.
ce = load_module(EVIDENCE, "check_evidence_mod")
for text, want in [
    ("pytest still fails on test_x", True),
    ("The build fails.", True),
    ("2 tests failed", True),
    ("DONE MEANS: not met", True),
    ("retry if it fails", False),
    ("ping me when it fails", False),
    ("it should fail loudly", False),
    ("no failures", False),
    ("0 failed", False),
    ("without failures", False),
    ("12 passed", False),
]:
    expect_true(f"reports_failure({text!r}) == {want}", ce.reports_failure(text) is want, text)

# ----------------------------------------------------------- check-evidence from the witness file
# Everything above ran with an empty EVIDENCE_ROOT, i.e. through the transcript fallback. These cases put a
# witness file (scripts/witness.py's format) where the hook looks for one; the transcript then loses every
# disagreement, because it is the file the docs call internal and "written asynchronously".
print()
print("check-evidence.py - the witness file is preferred over the transcript")
WPROMPT = "prompt-1"


def wline(
    tool: str,
    *,
    command: str | None = None,
    file_path: str | None = None,
    text: str = "",
    event: str = "PostToolUse",
    error: str | None = None,
    interrupted: bool = False,
    exit_code: int | None = None,
    prompt_id: str | None = WPROMPT,
) -> dict[str, object]:
    """One witness line, every schema key present - the shape the witness section below pins.

    The ids that name the file (`session_id`, `agent_id`, `agent_type`) are stamped on by `witness_for`,
    which knows them; what is set here is what the producer sets for a lead call.
    """
    return {
        "v": 1,
        "t": "2026-09-15T12:00:00Z",
        "event": event,
        "session_id": "s",
        "prompt_id": prompt_id,
        "agent_id": None,
        "agent_type": None,
        "tool_use_id": f"toolu_{uuid.uuid4().hex[:8]}",
        "tool": tool,
        "command": command,
        "file_path": file_path,
        "exit_code": exit_code,
        "interrupted": interrupted,
        "text": text,
        "error": error,
    }


def wedit(path: str, prompt_id: str | None = WPROMPT) -> dict[str, object]:
    return wline("Edit", file_path=path, prompt_id=prompt_id)


def wrun(command: str, text: str, prompt_id: str | None = WPROMPT) -> dict[str, object]:
    return wline("Bash", command=command, text=text, prompt_id=prompt_id)


def wfail(command: str, error: str, prompt_id: str | None = WPROMPT) -> dict[str, object]:
    """A non-zero exit: witness records it as PostToolUseFailure with the output in `error` and `text`."""
    return wline("Bash", command=command, event="PostToolUseFailure", text=error, error=error, prompt_id=prompt_id)


def witness_for(session: str, agent: str | None, lines: list[object]) -> Path:
    """Write a witness file where check-evidence looks for this session's calls; a str line is written raw.

    The ids the file name comes from are stamped onto every dict line, because that is what witness.py writes:
    a fixture whose lines carry other ids than its own path is not the producer's shape (see `wline`).
    """
    path = CEROOT / session / (f"witness-{agent}.jsonl" if agent else "witness-lead.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ""
    for item in lines:
        if isinstance(item, str):
            body += item + "\n"
            continue
        record = dict(item) if isinstance(item, dict) else {}
        record["session_id"] = session
        record["agent_id"] = agent
        record["agent_type"] = "implement" if agent else None
        body += json.dumps(record) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("transcript has the edit but not the run (the lag) -> block", rc, BLOCK, err)
witness_for(p.stem, a, [wedit("src/x.py"), wrun("pytest tests -q", "12 passed in 0.4s")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("  ... same transcript, but the witness file has the run -> allow", rc, ALLOW, err)
expect_true("  ... and the fallback is silent: nothing on stderr", err == "", err)

p, a = make_session([*edit("src/x.py"), *bash("pytest tests -q", "Exit code 1\n1 failed", ok=False)])
witness_for(p.stem, a, [wedit("src/x.py"), wrun("pytest tests -q", "12 passed in 0.4s")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("transcript says failed, witness says passed -> the witness file wins -> allow", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py"), *bash("pytest tests -q", "12 passed in 0.4s")])
witness_for(p.stem, a, [wedit("src/x.py"), wfail("pytest tests -q", "Exit code 1\n1 failed, 2 passed")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("transcript says passed, a Failure event says otherwise -> block", rc, BLOCK, err)
expect_true("  ... the reason quotes the run the witness file recorded", "pytest tests -q" in err, err)

p, a = make_session([*edit("src/x.py")])
cut_off = wline("Bash", command="pytest tests -q", text="12 passed", interrupted=True)
witness_for(p.stem, a, [wedit("src/x.py"), cut_off])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("an interrupted run is not a pass, whatever its output already said -> block", rc, BLOCK, err)

p, a = make_session([*edit("src/x.py")])
witness_for(p.stem, a, [wedit("src/x.py"), wrun("pytest tests -q", "12 passed in 0.4s")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("a success with exit_code null is a pass (Bash sends none) -> allow", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py")])
witness_for(
    p.stem,
    a,
    [
        wedit("src/x.py"),
        '{"v":1,"event":"PostToolUse","tool":"Bas',
        json.dumps({"tool": "Bash", "command": "pytest tests -q"}),
        wrun("pytest tests -q", "12 passed in 0.4s"),
    ],
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("a torn line and a line without the schema keys are skipped; the valid ones decide -> allow", rc, ALLOW, err)

# A write that died mid-line left no newline, so the next append is glued onto it: one physical line holding
# a fragment and then a whole record. Losing both would lose the run this claim rests on.
p, a = make_session([*edit("src/x.py")])
torn = '{"v":1,"t":"2026-09-15T12:00:00Z","event":"PostToolUse","tool":"Bash","command":"pytest tests -q","te'
whole = json.dumps({**wrun("pytest tests -q", "12 passed in 0.4s"), "session_id": p.stem, "agent_id": a})
witness_for(p.stem, a, [wedit("src/x.py"), torn + whole])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("a torn line glued to the next one: the whole record after it is still read -> allow", rc, ALLOW, err)

# A file that exists but yields no parseable line is BROKEN, not "this agent did nothing": treating [] as
# evidence would allow every claim (nothing edited) and block the lead (nothing ran). It falls back instead.
failing = [*edit("src/x.py"), *bash("pytest tests -q", "Exit code 1\n1 failed", ok=False)]
p, a = make_session(failing)
witness_for(p.stem, a, [])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("an empty witness file is broken, not evidence -> the transcript decides -> block", rc, BLOCK, err)
p, a = make_session(failing)
witness_for(p.stem, a, ["not json at all", "{}", '{"tool":"Bash"}'])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("a witness file of only unparseable lines -> the same fallback -> block", rc, BLOCK, err)

# Reading it can also fail outright. That is a broken state, not the routine absence the silence rule covers.
p, a = make_session(failing)
witness_for(p.stem, a, [wedit("src/x.py")])


def unreadable(path: Path) -> list[dict[str, object]]:
    raise OSError("access denied")


real_iter, ce.iter_witness = ce.iter_witness, unreadable
notice = io.StringIO()
with contextlib.redirect_stderr(notice):
    broken = ce.witness_lines({"session_id": p.stem, "agent_id": a})
ce.iter_witness = real_iter
expect_true(
    "an unreadable witness file -> fall back, saying so once on stderr",
    broken is None and "witness file unreadable" in notice.getvalue(),
    notice.getvalue(),
)
silent = io.StringIO()
with contextlib.redirect_stderr(silent):
    absent = ce.witness_lines({"session_id": "no-such-session", "agent_id": a})
expect_true(
    "... while an absent one falls back in silence (a session without the hook wired)",
    absent is None and silent.getvalue() == "",
    silent.getvalue(),
)

p, a = make_session([])
witness_for(p.stem, a, [wedit("src/only-in-witness.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, "Done."))
expect("a witness Edit line is a confirmed edit: changed, ran nothing -> block", rc, BLOCK, err)
expect_true("  ... naming the file only the witness file knew about", "src/only-in-witness.py" in err, err)
witness_for(p.stem, a, [wedit("src/only-in-witness.py"), wrun("pytest tests -q", "12 passed")])
rc, err = run_hook(
    EVIDENCE,
    stop_event(p, a, "CHANGED: src/only-in-witness.py (+2/-0)\nRAN: `pytest tests -q` -> 12 passed\nDONE MEANS: met"),
)
expect("  ... with a run behind it, the CHANGED claim naming that file is accepted", rc, ALLOW, err)

# --lead: the turn is the witness lines carrying the payload's prompt_id; the session is the whole file.
ls = lead_session(("t1", "fix it", [*edit("src/x.py")]), ("t2", "so?", []))
witness_for("leadses1", None, [wedit("src/x.py", "t1"), wrun("pytest tests -q", "12 passed", "t1")])
rc, err = run_hook(EVIDENCE, lead_event(ls, "t2", "All 12 tests pass.", session_id="leadses1"), "--lead")
expect("lead: claim in turn 2 backed by turn 1's run in the same file -> allow", rc, ALLOW, err)
expect_true("  ... silently, without touching the transcript that shows no run", err == "", err)

witness_for(
    "leadses2",
    None,
    [wedit("src/x.py", "t1"), wrun("pytest tests -q", "12 passed", "t1"), wedit("src/y.py", "t2")],
)
rc, err = run_hook(EVIDENCE, lead_event(ls, "t2", "Updated the loader.", session_id="leadses2"), "--lead")
expect("lead: turn 2 edited and ran nothing, turn 1's run does not count for rule C -> block", rc, BLOCK, err)
expect_true("  ... naming this turn's file only", "src/y.py" in err and "src/x.py" not in err, err)
rc, err = run_hook(EVIDENCE, lead_event(ls, None, "Updated the loader.", session_id="leadses2"), "--lead")
expect("lead: no prompt_id in the payload -> an empty turn, not the previous one's edits -> allow", rc, ALLOW, err)
rc, err = run_hook(EVIDENCE, lead_event(ls, None, "All 12 tests pass.", session_id="leadses2"), "--lead")
expect("  ... the session's runs still back (or bounce) a claim in such a turn -> allow", rc, ALLOW, err)
witness_for("leadses4", None, [wedit("src/x.py", "t1"), wfail("pytest tests -q", "Exit code 1\n1 failed", "t1")])
rc, err = run_hook(EVIDENCE, lead_event(ls, None, "All 12 tests pass.", session_id="leadses4"), "--lead")
expect("  ... and a claim over the session's last failed run is still blocked", rc, BLOCK, err)

ls = lead_session(("t1", "fix it", [*edit("src/x.py"), *bash("pytest tests -q", "Exit code 1\n1 failed", ok=False)]))
witness_for("leadses3", "someagent", [wrun("pytest tests -q", "12 passed", "t1")])
rc, err = run_hook(EVIDENCE, lead_event(ls, "t1", "Fixed the loader; all good.", session_id="leadses3"), "--lead")
expect("lead: no witness-lead.jsonl (an agent file is not one) -> the transcript decides -> block", rc, BLOCK, err)

wit = load_module(WITNESS, "witness_mod")
expect_true(
    "check-evidence and witness agree on EVIDENCE_ROOT, its default and the id pattern",
    (ce.EVIDENCE_ROOT_ENV, ce.DEFAULT_EVIDENCE_ROOT, ce.ID_OK.pattern)
    == (wit.EVIDENCE_ROOT_ENV, wit.DEFAULT_ROOT, wit.ID_OK.pattern),
    f"{ce.DEFAULT_EVIDENCE_ROOT} vs {wit.DEFAULT_ROOT}",
)

# End to end: the real witness.py writes, the real check-evidence.py reads. This is the contract between them.
E2E_SESSION, E2E_AGENT = "e2esession", "e2eagent"


def witness_payload(tool: str, *, event: str = "PostToolUse", **fields: object) -> dict[str, object]:
    """A PostToolUse / PostToolUseFailure payload in the shape measured on 2.1.272 (see capabilities.md)."""
    return {
        "hook_event_name": event,
        "session_id": E2E_SESSION,
        "prompt_id": "p-e2e",
        "agent_id": E2E_AGENT,
        "agent_type": "implement",
        "tool_use_id": f"toolu_{uuid.uuid4().hex[:8]}",
        "tool_name": tool,
        "cwd": str(ROOT),
        "tool_input": {},
        **fields,
    }


E2E_STOP = stop_event(
    PROJECT / "no-such-session.jsonl",
    E2E_AGENT,
    "CHANGED: src/e2e.py (+1/-0)\nRAN: `pytest -q` -> 3 passed\nDONE MEANS: met\nRISKS: none",
    session_id=E2E_SESSION,
)
rc, err = run_hook(
    WITNESS,
    witness_payload(
        "Edit",
        tool_input={"file_path": "src/e2e.py", "old_string": "a", "new_string": "b"},
        tool_response={"type": "update", "filePath": "src/e2e.py"},
    ),
)
expect("e2e: witness.py records the Edit", rc, ALLOW, err)
rc, err = run_hook(
    WITNESS,
    witness_payload(
        "Bash",
        tool_input={"command": "pytest -q"},
        tool_response={"stdout": "3 passed in 0.2s", "stderr": "", "interrupted": False, "isImage": False},
    ),
)
expect("e2e: witness.py records the passing run", rc, ALLOW, err)
rc, err = run_hook(EVIDENCE, E2E_STOP)
expect("e2e: check-evidence reads what witness wrote -> the honest report is allowed", rc, ALLOW, err)
expect_true("  ... with no notice: the transcript was never needed (it does not exist)", err == "", err)
rc, err = run_hook(
    WITNESS,
    witness_payload(
        "Bash",
        event="PostToolUseFailure",
        tool_input={"command": "pytest -q"},
        error="Exit code 1\ncollected 3 items\n1 failed, 2 passed",
        is_interrupt=False,
    ),
)
expect("e2e: witness.py records the later failure", rc, ALLOW, err)
rc, err = run_hook(EVIDENCE, E2E_STOP)
expect("e2e: that failure is now the last test run -> the same claim is blocked", rc, BLOCK, err)
expect_true("  ... quoting what witness captured", "1 failed" in err, err)
rc, err = run_hook(
    EVIDENCE,
    {**E2E_STOP, "last_assistant_message": "CHANGED: src/e2e.py\nRAN: `pytest -q` -> 1 failed\nDONE MEANS: not met"},
)
expect("e2e: the honest report of that failure is allowed", rc, ALLOW, err)

# --------------------------------------------------------------------------- no-ask
print()
print("no-ask.py - PreToolUse: AskUserQuestion blocked under /lost-mary")
NA = SCRATCH / "no-ask"
NA.mkdir(parents=True)


def command(name: str, args: str = "") -> dict[str, object]:
    """A slash-command turn as Claude Code 2.1.260 records it (observed shape)."""
    text = (
        f"<command-name>/{name}</command-name>\n"
        f"            <command-message>{name}</command-message>\n"
        f"            <command-args>{args}</command-args>"
    )
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}}


def prose(text: str) -> dict[str, object]:
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}}


def session_file(records: list[dict[str, object]]) -> Path:
    path = NA / f"{uuid.uuid4().hex}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def ask_event(transcript: Path, tool: str = "AskUserQuestion") -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": transcript.stem,
        "transcript_path": str(transcript),
        "cwd": str(ROOT),
        "tool_name": tool,
        "tool_input": {"questions": [{"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]},
    }


t = session_file([prose("fix the bug in x.py"), *edit("src/x.py")])
rc, err = run_hook(NOASK, ask_event(t))
expect("no /lost-mary in the session -> allow", rc, ALLOW, err)

t = session_file([command("lost-mary", "fix the bug in x.py"), *edit("src/x.py")])
rc, err = run_hook(NOASK, ask_event(t))
expect("/lost-mary invoked -> block", rc, BLOCK, err)
expect_true(
    "block message says decide, record an Assumption, plain text for the rest",
    "Assumption" in err and "plain text" in err,
    err,
)


# A skill invocation is recorded with <command-message> BEFORE <command-name> (observed 2.1.260); the live
# check on 2026-09-06 slipped through because only a leading <command-name> was recognised.
def skill_command(name: str, args: str = "") -> dict[str, object]:
    text = f"<command-message>{name}</command-message>" + chr(10) + f"<command-name>/{name}</command-name>" + chr(10)
    text += f"<command-args>{args}</command-args>" + chr(10) + "Base directory for this skill: ~/.claude/skills/" + name
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}}


t = session_file([skill_command("lost-mary", "due diligence"), *edit("src/x.py")])
rc, err = run_hook(NOASK, ask_event(t))
expect("skill-style invocation (command-message first) -> block", rc, BLOCK, err)
t = session_file([prose("Some prose that mentions <command-name>/lost-mary</command-name> in passing")])
rc, err = run_hook(NOASK, ask_event(t))
expect("the tag inside ordinary prose is still not an invocation -> allow", rc, ALLOW, err)

t = session_file([command("lost-mary", "a"), *edit("src/x.py"), command("clear"), prose("now something else")])
rc, err = run_hook(NOASK, ask_event(t))
expect("/lost-mary then /clear -> allow", rc, ALLOW, err)

t = session_file([command("lost-mary", "a"), command("clear"), command("lost-mary", "b")])
rc, err = run_hook(NOASK, ask_event(t))
expect("/clear then /lost-mary again -> block", rc, BLOCK, err)

t = session_file(
    [
        prose("please run <command-name>/lost-mary</command-name> later"),
        assistant(tool_use("t1", "Bash", {"command": "echo '<command-name>/lost-mary</command-name>'"})),
    ]
)
rc, err = run_hook(NOASK, ask_event(t))
expect("the tag quoted in prose or in a tool call is not an invocation -> allow", rc, ALLOW, err)

t = session_file([{"type": "user", "message": "<command-name>/lost-mary</command-name>"}, command("lost-mary", "a")])
rc, err = run_hook(NOASK, ask_event(t))
expect("a malformed record (message is a string) is skipped, the real invocation still counts -> block", rc, BLOCK, err)

t = session_file([command("lost-mary", "a")])
rc, err = run_hook(NOASK, ask_event(t, tool="Bash"))
expect("another tool under /lost-mary -> allow", rc, ALLOW, err)

rc, err = run_hook(NOASK, ask_event(NA / "missing.jsonl"))
expect("missing transcript fails open", rc, ALLOW, err)
expect_true("... and names the transcript", "transcript" in err, err)

rc, err = run_hook(NOASK, {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"})
expect("no transcript_path fails open", rc, ALLOW, err)

t = session_file([command("lost-mary", "a")])
rc, err = run_hook(NOASK, ask_event(t), bom=True)
expect("BOM-prefixed payload still blocks", rc, BLOCK, err)

proc = subprocess.run([sys.executable, str(NOASK)], input=b"\xef\xbb\xbfnot json", capture_output=True)
expect("garbage payload fails open", proc.returncode, ALLOW, proc.stderr.decode())

# ------------------------------------------------------------------------ check-spawn
print()
print("check-spawn.py - PreToolUse on Agent: an implement spawn carries its contract or does not start")


def spawn(role: str, prompt: str, tool: str = "Agent") -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_use_id": "toolu_spawn",
        "tool_input": {"subagent_type": role, "description": "Do the thing", "prompt": prompt},
    }


FULL = "GOAL: fix it" + chr(10) + "OWNED: src/x.py" + chr(10) + "OFF-LIMITS: tests/**" + chr(10)
FULL += (
    "DONE MEANS: pytest tests -q -> 12 passed"
    + chr(10)
    + "VALIDATION: pytest tests -q"
    + chr(10)
    + "REPORT: CHANGED / RAN"
)
rc, err = run_hook(SPAWN, spawn("implement", FULL))
expect("implement with all four fields -> allow", rc, ALLOW, err)
rc, err = run_hook(SPAWN, spawn("implement", "OWNED: src/x.py" + chr(10) + "DONE MEANS: tests pass"))
expect("implement missing two fields -> block", rc, BLOCK, err)
expect_true(
    "... names exactly the missing ones, in order",
    "MISSING: OFF-LIMITS, VALIDATION" in err and "OWNED" not in err.split(chr(10))[0],
    err,
)
decorated = (
    "**OWNED:** src/"
    + chr(10)
    + "- OFF-LIMITS: none"
    + chr(10)
    + "## DONE MEANS: met"
    + chr(10)
    + "  validation: pytest -q"
)
rc, err = run_hook(SPAWN, spawn("implement", decorated))
expect("bold, bulleted, heading or lower-case field lines all count -> allow", rc, ALLOW, err)
rc, err = run_hook(SPAWN, spawn("implement", "the owned files are src/x.py and validation is pytest"))
expect("field names in prose without a colon do not count -> block", rc, BLOCK, err)
rc, err = run_hook(SPAWN, spawn("explore", "Find where the loader parses dates."))
expect("explore has no contract -> allow", rc, ALLOW, err)
rc, err = run_hook(SPAWN, spawn("review", "Review this diff."))
expect("review has no contract -> allow", rc, ALLOW, err)
rc, err = run_hook(SPAWN, spawn("implement", "OWNED: x", tool="Bash"))
expect("another tool -> allow", rc, ALLOW, err)
rc, err = run_hook(SPAWN, {"hook_event_name": "PreToolUse", "tool_name": "Agent"})
expect("no tool_input fails open", rc, ALLOW, err)
rc, err = run_hook(SPAWN, spawn("implement", "OWNED: x"), bom=True)
expect("BOM-prefixed payload still blocks", rc, BLOCK, err)
proc = subprocess.run([sys.executable, str(SPAWN)], input=b"not json", capture_output=True)
expect("garbage payload fails open", proc.returncode, ALLOW, proc.stderr.decode())

# ----------------------------------------------------------------------------- permit
print()
print("permit.py - PermissionRequest: allow the routine, decide nothing else")
PM = SCRATCH / "permit"
PREPO = PM / "repo"
PREPO.mkdir(parents=True)
git(PREPO, "init", "-q")
git(PREPO, "switch", "-c", "main")
git(PREPO, "commit", "--allow-empty", "-q", "-m", "root")
git(PREPO, "switch", "-c", "feature/t")


def permit_out(tool: str, command: str, cwd: Path | None = None) -> tuple[int, str, str]:
    payload = {"hook_event_name": "PermissionRequest", "tool_name": tool, "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    proc = subprocess.run(
        [sys.executable, str(PERMIT), *()], input=json.dumps(payload).encode("utf-8"), capture_output=True
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def allowed(out: str) -> bool:
    try:
        return json.loads(out)["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    except (ValueError, KeyError, TypeError):
        return False


ALLOW_CASES = [
    "git push origin feature/x",
    "git push -u origin state-ledger",
    "git push origin HEAD:feature/x",
    "cd /repo && EU_env/.venv/Scripts/python.exe -m pytest tests -q 2>&1 | tail -5",
    "ruff check src > out.log 2>&1",
    "./install.sh --verify",
    "powershell -NoProfile -ExecutionPolicy Bypass -File ./install.ps1 -Verify",
    "PYTHONIOENCODING=utf-8 pytest tests -q; echo done",
]
for cmd in ALLOW_CASES:
    rc, out, err = permit_out("Bash", cmd)
    expect_true(f"allow: {cmd[:60]}", rc == 0 and allowed(out), out + err)

NO_DECISION_CASES = [
    "git push origin main",
    "git push origin master",
    "git push --force origin feature/x",
    "git push -f origin feature/x",
    "git push origin :feature/x",
    "git push origin +feature/x",
    "git push --delete origin feature/x",
    "git push --tags",
    "git push --no-verify origin feature/x",
    "git push origin feature/x && git push origin main",
    "pytest -q && rm -rf build",
    "pytest > /etc/passwd",
    "pytest > ~/x.log",
    "echo secret > file",
    "echo hi",
    "./install.sh",
    "python - <<'EOF'" + chr(10) + "pytest -q" + chr(10) + "EOF",
    "sed -i s/a/b/ x.py && pytest -q",
]
for cmd in NO_DECISION_CASES:
    rc, out, err = permit_out("Bash", cmd)
    expect_true(f"no decision: {cmd[:60]!r}", rc == 0 and out == "", out + err)

rc, out, err = permit_out("Bash", "git push", cwd=PREPO)
expect_true("bare git push on a feature branch (resolved from the repo) -> allow", allowed(out), out + err)
rc, out, err = permit_out("Bash", f"git -C {PREPO.as_posix()} push -u origin HEAD")
expect_true("git -C <repo> push HEAD on a feature branch -> allow", allowed(out), out + err)
git(PREPO, "switch", "main")
rc, out, err = permit_out("Bash", "git push", cwd=PREPO)
expect_true("bare git push on main -> no decision", out == "", out + err)
rc, out, err = permit_out("Bash", "git push")
expect_true("bare git push with no cwd -> no decision", out == "", out + err)
git(PREPO, "switch", "feature/t")

rc, out, err = permit_out("PowerShell", "Get-ChildItem; C:/repo/EU/EU_env/.venv/Scripts/python.exe -m pytest -q")
expect_true("PowerShell: read-only cmdlet + venv pytest -> allow", allowed(out), out + err)
rc, out, err = permit_out("PowerShell", "Stop-Process -Id 1; pytest -q")
expect_true("PowerShell: a foreign cmdlet -> no decision", out == "", out + err)
rc, out, err = permit_out("Edit", "git push origin feature/x")
expect_true("not a shell tool -> no decision", out == "", out + err)
proc = subprocess.run([sys.executable, str(PERMIT)], input=b"not json", capture_output=True)
expect("garbage payload -> exit 0, no decision", proc.returncode, ALLOW, proc.stderr.decode())
expect_true("... and nothing on stdout", proc.stdout == b"", proc.stdout.decode())
rc, err = run_hook(
    PERMIT,
    {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "pytest -q"}},
    bom=True,
)
expect("BOM-prefixed payload still decides (exit 0)", rc, ALLOW, err)

# --------------------------------------------------------------------------- no-punt
print()
print("no-punt.py - Stop: a final message that hands work back to the user is bounced once")
NP = SCRATCH / "no-punt"
NP.mkdir(parents=True)


def stop_payload(message: str | None, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"hook_event_name": "Stop", "session_id": "s", "cwd": str(ROOT), **extra}
    if message is not None:
        payload["last_assistant_message"] = message
    return payload


PUNTS = [
    "Fixed the parser. I also noticed the date handling in loader.py is off by one; I'll leave that for you.",
    "Done. The flaky test in test_io.py is left for you to look at.",
    "There is a similar bug in sync.py - you may want to fix that separately.",
    "I did not touch the config validation; that one is for you to handle.",
    "Fixed the parser. I'll leave `loader.py` for you.",
    "The fix is not small so I'll leave that for you.",
    "Everything is recorded. The one item that isn't mine: I'll leave the merge of PR #4 for you.",
    "Done. I'll leave the flaky test for you.",
    "The retry logic is wrong too, but that's up to you to decide.",
]
for i, msg in enumerate(PUNTS):
    rc, err = run_hook(NOPUNT, stop_payload(msg))
    expect(f"hand-back #{i + 1} -> block", rc, BLOCK, err)
expect_true("block message quotes the phrase", "up to you" in err.lower(), err)
expect_true("... and names the three ways to close", "implement" in err and "failing test" in err, err)

OK_MESSAGES = [
    "Fixed the parser. FOUND: loader.py - date handling off by one; fixed in place, test added.",
    "Fixed the parser. The loader bug is in flight: implement agent on branch fix/loader-dst, worktree isolation.",
    "Fixed the parser. Residual risk: the DST path is untested here; a failing test is committed on fix/loader-dst.",
    'The rule says: never "I\'ll leave that for you". Both findings are fixed.',
    "> quoted from the old report: left that for you\n\nBoth items are now fixed.",
    "Left the feature flag default at `false` - see `config.py`.",
    "Left the default timeout in place for now; both bugs are fixed.",
    "Nothing is left as a decision for you - everything I implemented is additive.",
    "No polling, nothing for you to decide; I'll push when they finish.",
    "I'll leave the worktree in place for you to inspect.",
    "I left a note for you in NOTES.md with the commands I ran.",
    "You may want to take a look at the PR description before merging.",
]
for i, msg in enumerate(OK_MESSAGES):
    rc, err = run_hook(NOPUNT, stop_payload(msg))
    expect(f"closing message #{i + 1} -> allow", rc, ALLOW, err)

rc, err = run_hook(NOPUNT, stop_payload(PUNTS[0], stop_hook_active=True))
expect("re-emitted stop (stop_hook_active) -> allow, one strike", rc, ALLOW, err)

# No last_assistant_message: fall back to the transcript's last assistant record.
t = NP / "lead-turn.jsonl"  # a no-punt transcript fixture, not a witness file
t.write_text(
    "\n".join(
        json.dumps(r)
        for r in [
            prose("fix x"),
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Working."}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": PUNTS[1]}]}},
        ]
    )
    + "\n",
    encoding="utf-8",
)
rc, err = run_hook(NOPUNT, stop_payload(None, transcript_path=str(t)))
expect("no last_assistant_message -> read transcript -> block", rc, BLOCK, err)


def streamed(message_id: str, text: str) -> dict[str, object]:
    content = [{"type": "text", "text": text}]
    return {
        "type": "assistant",
        "uuid": uuid.uuid4().hex,
        "message": {"id": message_id, "role": "assistant", "content": content},
    }


t = NP / "lead2.jsonl"
t.write_text(
    "\n".join(
        json.dumps(r)
        for r in [
            streamed("m1", "Working."),
            streamed("m1", PUNTS[1]),
            streamed("m2", "All fixed."),
            streamed("m2", "FOUND: a.py - typo, fixed."),
        ]
    )
    + "\n",
    encoding="utf-8",
)
rc, err = run_hook(NOPUNT, stop_payload(None, transcript_path=str(t)))
expect("fallback judges the last message only, not an earlier turn -> allow", rc, ALLOW, err)
t.write_text(
    "\n".join(
        json.dumps(r) for r in [streamed("m1", "Working."), streamed("m2", PUNTS[1]), streamed("m2", "Let me know.")]
    )
    + "\n",
    encoding="utf-8",
)
rc, err = run_hook(NOPUNT, stop_payload(None, transcript_path=str(t)))
expect("hand-back in an earlier record of the last streamed message -> block", rc, BLOCK, err)

rc, err = run_hook(NOPUNT, stop_payload(None, transcript_path=str(NP / "missing.jsonl")))
expect("no message anywhere fails open", rc, ALLOW, err)
expect_true("... and says so", "no final message" in err, err)

rc, err = run_hook(NOPUNT, stop_payload(PUNTS[0]), bom=True)
expect("BOM-prefixed payload still blocks", rc, BLOCK, err)

proc = subprocess.run([sys.executable, str(NOPUNT)], input=b"\xef\xbb\xbfnot json", capture_output=True)
expect("garbage payload fails open", proc.returncode, ALLOW, proc.stderr.decode())

# --------------------------------------------------------------------------- friction
print()
print("friction.py - read-only report over transcripts and settings")
FR = SCRATCH / "friction"
PROJ_A = FR / "projects" / "c--repo-a"
PROJ_B = FR / "projects" / "c--repo-b"
PROJ_A.mkdir(parents=True)
PROJ_B.mkdir(parents=True)
(PROJ_A / "s1" / "subagents").mkdir(parents=True)


def ask(recommended: bool) -> dict[str, object]:
    label = "Keep (Recommended)" if recommended else "Keep"
    questions = [{"question": "Which?", "options": [{"label": label}, {"label": "Drop"}]}]
    return assistant(tool_use("q1", "AskUserQuestion", {"questions": questions}))


def refused(uid: str, command: str) -> list[dict[str, object]]:
    text = "Permission for this action was denied by the Claude Code auto mode classifier. Reason: Blocked."
    return [assistant(tool_use(uid, "Bash", {"command": command})), result(uid, text, is_error=True)]


def said(text: str, when: str = "") -> dict[str, object]:
    record: dict[str, object] = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    if when:  # every real record carries one; friction dates its findings from it, not from the mtime
        record["timestamp"] = when
    return record


def jsonl(records: list[dict[str, object]]) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


(PROJ_A / "s1.jsonl").write_text(
    jsonl(
        [
            prose("do x"),
            ask(True),
            ask(False),
            *refused("r1", "cd /repo && EU_env/.venv/Scripts/python.exe -m pytest -q"),
            said("Done. I'll leave the flaky test for you.", "2026-09-04T09:12:00.000Z"),
            said("Done. I'll leave the flaky test for you.", "2026-09-04T09:12:01.000Z"),  # streamed - one hand-back
            assistant(
                tool_use(
                    "q9",
                    "AskUserQuestion",
                    {"questions": [{"question": "Menu?", "options": [{"label": "A (Recommended)"}]}]},
                )
            ),
            result("q9", "AskUserQuestion is blocked while this session runs under /lost-mary. Decide.", is_error=True),
            assistant(tool_use("rd", "Read", {"file_path": "capabilities.md"})),
            result("rd", "x" * 400 + "denied by the Claude Code auto mode classifier"),
        ]
    ),
    encoding="utf-8",
)
# A subagent transcript with a question in it must not be counted: subagents cannot ask.
(PROJ_A / "s1" / "subagents" / "agent-a1.jsonl").write_text(jsonl([ask(True)]), encoding="utf-8")
(FR / "projects" / "wf_abc123").mkdir(parents=True)
(FR / "projects" / "wf_abc123" / "w.jsonl").write_text(jsonl([ask(True)]), encoding="utf-8")
(PROJ_B / "s2.jsonl").write_text(
    jsonl(
        [
            prose("do y"),
            *refused("r2", "# restart the run role\n$p = Get-Process -Id 1\nStop-ScheduledTask -TaskName OBF-run"),
            said("All fixed; FOUND: a.py - typo, fixed."),
            said("Shipped. I'll leave the release notes for you.", "2026-09-10T15:30:00.000Z"),
        ]
    ),
    encoding="utf-8",
)
fr_settings = FR / "settings.json"
fr_settings.write_text(
    json.dumps(
        {
            "permissions": {
                "allow": ["Bash(git push *)", 'Bash(python -c "import x")', "Bash(./run.sh --once)", "Read(//c//**)"]
            }
        }
    ),
    encoding="utf-8",
)

fr_args = ["--projects-dir", str(FR / "projects"), "--settings", str(fr_settings)]
proc = subprocess.run([sys.executable, str(FRICTION), *fr_args, "--json"], capture_output=True)
expect("friction --json exits 0", proc.returncode, ALLOW, proc.stderr.decode())
data = json.loads(proc.stdout.decode("utf-8"))
expect_true("two sessions counted; subagent and wf_* transcripts skipped", data["sessions"] == 2, str(data))
expect_true("questions 2, one recommended", data["questions"] == 2 and data["recommended"] == 1, str(data))
expect_true("a menu blocked by no-ask is counted as blocked, not asked", data["blocked"] == 1, str(data))
expect_true(
    "two refusals (a Read that merely mentions a marker is not one), attributed past `cd` to the real command",
    data["refusals"] == 2
    and data["refused_commands"].get("EU_env/.venv/Scripts/python.exe") == 1
    and data["refused_commands"].get("Stop-ScheduledTask") == 1,
    str(data),
)
expect_true("two hand-backs (one streamed twice); the FOUND: line is not one", data["hand_backs"] == 2, str(data))
expect_true(
    "the example names the matched phrase",
    any("leave the flaky test for you" in e for e in data["hand_back_examples"]),
    str(data),
)
# Dating: an undated hand-back reads as a live one. The example carries the record's own date, the most
# recent comes first, and the reported window is the span of the records - not of the files' mtimes.
expect_true(
    "hand-back examples are dated and newest-first",
    data["hand_back_examples"][0].startswith("2026-09-10  [c--repo-b]")
    and data["hand_back_examples"][1].startswith("2026-09-04  [c--repo-a]"),
    str(data["hand_back_examples"]),
)
expect_true(
    "the window comes from the record timestamps, not the file mtimes",
    data["first"] == "2026-09-04" and data["last"] == "2026-09-10",
    f"{data['first']}..{data['last']}",
)
expect_true(
    "dead allow rules: exact Bash entries only",
    data["dead_allow"] == ['Bash(python -c "import x")', "Bash(./run.sh --once)"],
    str(data),
)
expect_true(
    "per-project breakdown",
    data["projects"]["c--repo-a"]["questions"] == 2 and data["projects"]["c--repo-b"]["refusals"] == 1,
    str(data),
)

proc = subprocess.run([sys.executable, str(FRICTION), *fr_args], capture_output=True)
out = proc.stdout.decode("utf-8")
expect("friction text mode exits 0", proc.returncode, ALLOW, proc.stderr.decode())
expect_true(
    "text report has the four headline lines",
    all(k in out for k in ("questions", "refusals", "hand-backs", "allow rules")),
    out,
)

proc = subprocess.run([sys.executable, str(FRICTION), "--projects-dir", str(FR / "nowhere")], capture_output=True)
expect("missing projects dir -> exit 0 with a notice", proc.returncode, ALLOW, proc.stderr.decode())
expect_true("... naming the directory", "nowhere" in proc.stderr.decode(), proc.stderr.decode())

fr_env = {**os.environ, "FRICTION_ROOT": str(FR / "readings")}
proc = subprocess.run([sys.executable, str(FRICTION), *fr_args, "--record", "--json"], capture_output=True, env=fr_env)
expect("friction --record exits 0", proc.returncode, ALLOW, proc.stderr.decode())
saved = list((FR / "readings").glob("*.json"))
expect_true(
    "one reading saved under FRICTION_ROOT",
    len(saved) == 1 and json.loads(saved[0].read_text(encoding="utf-8")).get("questions") == 2,
    str(saved),
)
proc = subprocess.run([sys.executable, str(FRICTION), "--history"], capture_output=True, env=fr_env)
out = proc.stdout.decode("utf-8", "replace")
expect_true(
    "--history lists the saved reading", proc.returncode == 0 and "recorded (UTC)" in out and out.count("\n") >= 1, out
)
# A reading saved by an older version knows none of today's keys. A new column that raises on it would
# take the whole comparison table down, which is the one thing the saved readings exist for.
(FR / "readings" / "20260906T210740Z.json").write_text(
    json.dumps(
        {
            "sessions": 40,
            "first": "2026-09-02",
            "last": "2026-09-06",
            "questions": 9,
            "recommended": 4,
            "blocked": 1,
            "refusals": 12,
            "hand_backs": 8,
            "allow_total": 45,
            "dead_allow": ["Bash(git push)"],
            "refused_commands": {"git": 3},
            "refused_tools": {"Bash": 12},
            "projects": {},
            "hand_back_examples": ["[c--repo-a] 'leave that for you'"],
            "notes": [],
            "recorded_at": "2026-09-06T21:07:40Z",
        }
    ),
    encoding="utf-8",
)
proc = subprocess.run([sys.executable, str(FRICTION), "--history"], capture_output=True, env=fr_env)
out = proc.stdout.decode("utf-8", "replace")
rows = [line for line in out.splitlines() if line and not line.startswith("recorded (UTC)")]
expect("--history over a pre-schema reading exits 0", proc.returncode, ALLOW, proc.stderr.decode())
expect_true(
    "... and renders BOTH rows, the old one included",
    len(rows) == 2 and any(r.startswith("2026-09-06T21:07:40Z") for r in rows) and "unexpected error" not in out,
    out,
)


# Hook firings, in the THREE shapes Claude Code really writes (verified 2026-09-15 against
# ~/.claude/projects on 2.1.260 - 222 blocking errors, 86 successes, 34 additional-context records).
# The keys differ per shape: `hook_blocking_error` carries NO exitCode - the block text and the hook's
# own command line live under `blockingError` - `hook_success` carries command/exitCode/stdout/stderr,
# and `hook_additional_context` carries only `content`. The fixture here used to be an invented
# `{"type": "hook", "exitCode": 2}`, which is why "blocks: exitCode == 2" shipped green while friction
# reported 0 blocks through 222 real ones. Keep these shapes tied to a real transcript.
HOOK_CMD = '"C:/py/python.exe" "C:/Users/x/.claude/agent-library/scripts/{}" {}'  # quoted paths, then args


def blocked_by(event: str, script: str, args: str, text: str, when: str) -> dict[str, object]:
    return {
        "type": "attachment",
        "timestamp": when,
        "attachment": {
            "type": "hook_blocking_error",
            "hookName": f"{event}:x",
            "toolUseID": "toolu_01",
            "hookEvent": event,
            "blockingError": {"blockingError": text, "command": HOOK_CMD.format(script, args)},
        },
    }


def hook_ok(event: str, script: str, args: str, when: str, stderr: str = "") -> dict[str, object]:
    return {
        "type": "attachment",
        "timestamp": when,
        "attachment": {
            "type": "hook_success",
            "hookName": f"{event}:x",
            "toolUseID": "toolu_02",
            "hookEvent": event,
            "command": HOOK_CMD.format(script, args),
            "content": "",
            "durationMs": 21,
            "exitCode": 0,
            "stdout": "",
            "stderr": stderr,
        },
    }


def hook_context(event: str, when: str) -> dict[str, object]:
    return {
        "type": "attachment",
        "timestamp": when,
        "attachment": {
            "type": "hook_additional_context",
            "hookName": event,
            "toolUseID": "toolu_03",
            "hookEvent": event,
            "content": ["Ledger for c--repo-a - what agents actually edited and ran."],
        },
    }


(PROJ_A / "s3.jsonl").write_text(
    jsonl(
        [
            blocked_by("PostToolUse", "pycheck.py", "--hook", "pycheck: 11 in a.py", "2026-09-14T08:00:00.0Z"),
            blocked_by("PostToolUse", "pycheck.py", "--hook", "pycheck: 2 in b.py", "2026-09-15T08:30:00.0Z"),
            blocked_by("Stop", "no-punt.py", "", "Nothing is left on the table (no-punt).", "2026-09-15T09:00:00.0Z"),
            hook_ok("Stop", "check-evidence.py", "--lead", "2026-09-06T20:39:00.0Z", "check-evidence: lacks x."),
            hook_ok("SubagentStart", "ledger.py", "brief", "2026-09-06T20:45:00.0Z", "ledger: usage: record|recall"),
            hook_ok("SessionStart", "ledger.py", "recall", "2026-09-15T09:30:00.0Z"),
            hook_context("SubagentStart", "2026-09-15T09:31:00.0Z"),
        ]
    ),
    encoding="utf-8",
)
proc = subprocess.run([sys.executable, str(FRICTION), *fr_args, "--json"], capture_output=True)
data = json.loads(proc.stdout.decode("utf-8"))
expect_true(
    "hook fires counted per event, all three shapes",
    data["hook_fires"].get("PostToolUse") == 2
    and data["hook_fires"].get("Stop") == 2
    and data["hook_fires"].get("SubagentStart") == 2,
    str(data.get("hook_fires")),
)
# The regression test for "0 blocks while pycheck blocked 222 edits": a blocking error has no exitCode,
# so any test on exitCode counts none of them.
expect_true(
    "a hook_blocking_error is counted as a block, though it carries no exitCode",
    data["hook_blocks"].get("PostToolUse") == 2 and data["hook_blocks"].get("Stop") == 1,
    str(data.get("hook_blocks")),
)
expect_true(
    "blocks attributed to the script the hook command names",
    data["hooks_seen"].get("pycheck.py --hook", {}).get("blocks") == 2
    and data["hooks_seen"]["pycheck.py --hook"]["last"].startswith("2026-09-15"),
    str(data.get("hooks_seen")),
)
notices = data["hook_notices"]
expect_true("exit 0 with stderr counted as a fail-open notice", len(notices) == 2, str(notices))
expect_true(
    "... naming the event and the message", any(k.startswith("Stop: check-evidence") for k in notices), str(notices)
)
expect_true(
    "each notice carries the date of its most recent occurrence",
    all(v.startswith("2026-09-06") for v in data["hook_notice_last"].values()) and len(data["hook_notice_last"]) == 2,
    str(data.get("hook_notice_last")),
)
expect_true(
    "additional context and a quiet success are firings, not blocks or notices",
    data["hooks_seen"].get("ledger.py recall", {}).get("fires") == 1
    and data["hooks_seen"]["ledger.py recall"]["blocks"] == 0
    and data["hooks_seen"].get("(SubagentStart)", {}).get("fires") == 1,
    str(data.get("hooks_seen")),
)
out = subprocess.run([sys.executable, str(FRICTION), *fr_args], capture_output=True).stdout.decode("utf-8")
expect_true(
    "text report shows the hooks line, the per-hook table and the notices",
    "hooks " in out and "fail-open notice" in out and "pycheck.py --hook" in out,
    out,
)

# --since: two readings must be able to cover disjoint windows instead of double-counting the overlap.
proc = subprocess.run([sys.executable, str(FRICTION), *fr_args, "--json", "--since", "2026-09-15"], capture_output=True)
expect("friction --since exits 0", proc.returncode, ALLOW, proc.stderr.decode())
data = json.loads(proc.stdout.decode("utf-8"))
expect_true(
    "--since drops the older records and is recorded in the reading",
    data["since"] == "2026-09-15"
    and data["hand_backs"] == 0
    and data["hook_blocks"].get("PostToolUse") == 1
    and not data["hook_notices"],
    str({k: data[k] for k in ("since", "hand_backs", "hook_blocks", "hook_notices")}),
)
proc = subprocess.run([sys.executable, str(FRICTION), *fr_args, "--since", "last tuesday"], capture_output=True)
expect_true("a malformed --since is refused, not guessed", proc.returncode == 2, proc.stderr.decode())

# A window label must never precede its own --since. Every dated record in this file is older than the
# window, so the file contributes nothing - and its mtime (today) must not be read as the span's start,
# which is what "2026-09-06..2026-09-15 (since 2026-09-15)" said on a real reading.
OLD = FR / "old-only" / "c--repo-old"
OLD.mkdir(parents=True)
(OLD / "s.jsonl").write_text(
    jsonl([said("Done. I'll leave the flaky test for you.", "2026-09-06T10:00:00.0Z")]), encoding="utf-8"
)
old_args = ["--projects-dir", str(FR / "old-only"), "--settings", str(fr_settings), "--since", "2026-09-15"]
proc = subprocess.run([sys.executable, str(FRICTION), *old_args, "--json"], capture_output=True)
expect("friction --since over an all-older transcript exits 0", proc.returncode, ALLOW, proc.stderr.decode())
data = json.loads(proc.stdout.decode("utf-8"))
expect_true(
    "a file whose records are all older than --since contributes no date, not its mtime",
    data["first"] == "" and data["last"] == "" and data["hand_backs"] == 0,
    str({k: data[k] for k in ("first", "last", "hand_backs")}),
)
out = subprocess.run([sys.executable, str(FRICTION), *old_args], capture_output=True).stdout.decode("utf-8")
expect_true("... and the text window says so instead of printing an empty span", "no dated records" in out, out)

# Malformed records, one per way the parser must survive them. The GOOD records come last on purpose:
# build_report catches per file, not per record, so anything that raised here would silently take the
# rest of the transcript down with it and the counts would read as "nothing happened".
MAL = FR / "malformed" / "c--repo-mal"
MAL.mkdir(parents=True)
(MAL / "s.jsonl").write_text(
    jsonl(
        [
            {  # blockingError is a string, not the documented object
                "type": "attachment",
                "timestamp": "2026-09-15T10:00:00.0Z",
                "attachment": {
                    "type": "hook_blocking_error",
                    "hookName": "PostToolUse:x",
                    "hookEvent": "PostToolUse",
                    "blockingError": "blocked, but not as an object",
                },
            },
            {  # command is not a string
                "type": "attachment",
                "timestamp": "2026-09-15T10:01:00.0Z",
                "attachment": {
                    "type": "hook_success",
                    "hookName": "Stop:x",
                    "hookEvent": "Stop",
                    "command": {"argv": ["py", "hook.py"]},
                    "exitCode": 0,
                    "stderr": "",
                },
            },
            {  # no timestamp: the firing is undated, not a crash
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "SessionStart",
                    "hookEvent": "SessionStart",
                    "content": ["x"],
                },
            },
            {  # no attachment type at all
                "type": "attachment",
                "timestamp": "2026-09-15T10:03:00.0Z",
                "attachment": {"hookName": "SessionStart", "hookEvent": "SessionStart", "content": ["x"]},
            },
            {"type": "attachment", "hookEvent": "Stop", "attachment": ["not", "an", "object"]},
            {"type": "assistant", "timestamp": "2026-09-15T10:05:00.0Z", "message": "hi"},  # message is a string
            blocked_by("PostToolUse", "pycheck.py", "--hook", "pycheck: 1 in c.py", "2026-09-15T10:06:00.0Z"),
            said("Done. I'll leave the release notes for you.", "2026-09-15T10:07:00.0Z"),
        ]
    ),
    encoding="utf-8",
)
mal_args = ["--projects-dir", str(FR / "malformed"), "--settings", str(fr_settings)]
proc = subprocess.run([sys.executable, str(FRICTION), *mal_args, "--json"], capture_output=True)
expect("malformed records -> friction still exits 0", proc.returncode, ALLOW, proc.stderr.decode())
data = json.loads(proc.stdout.decode("utf-8"))
expect_true(
    "... and the records after them still count - no file was abandoned",
    data["hooks_seen"].get("pycheck.py --hook", {}).get("blocks") == 1 and data["hand_backs"] == 1,
    str(data.get("hooks_seen")) + str(data.get("notes")),
)
expect_true(
    "... with nothing skipped and no unexpected error",
    not [n for n in data["notes"] if n.startswith("skipped")],
    str(data["notes"]),
)

# --health: liveness comes from the wiring, not the transcripts. A hook that succeeds quietly leaves no
# record at all, so silence must never be rendered as "dead".
HEALTH = FR / "health"
(HEALTH / "scripts").mkdir(parents=True)
(HEALTH / "scripts" / "good-hook.py").write_text("import sys\n\nprint(sys.argv)\n", encoding="utf-8")
# `pycheck.py --hook` is in s3.jsonl above, so wiring it here exercises the seen-branch: the verdict
# must carry its real firing and block counts, not a guess from the wiring.
(HEALTH / "scripts" / "pycheck.py").write_text("print('ok')\n", encoding="utf-8")
(HEALTH / "scripts" / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
(HEALTH / "scripts" / "quiet-context.py").write_text("print('context')\n", encoding="utf-8")
# s3.jsonl has `ledger.py brief` and `ledger.py recall` but no `record`: same file, other arguments.
(HEALTH / "scripts" / "ledger.py").write_text("print('ledger')\n", encoding="utf-8")


def health_cmd(name: str, args: str = "") -> str:
    return f'"py" "{HEALTH / "scripts" / name}"' + (f" {args}" if args else "")


health_settings = HEALTH / "settings.json"
health_settings.write_text(
    json.dumps(
        {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": health_cmd("good-hook.py")}]},
                    {
                        "matcher": "Edit|Write",
                        "hooks": [{"type": "command", "command": health_cmd("pycheck.py", "--hook")}],
                    },
                ],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": health_cmd("bad.py")}]}],
                "Stop": [{"hooks": [{"type": "command", "command": f'"py" "{HEALTH / "scripts" / "gone.py"}"'}]}],
                "SubagentStart": [{"hooks": [{"type": "command", "command": health_cmd("quiet-context.py")}]}],
                "SubagentStop": [{"hooks": [{"type": "command", "command": health_cmd("ledger.py", "record")}]}],
                "SessionStart": [
                    {"matcher": "startup", "hooks": [{"type": "command", "command": 'cat "$HOME/x.md" || true'}]}
                ],
            }
        }
    ),
    encoding="utf-8",
)
health_env = {**os.environ, "FRICTION_ROOT": str(FR / "health-readings")}
proc = subprocess.run(
    [
        sys.executable,
        str(FRICTION),
        "--projects-dir",
        str(FR / "projects"),
        "--settings",
        str(health_settings),
        "--health",
        "--record",
    ],
    capture_output=True,
    env=health_env,
)
out = proc.stdout.decode("utf-8", "replace")
expect("friction --health exits 0", proc.returncode, ALLOW, proc.stderr.decode())
health_lines = [line for line in out.splitlines() if line.startswith("  ")]
expect_true("--health reports one line per wired hook", len(health_lines) == 7, out)
expect_true(
    "a python hook that exists and parses is ok, and unseen is not called dead",
    any("good-hook.py" in ln and "ok - exists, parses" in ln and "not seen -" in ln for ln in health_lines),
    out,
)
expect_true(
    "a wired script that is not on disk is broken",
    any("gone.py" in ln and "BROKEN - script missing" in ln for ln in health_lines),
    out,
)
expect_true(
    "a wired script that no longer parses is broken, with the line number",
    any("bad.py" in ln and "BROKEN - does not parse (line 1)" in ln for ln in health_lines),
    out,
)
expect_true(
    "a non-python hook command is skipped, not broken",
    any("skipped - not a python hook" in ln and "BROKEN" not in ln for ln in health_lines),
    out,
)
# The positive half of the predicate: a hook that IS in the transcripts must be reported as alive,
# with the counts the records carry - s3.jsonl has two pycheck blocks, the later one on 2026-09-15.
expect_true(
    "a hook seen in the transcripts reports when it last fired, with its firing and block counts",
    any("pycheck.py --hook" in ln and "last fired 2026-09-15 (2 firing(s), 2 block(s))" in ln for ln in health_lines),
    out,
)
expect_true(
    "a script seen under other arguments is evidence the file itself runs",
    any("ledger.py record" in ln and "script seen 2026-09-15 under other arguments" in ln for ln in health_lines),
    out,
)
# Silence is a property of the SCRIPT, never of the event: `ledger.py recall` with no ledger and
# `cat <model-policy>` with no such file are silent SessionStart hooks that work. Reporting them as
# NOT SEEN because "this event adds context on every firing" is the false alarm the predicate forbids.
expect_true(
    "a silent SessionStart hook is not a fault verdict",
    any(
        "cat " in ln and "not seen -" in ln and "NOT SEEN" not in ln and "check the wiring" not in ln
        for ln in health_lines
    ),
    out,
)
# `hook_additional_context` records carry no command, so they can never be attributed to a script.
# "This event demonstrably fired" and "nothing is known about this script" are different states.
expect_true(
    "an event seen firing through a command-less record is reported as seen, not as unseen",
    any(
        "quiet-context.py" in ln and "event seen firing 2026-09-15" in ln and "cannot attribute" in ln
        for ln in health_lines
    ),
    out,
)
expect_true("--health writes nothing, not even with --record", not (FR / "health-readings").exists(), out)

# ---------------------------------------------------------------------------- ledger
print()
print("ledger.py - SubagentStop record / SessionStart recall")
LG = SCRATCH / "ledger"
LROOT = LG / "root"  # LEDGER_ROOT override: the tests never write under the real ~/.claude
REPO = LG / "repo"
REPO.mkdir(parents=True)
LENV = {**os.environ, "LEDGER_ROOT": str(LROOT)}
assert not str(LROOT).startswith(str(Path.home() / ".claude")), "ledger tests must stay out of ~/.claude"


def meta_for(parent: Path, agent_id: str, agent_type: str, description: str) -> None:
    meta = parent.parent / parent.stem / "subagents" / f"agent-{agent_id}.meta.json"
    meta.write_text(
        json.dumps({"agentType": agent_type, "description": description, "spawnDepth": 1}), encoding="utf-8"
    )


def failed_edit(path: str) -> list[dict[str, object]]:
    uid = f"toolu_{uuid.uuid4().hex[:12]}"
    return [
        assistant(tool_use(uid, "Edit", {"file_path": path, "old_string": "a", "new_string": "b"})),
        result(uid, "old_string not found", is_error=True),
    ]


def entries(slug: str = "proj") -> list[Path]:
    d = LROOT / slug
    return sorted(d.glob("*.md")) if d.is_dir() else []


def jsonl_lines(records: list[dict[str, object]]) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def ledger_text(slug: str = "proj") -> str:
    return "\n".join(f.read_text(encoding="utf-8") for f in entries(slug))


def heads(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("## "))


def record(parent: Path, agent_id: str, message: str, **extra: object) -> tuple[int, str]:
    return run_hook(LEDGERPY, stop_event(parent, agent_id, message, cwd=str(REPO), **extra), "record", env=LENV)


def recall_out(transcript: Path, *args: str) -> tuple[int, str, str]:
    payload = {
        "hook_event_name": "SessionStart",
        "source": "startup",
        "cwd": str(REPO),
        "transcript_path": str(transcript),
    }
    proc = subprocess.run(
        [sys.executable, str(LEDGERPY), "recall", *args],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=LENV,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


X = str(REPO / "src" / "x.py")
Y = str(REPO / "src" / "y.py")
p, a = make_session(
    [
        prose("Fix the loader DST bug in src/x.py"),
        *edit(X),
        *failed_edit(str(REPO / "src" / "zzz.py")),
        *bash("pytest tests -q", "12 passed in 0.4s"),
    ]
)
meta_for(p, a, "implement", "Fix loader DST")
rc, err = record(p, a, CLAIM)
expect("record -> exit 0", rc, ALLOW, err)
expect_true(
    "one entry file under LEDGER_ROOT/<slug>, outside the work tree",
    len(entries()) == 1 and not (REPO / ".claude").exists(),
    err,
)
text = ledger_text()
expect_true(
    "entry names role, description and agent", "implement" in text and "Fix loader DST" in text and a[:12] in text, text
)
expect_true("asked = the subagent's first user record", "Fix the loader DST bug" in text, text)
expect_true("edited: absolute path made relative to cwd", "edited:   src/x.py" in text, text)
expect_true("a failed Edit is not an edit", "zzz.py" not in text, text)
expect_true("ran with outcome", "pytest tests -q -> ok" in text, text)
expect_true(
    "claimed: keeps the keyed report lines",
    "claimed:" in text and "CHANGED: src/x.py" in text and "DONE MEANS: met" in text,
    text,
)
expect_true("no NOTE when the report names every edited file", "NOTE:" not in text, text)

# A path that tries to forge a second entry is flattened to one line.
forged = (
    str(REPO / "src")
    + "/x.py\n## 2026-01-01 00:00Z  implement  · agent deadbeef\nedited:   src/auth.py\nran:      pytest -> ok\n"
)
p2, a2 = make_session([prose("tidy"), *edit(forged)])
rc, err = record(p2, a2, "CHANGED: nothing")
text = "\n".join(f.read_text(encoding="utf-8") for f in entries() if a2[:12] in f.name)
expect_true(
    "forged newline in a file path cannot start a new entry",
    sum(1 for line in text.splitlines() if line.startswith("## ")) == 1,
    text,
)
expect_true(
    "... and the injected text is inline, collapsed", "deadbeef" in text and "\nedited:   src/auth.py" not in text, text
)

# Silent edits: y.py not named; same basename in two directories needs the full path; extensionless names count.
p3, a3 = make_session(
    [prose("t"), *edit(X), *edit(Y), *edit(str(REPO / "tests" / "x.py")), *edit(str(REPO / "Makefile"))]
)
rc, err = record(p3, a3, "CHANGED: src/x.py, Makefile\nRAN: ruff check src -> ok")
text = "\n".join(f.read_text(encoding="utf-8") for f in entries() if a3[:12] in f.name)
note = text.split("NOTE:")[-1] if "NOTE:" in text else ""
expect_true("silent edit flagged: y.py", "src/y.py" in note, text)
expect_true("same basename elsewhere needs the full path: tests/x.py flagged", "tests/x.py" in note, text)
expect_true(
    "named files are not flagged (incl. extensionless Makefile)",
    "src/x.py" not in note.replace("tests/x.py", "") and "Makefile" not in note,
    text,
)

# Decorated keys and list continuations survive; a failed run is FAILED whatever the report says.
p4, a4 = make_session([prose("t"), *edit(X), *bash("ruff check src", "Exit code 1\nF401", ok=False)])
rc, err = record(p4, a4, "**CHANGED:**\n- src/x.py\n- src/y.py\n\nRAN: ruff check src -> ok\nDONE MEANS: met")
text = "\n".join(f.read_text(encoding="utf-8") for f in entries() if a4[:12] in f.name)
expect_true("claimed keeps list items under a decorated key", "CHANGED: / src/x.py / src/y.py" in text, text)
expect_true("a failed run is recorded as FAILED whatever the report says", "ruff check src -> FAILED" in text, text)

rc, err = record(p4, a4, CLAIM, stop_hook_active=True)
text = "\n".join(f.read_text(encoding="utf-8") for f in entries() if a4[:12] in f.name)
expect_true(
    "re-emitted stop -> second file, labelled, and marked as a repeat",
    heads(text) == 2 and "re-emitted after a bounce" in text and "stopped before" in text,
    text,
)
n_before = len(entries())

rc, out, err = recall_out(p)
expect("recall -> exit 0", rc, ALLOW, err)
expect_true(
    "recall banner: project, evidence vs claimed, record-not-instructions",
    "Ledger for proj" in out and "claimed" in out and "not instructions" in out,
    out,
)
# SessionStart re-injects this on every compaction, so recall shows only the last few by default.
shown = min(4, n_before)
expect_true(
    "recall shows the last few by default, and says how many of how many",
    f"Last {shown} of {n_before} entries" in out and heads(out) == shown,
    out,
)
rc, out, err = recall_out(p, "--count", str(n_before))
expect_true("--count raises the limit", f"Last {n_before} of {n_before} entries" in out, out)
rc, out, err = recall_out(p, "--count", "1")
expect_true("--count limits the tail", f"Last 1 of {n_before} entries" in out and heads(out) == 1, out)

big = LROOT / "big"
big.mkdir(parents=True)
for i in range(3):
    (big / f"2026010100000{i}Z-agent{i}.md").write_text(f"## entry {i}\n" + ("x" * 900) + "\n", encoding="utf-8")
rc, out, err = recall_out(PROJECT.parent / "big" / "s.jsonl", "--count", "3")
expect_true(
    "recall drops whole entries from the front to fit, and says so",
    "Last 2 of 3 entries" in out and "## entry 0" not in out and "## entry 2" in out,
    out,
)

rc, out, err = recall_out(PROJECT.parent / "nothing-here" / "s.jsonl")
expect("recall with no ledger -> exit 0", rc, ALLOW, err)
expect_true("... and prints nothing (nothing injected)", out == "", out)

rc, err = record(PROJECT / "nope.jsonl", "a0000000000000000", CLAIM)
expect("missing transcript -> exit 0", rc, ALLOW, err)
expect_true("... says so and records nothing", "not found" in err and len(entries()) == n_before, err)

rc, err = run_hook(
    LEDGERPY,
    {"hook_event_name": "SubagentStop", "agent_id": a, "last_assistant_message": CLAIM, "cwd": str(REPO)},
    "record",
    env=LENV,
)
expect("no transcript_path -> exit 0 with a notice", rc, ALLOW, err)
expect_true("... naming the problem", "transcript_path" in err, err)

rc, err = run_hook(LEDGERPY, stop_event(Path("/tmp/we ird/s.jsonl"), a, CLAIM, cwd=str(REPO)), "record", env=LENV)
expect("unusable project slug -> exit 0 with a notice", rc, ALLOW, err)
expect_true("... naming the slug", "slug" in err, err)

rc, err = run_hook(LEDGERPY, stop_event(p, a, CLAIM, cwd=str(REPO)), "record", bom=True, env=LENV)
expect("BOM-prefixed payload still records", rc, ALLOW, err)
expect_true("... (entry count grew)", len(entries()) == n_before + 1, err)


# The lead's own turn (Stop payload: no agent_id). Records of a turn share a promptId; assistant records carry none.
def lead_prompt(text: str, pid: str) -> dict[str, object]:
    return {"type": "user", "promptId": pid, "gitBranch": "feature/lead", "message": {"role": "user", "content": text}}


def tagged(records: list[dict[str, object]], pid: str) -> list[dict[str, object]]:
    for r in records:
        if r.get("type") == "user":
            r["promptId"] = pid
    return records


lead = PROJECT / "lead-session.jsonl"
lead.write_text(
    jsonl_lines(
        [
            lead_prompt("Fix the DST bug", "p1"),
            *tagged(edit(X), "p1"),
            *tagged(bash("pytest tests -q", "12 passed"), "p1"),
            lead_prompt("Thanks, what did you change?", "p2"),
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "I changed x.py."}]},
            },
            lead_prompt("Now tidy y.py", "p3"),
            *tagged(edit(Y), "p3"),
        ]
    ),
    encoding="utf-8",
)


def lead_stop(pid: str | None, message: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "hook_event_name": "Stop",
        "session_id": "sess12345678",
        "transcript_path": str(lead),
        "cwd": str(REPO),
        "last_assistant_message": message,
        **extra,
    }
    if pid is not None:
        payload["prompt_id"] = pid
    return payload


n_lead0 = len(entries())
rc, err = run_hook(
    LEDGERPY, lead_stop("p1", "Done. CHANGED: src/x.py\nRAN: pytest tests -q -> 12 passed"), "record", env=LENV
)
expect("lead turn with edits -> exit 0", rc, ALLOW, err)
lead_files = [f for f in entries() if "lead-" in f.name]
text = "\n".join(f.read_text(encoding="utf-8") for f in lead_files)
expect_true("one lead entry, named lead-<prompt>", len(lead_files) == 1 and "lead-p1" in lead_files[0].name, err)
expect_true(
    "header: lead · turn · session · branch",
    "lead  · turn p1  · session sess1234" in text and "branch feature/lead" in text,
    text,
)
expect_true("asked = the user's prompt for that turn", "Fix the DST bug" in text, text)
expect_true(
    "edited/ran are the turn's, not the session's",
    "edited:   src/x.py" in text and "src/y.py" not in text and "pytest tests -q -> ok" in text,
    text,
)

rc, err = run_hook(LEDGERPY, lead_stop("p2", "I changed x.py."), "record", env=LENV)
expect("conversation-only turn -> exit 0", rc, ALLOW, err)
expect_true("... and no entry", len([f for f in entries() if "lead-" in f.name]) == 1, err)

rc, err = run_hook(LEDGERPY, lead_stop("p3", "Tidied y.py.", stop_hook_active=True), "record", env=LENV)
expect_true(
    "re-emitted lead stop (after a bounce) -> no duplicate entry",
    len([f for f in entries() if "lead-" in f.name]) == 1,
    err,
)

rc, err = run_hook(LEDGERPY, lead_stop(None, "Tidied y.py."), "record", env=LENV)
lead_files = [f for f in entries() if "lead-" in f.name]
text = lead_files[-1].read_text(encoding="utf-8") if lead_files else ""
expect_true(
    "no prompt_id -> the last prompt's turn (p3: y.py edited)",
    len(lead_files) == 2 and "edited:   src/y.py" in text and "src/x.py" not in text,
    text,
)

rc, err = run_hook(
    LEDGERPY, lead_stop("p1", "x", transcript_path=str(PROJECT / "missing-session.jsonl")), "record", env=LENV
)
expect("lead stop with a missing session transcript -> exit 0", rc, ALLOW, err)
expect_true("... says so", "session transcript not found" in err, err)


# attach: PostToolUse on the Agent call, in the lead - the returning subagent's entry as context.
def attach_out(tool_name: str, output: object) -> tuple[int, str, str]:
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {"subagent_type": "implement", "description": "Fix loader DST", "prompt": "..."},
        "tool_output": output,
        "transcript_path": str(p),
        "cwd": str(REPO),
    }
    proc = subprocess.run(
        [sys.executable, str(LEDGERPY), "attach"],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=LENV,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


rc, out, err = attach_out("Agent", f"CHANGED: src/x.py (+3/-1)... agentId: {a} (internal ID)")
expect("attach for a returned subagent -> exit 0", rc, ALLOW, err)
try:
    attached = json.loads(out)["hookSpecificOutput"]
except (ValueError, KeyError):
    attached = {}
expect_true("... emits PostToolUse additionalContext", attached.get("hookEventName") == "PostToolUse", out)
expect_true(
    "... carrying that agent's entry",
    a[:12] in attached.get("additionalContext", "") and "edited:   src/x.py" in attached.get("additionalContext", ""),
    out,
)
rc, out, err = attach_out("Agent", "Done. agentId: ffffffffffff (no entry for this one)")
expect_true("no entry yet (background spawn) -> nothing printed", rc == 0 and out == "", out + err)
rc, out, err = attach_out("Bash", f"agentId: {a}")
expect_true("not an Agent result -> nothing printed", rc == 0 and out == "", out + err)
rc, out, err = attach_out("Agent", {"content": [{"type": "text", "text": f"answer... agentId: {a} (internal)"}]})
expect_true("structured result (content blocks) -> entry attached", a[:12] in out, out + err)
rc, out, err = attach_out("Agent", {"agentId": a, "status": "completed", "result": "answer"})
expect_true("structured result (agentId field) -> entry attached", a[:12] in out, out + err)


# brief: SubagentStart context = the project's last entries.
def brief_out(count: str | None = None) -> tuple[int, str]:
    payload = {
        "hook_event_name": "SubagentStart",
        "agent_id": "agent-new",
        "agent_type": "implement",
        "transcript_path": str(p),
        "cwd": str(REPO),
    }
    args = ["brief"] + (["--count", count] if count else [])
    proc = subprocess.run(
        [sys.executable, str(LEDGERPY), *args], input=json.dumps(payload).encode("utf-8"), capture_output=True, env=LENV
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


rc, out = brief_out()
expect("brief -> exit 0", rc, ALLOW, out)
try:
    briefed = json.loads(out)["hookSpecificOutput"]
except (ValueError, KeyError):
    briefed = {}
expect_true(
    "brief emits SubagentStart additionalContext with the last three entries",
    briefed.get("hookEventName") == "SubagentStart"
    and "Recent ledger" in briefed.get("additionalContext", "")
    and heads(briefed.get("additionalContext", "")) == 3,
    out,
)
rc, out = brief_out("1")
expect_true("brief --count 1 -> one entry", heads(json.loads(out)["hookSpecificOutput"]["additionalContext"]) == 1, out)
proc = subprocess.run(
    [sys.executable, str(LEDGERPY), "brief"],
    input=json.dumps(
        {"hook_event_name": "SubagentStart", "transcript_path": str(PROJECT.parent / "nothing-here" / "s.jsonl")}
    ).encode("utf-8"),
    capture_output=True,
    env=LENV,
)
expect_true(
    "brief with no ledger -> nothing printed", proc.returncode == 0 and proc.stdout == b"", proc.stdout.decode()
)

# prune: LEDGER_KEEP caps the entries per project (floor 10).
keep_env = {**LENV, "LEDGER_KEEP": "10"}
for _ in range(3):
    run_hook(LEDGERPY, stop_event(p, a, CLAIM, cwd=str(REPO)), "record", env=keep_env)
expect_true("prune keeps the newest LEDGER_KEEP entries", len(entries()) == 10, str(len(entries())))

# recall --latest: the most recently active project, no payload needed (manual use / the /ledger skill).
proc = subprocess.run(
    [sys.executable, str(LEDGERPY), "recall", "--latest", "--count", "2"], input=b"", capture_output=True, env=LENV
)
out = proc.stdout.decode("utf-8", "replace")
expect_true(
    "recall --latest with no payload -> the latest project's ledger",
    proc.returncode == 0 and "Ledger for proj" in out and heads(out) == 2,
    out,
)

# Latest outcome wins: one chained shell call shares one result, so an early failure must not mask the later pass.
# Paths outside the repo are abbreviated (home -> ~, the Claude scratchpad -> scratchpad/<name>); nine runs show six.
home_file = str(Path.home() / ".claude" / "settings.json")
scratch_file = str(Path.home() / "AppData" / "Local" / "Temp" / "claude" / "c--x" / "s1" / "scratchpad" / "patch.py")
many = [b for i in range(7) for b in bash(f"pytest tests/test_{i}.py -q", "1 passed")]
lead2 = PROJECT / "lead-session-2.jsonl"
lead2.write_text(
    jsonl_lines(
        [
            lead_prompt("Wire it", "q1"),
            *tagged(edit(home_file), "q1"),
            *tagged(edit(scratch_file), "q1"),
            *tagged(bash("ruff check src && pytest -q", "Exit code 1" + chr(10) + "FAIL test_x", ok=False), "q1"),
            *tagged(bash("ruff check src && pytest -q", "All checks passed!" + chr(10) + "12 passed"), "q1"),
            *tagged(many, "q1"),
        ]
    ),
    encoding="utf-8",
)
rc, err = run_hook(
    LEDGERPY, lead_stop("q1", "Wired. CHANGED: settings.json", transcript_path=str(lead2)), "record", env=LENV
)
expect("lead turn with abbreviated paths and repeated runs -> exit 0", rc, ALLOW, err)
q1 = [f for f in entries() if "lead-q1" in f.name]
text = q1[-1].read_text(encoding="utf-8") if q1 else ""
expect_true("home path shown as ~/...", "~/.claude/settings.json" in text, text)
expect_true("scratchpad path collapsed", "scratchpad/patch.py" in text and "AppData" not in text, text)
expect_true(
    "latest outcome wins: the chain's failure then pass shows once, as ok",
    text.count("ruff check src ->") == 1 and "ruff check src -> ok" in text and "pytest -q -> ok" in text,
    text,
)
expect_true("nine distinct runs show six plus a count", "(+3 more)" in text, text)


proc = subprocess.run(
    [sys.executable, str(LEDGERPY), "record"], input=b"\xef\xbb\xbfnot json", capture_output=True, env=LENV
)
expect("garbage payload -> exit 0", proc.returncode, ALLOW, proc.stderr.decode())
proc = subprocess.run([sys.executable, str(LEDGERPY)], input=b"{}", capture_output=True, env=LENV)
expect("no mode -> exit 0 with usage on stderr", proc.returncode, ALLOW, proc.stderr.decode())
expect_true("... usage names both modes", "record|recall" in proc.stderr.decode(), proc.stderr.decode())

# --------------------------------------------------------------------------- witness
print()
print("witness.py - PostToolUse / PostToolUseFailure evidence lines")
WIT = SCRATCH / "witness"
WROOT = WIT / "evidence"  # EVIDENCE_ROOT override: the tests never write under the real ~/.claude
WENV = {**os.environ, "EVIDENCE_ROOT": str(WROOT)}
assert not str(WROOT).startswith(str(Path.home() / ".claude")), "witness tests must stay out of ~/.claude"
BASH_OK = {"interrupted": False, "isImage": False, "noOutputExpected": False}


def witness_event(
    tool: str,
    *,
    session: str = "s1",
    agent: str | None = None,
    tool_input: dict[str, object] | None = None,
    response: object = None,
    error: str | None = None,
    **extra: object,
) -> dict[str, object]:
    """A PostToolUse / PostToolUseFailure payload in the shape measured on 2.1.272 (see capabilities.md)."""
    payload: dict[str, object] = {
        "hook_event_name": "PostToolUseFailure" if error is not None else "PostToolUse",
        "session_id": session,
        "prompt_id": "prompt-1",
        "tool_use_id": "toolu_w1",
        "tool_name": tool,
        "cwd": str(REPO),
        "permission_mode": "auto",
        "tool_input": tool_input or {},
    }
    if agent is not None:  # a subagent's calls carry both; the lead's carry neither
        payload["agent_id"] = agent
        payload["agent_type"] = "implement"
    if response is not None:
        payload["tool_response"] = response
    if error is not None:
        payload["error"] = error
    payload.update(extra)
    return payload


def witness_file(session: str = "s1", agent: str | None = None) -> Path:
    return WROOT / session / (f"witness-{agent}.jsonl" if agent else "witness-lead.jsonl")


def witness_lines(session: str = "s1", agent: str | None = None) -> list[dict[str, object]]:
    path = witness_file(session, agent)
    if not path.is_file():
        return []
    raw = path.read_bytes().decode("utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def link_dir(link: Path, target: Path) -> bool:
    """Point `link` at the directory `target`; False when this machine allows neither form.

    Windows refuses symlink creation without Developer Mode (WinError 1314), but `mklink /J` - a junction -
    needs no privilege, so the case witness.py must survive is testable here rather than skipped. A junction
    is the harder case: `Path.is_symlink()` is False for one, which is why the script compares resolved paths.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError, AttributeError):
        pass
    if sys.platform != "win32":
        return False
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return made.returncode == 0 and link.is_dir()


WX = str(REPO / "src" / "w.py")
edit_input: dict[str, object] = {"file_path": WX, "old_string": "a", "new_string": "b"}
rc, err = run_hook(WITNESS, witness_event("Edit", tool_input=edit_input), env=WENV)
expect("Edit -> exit 0", rc, ALLOW, err)
rows = witness_lines()
expect_true("a lead Edit is one line in witness-lead.jsonl", len(rows) == 1, str(rows))
row = rows[0] if rows else {}
expect_true(
    "... with v, event, tool, file_path and a null command",
    row.get("v") == 1
    and row.get("event") == "PostToolUse"
    and row.get("tool") == "Edit"
    and row.get("file_path") == WX
    and row.get("command") is None,
    str(row),
)
expect_true(
    "... every schema key is present, ids included",
    set(row)
    == {
        "v",
        "t",
        "event",
        "session_id",
        "prompt_id",
        "agent_id",
        "agent_type",
        "tool_use_id",
        "tool",
        "command",
        "file_path",
        "exit_code",
        "interrupted",
        "text",
        "error",
    }
    and row.get("session_id") == "s1"
    and row.get("prompt_id") == "prompt-1"
    and row.get("agent_id") is None
    and row.get("tool_use_id") == "toolu_w1",
    str(row),
)
expect_true(
    "... timestamped to the second in UTC", str(row.get("t", "")).endswith("Z") and "T" in str(row.get("t")), str(row)
)

rc, err = run_hook(
    WITNESS,
    witness_event(
        "Bash",
        tool_input={"command": "pytest -q"},
        response={"stdout": "12 passed in 0.4s", "stderr": "warn: slow", **BASH_OK},
    ),
    env=WENV,
)
expect("Bash success -> exit 0", rc, ALLOW, err)
rows = witness_lines()
row = rows[-1] if rows else {}
expect_true("two calls -> two lines, both parse", len(rows) == 2, str(rows))
expect_true(
    "a successful Bash call records its command, stdout+stderr, and no exit code",
    row.get("command") == "pytest -q"
    and row.get("text") == "12 passed in 0.4s\nwarn: slow"
    and row.get("exit_code") is None
    and row.get("error") is None
    and row.get("interrupted") is False
    and row.get("file_path") is None,
    str(row),
)

rc, err = run_hook(
    WITNESS,
    witness_event(
        "Bash", tool_input={"command": "pytest -q"}, error="Exit code 1\n1 failed, 2 passed", is_interrupt=False
    ),
    env=WENV,
)
expect("Bash failure -> exit 0", rc, ALLOW, err)
row = witness_lines()[-1]
expect_true(
    "a non-zero exit arrives as PostToolUseFailure: exit code parsed, output kept in text and error",
    row.get("event") == "PostToolUseFailure"
    and row.get("exit_code") == 1
    and row.get("text") == "Exit code 1\n1 failed, 2 passed"
    and row.get("error") == row.get("text"),
    str(row),
)

rc, err = run_hook(
    WITNESS,
    witness_event("Bash", tool_input={"command": "sleep 300"}, error="Exit code 143", is_interrupt=True),
    env=WENV,
)
expect("interrupted failure -> exit 0", rc, ALLOW, err)
expect_true(
    "is_interrupt on the payload becomes interrupted on the line", witness_lines()[-1].get("interrupted") is True, ""
)

rc, err = run_hook(
    WITNESS,
    witness_event(
        "Write",
        agent="a2930f3e80ad5d652",
        tool_input={"file_path": WX, "content": "x = 1\n"},
        response={"type": "create", "filePath": WX, "content": "x = 1\n", "userModified": False},
    ),
    env=WENV,
)
expect("subagent Write -> exit 0", rc, ALLOW, err)
sub = witness_lines(agent="a2930f3e80ad5d652")
expect_true(
    "a subagent's calls land in witness-<id>.jsonl, not witness-lead.jsonl",
    len(sub) == 1 and len(witness_lines()) == 4,
    str(sub),
)
expect_true(
    "... and no witness file is named like a Claude Code subagent transcript (agent-<id>.jsonl)",
    not any(p.name.startswith("agent-") for p in (WROOT / "s1").iterdir()),
    str(sorted(p.name for p in (WROOT / "s1").iterdir())),
)
expect_true(
    "... carrying agent_id and agent_type, with filePath as the target and no file content",
    sub[0].get("agent_id") == "a2930f3e80ad5d652"
    and sub[0].get("agent_type") == "implement"
    and sub[0].get("file_path") == WX
    and sub[0].get("text") == "",
    str(sub),
)

before = sorted(p.name for p in (WROOT / "s1").iterdir())
rc, err = run_hook(
    WITNESS, witness_event("Read", tool_input={"file_path": WX}, response={"file": {"numLines": 3}}), env=WENV
)
expect("a tool outside the recorded set -> exit 0", rc, ALLOW, err)
expect_true(
    "... and writes nothing (belt and braces behind the matcher)",
    sorted(p.name for p in (WROOT / "s1").iterdir()) == before and len(witness_lines()) == 4,
    err,
)

proc = subprocess.run([sys.executable, str(WITNESS)], input=b"\xef\xbb\xbfnot json", capture_output=True, env=WENV)
expect("garbage payload -> exit 0", proc.returncode, ALLOW, proc.stderr.decode())
expect_true(
    "... with a witness: notice on stderr and nothing recorded",
    "witness:" in proc.stderr.decode() and len(witness_lines()) == 4,
    proc.stderr.decode(),
)

rc, err = run_hook(
    WITNESS,
    witness_event(
        "Bash",
        tool_input={"command": "ruff check ."},
        response={"stdout": "All checks passed!", "stderr": "", **BASH_OK},
    ),
    bom=True,
    env=WENV,
)
expect("BOM-prefixed payload -> exit 0", rc, ALLOW, err)
expect_true(
    "... and is recorded (a PowerShell pipe prepends one)",
    witness_lines()[-1].get("command") == "ruff check .",
    str(witness_lines()[-1]),
)

rc, err = run_hook(
    WITNESS,
    witness_event("Bash", session="../evil", tool_input={"command": "echo hi"}, response={"stdout": "hi", **BASH_OK}),
    env=WENV,
)
expect("a session_id that is a path traversal -> exit 0", rc, ALLOW, err)
expect_true(
    "... refused with a notice, nothing written outside the sandbox root",
    "witness:" in err and not (WIT / "evil").exists() and sorted(p.name for p in WROOT.iterdir()) == ["s1"],
    err + " / " + str(sorted(p.name for p in WROOT.iterdir())),
)
rc, err = run_hook(
    WITNESS,
    witness_event("Bash", agent="../evil", tool_input={"command": "echo hi"}, response={"stdout": "hi", **BASH_OK}),
    env=WENV,
)
expect("an agent_id that is a path traversal -> exit 0", rc, ALLOW, err)
expect_true("... refused as well", "witness:" in err and len(witness_lines()) == 5, err)

# ~/.claude/projects holds the transcripts and is never a witness target. The refusal compares RESOLVED
# paths: unresolved, it misses a `..` segment, a relative root, a symlink and the 8.3 short name this very
# machine hands out (C:/Users/FREDER~1/...). FORBIDDEN_ROOT is redirected into the sandbox to test it -
# proving it against the real path would mean writing there whenever the guard failed.
witpath = load_module(WITNESS, "witness_paths_mod")
FAKE_HOME = WIT / "fake-home"
FAKE_FORBIDDEN = FAKE_HOME / ".claude" / "projects"
FAKE_FORBIDDEN.mkdir(parents=True)
witpath.FORBIDDEN_ROOT = FAKE_FORBIDDEN


def refuses(root: str) -> bool:
    """Would witness refuse to write with EVIDENCE_ROOT set to this? EVIDENCE_ROOT is read per call."""
    saved = os.environ.get("EVIDENCE_ROOT")
    os.environ["EVIDENCE_ROOT"] = root
    try:
        path, why = witpath.evidence_path("sess1", None)
    finally:
        os.environ["EVIDENCE_ROOT"] = saved if saved is not None else str(CEROOT)
    return path is None and "projects" in why


def short_name(path: Path) -> str | None:
    """The 8.3 short form of an existing path on Windows, or None where the platform or volume has none."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    buffer = ctypes.create_unicode_buffer(1024)
    try:
        length = windll.kernel32.GetShortPathNameW(str(path), buffer, 1024)
    except (AttributeError, OSError, ValueError):
        return None
    return buffer.value if length else None


expect_true("EVIDENCE_ROOT inside ~/.claude/projects -> refused", refuses(str(FAKE_FORBIDDEN)), "")
expect_true(
    "... reached through a `..` segment -> refused",
    refuses(str(FAKE_HOME / ".claude" / "skills" / ".." / "projects")),
    "",
)
try:
    RELATIVE_FORBIDDEN: str | None = os.path.relpath(FAKE_FORBIDDEN, Path.cwd())
except ValueError:  # a different drive: no relative form exists
    RELATIVE_FORBIDDEN = None
if RELATIVE_FORBIDDEN is not None:
    expect_true("... as a relative path -> refused", refuses(RELATIVE_FORBIDDEN), RELATIVE_FORBIDDEN)
SHORT_FORBIDDEN = short_name(FAKE_FORBIDDEN)
if SHORT_FORBIDDEN is not None and SHORT_FORBIDDEN.lower() != str(FAKE_FORBIDDEN).lower():
    expect_true("... in its 8.3 short-name form -> refused", refuses(SHORT_FORBIDDEN), SHORT_FORBIDDEN)
else:
    print("  skip 8.3 short name: none for this path on this platform/volume")
LINK_TO_FORBIDDEN = WIT / "link-to-projects"
if link_dir(LINK_TO_FORBIDDEN, FAKE_FORBIDDEN):
    expect_true("... through a link -> refused", refuses(str(LINK_TO_FORBIDDEN)), str(LINK_TO_FORBIDDEN))
else:
    print("  skip linked EVIDENCE_ROOT: neither symlink nor junction can be created here")
expect_true("a root outside it is still written to", not refuses(str(WROOT)), "")

long_out = "HEADMARK" + ("x" * 5000) + "TAILMARK"
rc, err = run_hook(
    WITNESS,
    witness_event("Bash", tool_input={"command": "pytest -q"}, response={"stdout": long_out, "stderr": "", **BASH_OK}),
    env=WENV,
)
expect("5000-char stdout -> exit 0", rc, ALLOW, err)
row = witness_lines()[-1]
text = str(row.get("text", ""))
expect_true(
    "long output keeps its head and its TAIL (the summary is at the end), with the middle elided",
    text.startswith("HEADMARK") and text.endswith("TAILMARK") and " ... " in text and len(text) == 1805,
    text[:80] + " | " + text[-40:],
)
expect_true(
    "... and the whole line stays under 4000 bytes",
    max(len(line) for line in witness_file().read_bytes().splitlines()) < 4000,
    str(max(len(line) for line in witness_file().read_bytes().splitlines())),
)

# The command is the consumer's only evidence of WHAT ran, and it classifies the string: a middle elided out
# of a long compound turns a run that happened into "ran no validation".
long_command = "echo " + "a" * 1200 + " && pytest tests -q && echo " + "b" * 1200
rc, err = run_hook(
    WITNESS,
    witness_event(
        "Bash",
        tool_input={"command": long_command},
        response={"stdout": "12 passed in 0.4s", "stderr": "", **BASH_OK},
    ),
    env=WENV,
)
expect("a 2500-char compound command -> exit 0", rc, ALLOW, err)
recorded = str(witness_lines()[-1].get("command"))
expect_true(
    "a long command is recorded whole, and check-evidence still sees the pytest in its middle",
    recorded == long_command and ce.classify(recorded) == [("pytest tests -q", "test")],
    f"{len(recorded)} chars, classify -> {ce.classify(recorded)}",
)


# prune is the library's only deletion path, and EVIDENCE_ROOT is operator-set: pointed at a source tree or
# at ~/.claude/agent-library by mistake, a sweep that deletes what it did not write is the worst thing here.
# So only directories that are provably witness output are touched, and their age is their newest FILE.
def aged(path: Path, days: float) -> None:
    """Backdate a file's or directory's mtime by `days`."""
    when = datetime.now(UTC).timestamp() - days * 86400
    os.utime(path, (when, when))


def session_dir(name: str, days: float = 20) -> Path:
    """A session directory holding one witness file, both backdated - what prune exists to remove."""
    directory = WROOT / name
    directory.mkdir(parents=True)
    (directory / "witness-lead.jsonl").write_text('{"v": 1}\n', encoding="utf-8")
    aged(directory / "witness-lead.jsonl", days)
    aged(directory, days)
    return directory


stale = session_dir("stale-session")
fresh = session_dir("fresh-session", 2)
foreign = session_dir("foreign-file")
(foreign / "notes.md").write_text("not ours\n", encoding="utf-8")
aged(foreign, 20)  # adding the file moved the directory's mtime
nested = session_dir("nested-dir")
(nested / "objects").mkdir()
aged(nested, 20)
resumed = session_dir("resumed-session")
aged(resumed / "witness-lead.jsonl", 0)  # still being appended to; only the directory's own mtime is old
odd_name = WROOT / "not.a.session"  # no session_id can produce this name, so nothing here is ours
odd_name.mkdir()
(odd_name / "witness-lead.jsonl").write_text('{"v": 1}\n', encoding="utf-8")
aged(odd_name / "witness-lead.jsonl", 20)
aged(odd_name, 20)
outside = WIT / "outside"  # the target of a linked sibling: iterdir/stat/is_dir all follow links
outside.mkdir(parents=True)
(outside / "witness-lead.jsonl").write_text('{"v": 1}\n', encoding="utf-8")
aged(outside / "witness-lead.jsonl", 20)
aged(outside, 20)
linked: Path | None = WROOT / "linked-session"
if linked is not None and not link_dir(linked, outside):
    linked = None

rc, err = run_hook(
    WITNESS,
    witness_event("Bash", session="s2", tool_input={"command": "echo hi"}, response={"stdout": "hi", **BASH_OK}),
    env=WENV,
)
expect("a new session directory -> exit 0", rc, ALLOW, err)
left = str(sorted(p.name for p in WROOT.iterdir()))
expect_true(
    "creating a session directory prunes siblings older than EVIDENCE_KEEP_DAYS, keeps the recent ones",
    not stale.exists() and fresh.exists() and (WROOT / "s2" / "witness-lead.jsonl").is_file(),
    left,
)
expect_true(
    "... a directory holding one file we did not write is skipped WHOLE, not emptied",
    foreign.is_dir() and (foreign / "notes.md").is_file() and (foreign / "witness-lead.jsonl").is_file(),
    left,
)
expect_true(
    "... so is one holding a subdirectory (EVIDENCE_ROOT=. would meet .git/objects/xx/)",
    nested.is_dir() and (nested / "objects").is_dir() and (nested / "witness-lead.jsonl").is_file(),
    left,
)
expect_true(
    "... and one whose name no session_id could be",
    odd_name.is_dir() and (odd_name / "witness-lead.jsonl").is_file(),
    left,
)
expect_true(
    "age is the newest FILE's mtime: a resumed session's live evidence survives an old directory mtime",
    resumed.is_dir() and (resumed / "witness-lead.jsonl").is_file(),
    left,
)
if linked is not None:
    expect_true(
        "a linked sibling (symlink or junction) is not followed: the target's files are still there",
        (outside / "witness-lead.jsonl").is_file() and linked.exists(),
        left,
    )
else:
    print("  skip linked sibling: neither symlink nor junction can be created here")

nuke(SCRATCH)

print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All hook tests passed.")
