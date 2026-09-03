#!/usr/bin/env python3
"""Behavioural tests for the two hooks. Exit 0 = all passed.

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
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYCHECK = ROOT / "scripts" / "pycheck.py"
EVIDENCE = ROOT / "scripts" / "check-evidence.py"
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

nuke(SCRATCH)

print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All hook tests passed.")
