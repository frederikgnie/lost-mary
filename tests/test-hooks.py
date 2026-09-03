#!/usr/bin/env python3
"""Behavioural tests for the two hooks. Exit 0 = all passed.

pycheck (PostToolUse) needs `ruff` and `ty` on PATH or via `uvx`; CI installs
both. check-evidence (SubagentStop) runs against synthetic transcripts that
mirror the on-disk shape observed in Claude Code 2.1.259:

  <project>/<session>.jsonl                          parent transcript
  <project>/<session>/subagents/agent-<id>.jsonl     the subagent's own

with `message.content[]` items of type `tool_use` {id, name, input} and
`tool_result` {tool_use_id, content, is_error}. A failing Bash result carries
`is_error: true` and text starting with "Exit code N".

Usage: python tests/test-hooks.py
"""
from __future__ import annotations

import importlib.util
import json
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
# Deliberately NOT the system temp dir: pycheck skips scratch files there.
SCRATCH = ROOT / ".tmp-pycheck"
MARKERS = Path(tempfile.gettempdir()) / "claude-check-evidence"

ALLOW, BLOCK = 0, 2
failures: list[str] = []


def run_hook(script: Path, event: object, *args: str, bom: bool = False) -> tuple[int, str]:
    payload = json.dumps(event)
    if bom:
        payload = chr(0xFEFF) + payload
    result = subprocess.run(
        [sys.executable, str(script), *args],
        input=payload.encode("utf-8"),
        capture_output=True,
    )
    return result.returncode, result.stderr.decode("utf-8", "replace")


def expect(name: str, actual: int, wanted: int, detail: str = "") -> None:
    ok = actual == wanted
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        failures.append(f"{name}: expected exit {wanted}, got {actual}\n{detail.strip()[:600]}")


def expect_true(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {name}")
    if not condition:
        failures.append(f"{name}\n{detail.strip()[:600]}")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- pycheck
print("pycheck.py - PostToolUse lint/type feedback")
tools_present = {t: shutil.which(t) is not None for t in ("ruff", "ty")}
if not all(tools_present.values()) and shutil.which("uvx") is None:
    failures.append(f"ruff/ty not runnable (PATH: {tools_present}, no uvx). Install: pip install ruff ty")
    print("  FAIL ruff and ty must be available for these tests")
else:

    def edit_event(path: Path, tool: str = "Edit") -> dict[str, object]:
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"},
            "tool_result": "ok",
            "cwd": str(ROOT),
        }

    rc, err = run_hook(PYCHECK, edit_event(FIXTURES / "bad.py"), "--hook")
    expect("Edit on bad.py -> findings (exit 2)", rc, BLOCK, err)
    expect_true("  ... ruff finding reported (F401)", "F401" in err, err)
    expect_true("  ... ty finding reported (error[...])", "error[" in err, err)
    expect_true("  ... message names the file", "bad.py" in err, err)
    expect_true("  ... ruff summary noise filtered", "fixable with" not in err, err)

    rc, err = run_hook(PYCHECK, edit_event(FIXTURES / "good.py", "Write"), "--hook")
    expect("Write on good.py -> clean (exit 0)", rc, ALLOW, err)

    rc, err = run_hook(PYCHECK, edit_event(ROOT / "README.md"), "--hook")
    expect("non-Python file -> ignored", rc, ALLOW, err)

    rc, err = run_hook(PYCHECK, edit_event(FIXTURES / "missing.py"), "--hook")
    expect("deleted/missing file -> ignored", rc, ALLOW, err)

    ev = edit_event(FIXTURES / "bad.py")
    ev["tool_name"] = "Bash"
    rc, err = run_hook(PYCHECK, ev, "--hook")
    expect("non-edit tool -> ignored", rc, ALLOW, err)

    rc, err = run_hook(PYCHECK, edit_event(FIXTURES / "bad.py"), "--hook", bom=True)
    expect("BOM-prefixed payload still checks (PowerShell pipe)", rc, BLOCK, err)

    proc = subprocess.run([sys.executable, str(PYCHECK), "--hook"], input=b"not json", capture_output=True)
    expect("garbage payload fails open", proc.returncode, ALLOW, proc.stderr.decode())

    proc = subprocess.run([sys.executable, str(PYCHECK), str(FIXTURES / "bad.py")], capture_output=True)
    expect("CLI on bad.py -> exit 1", proc.returncode, 1, proc.stdout.decode())
    proc = subprocess.run([sys.executable, str(PYCHECK), str(FIXTURES / "good.py")], capture_output=True)
    expect("CLI on good.py -> exit 0", proc.returncode, 0, proc.stdout.decode())

    # Venv discovery: shared sibling env beside package repos, bounded above,
    # project-local env preferred, ambient VIRTUAL_ENV only as a fallback.
    pycheck = load_module(PYCHECK, "_pycheck")
    shutil.rmtree(SCRATCH, ignore_errors=True)

    def make_venv(path: Path) -> Path:
        path.mkdir(parents=True)
        (path / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
        return path

    def make_pkg(path: Path) -> Path:
        (path / "src").mkdir(parents=True)
        (path / "pyproject.toml").write_text("[project]\nname='p'\n", encoding="utf-8")
        return path / "src"

    t1_env = make_venv(SCRATCH / "t1" / "ws" / "env" / ".venv")
    t1_src = make_pkg(SCRATCH / "t1" / "ws" / "pkg")
    found = pycheck.find_venv(t1_src)
    expect_true("find_venv: shared <ws>/env/.venv beside the package repo", found == t1_env, repr(found))

    make_venv(SCRATCH / "t2" / ".venv")
    t2_src = make_pkg(SCRATCH / "t2" / "ws" / "pkg")
    saved = pycheck.os.environ.pop("VIRTUAL_ENV", None)
    try:
        found = pycheck.find_venv(t2_src)
        expect_true("find_venv: does not climb above the workspace root", found is None, repr(found))
        pycheck.os.environ["VIRTUAL_ENV"] = str(SCRATCH / "t2" / ".venv")
        found = pycheck.find_venv(t2_src)
        expect_true("find_venv: ambient VIRTUAL_ENV is the fallback", found == SCRATCH / "t2" / ".venv", repr(found))
        local_env = make_venv(SCRATCH / "t2" / "ws" / "pkg" / ".venv")
        found = pycheck.find_venv(t2_src)
        expect_true("find_venv: the file's own .venv beats VIRTUAL_ENV", found == local_env, repr(found))
    finally:
        pycheck.os.environ.pop("VIRTUAL_ENV", None)
        if saved is not None:
            pycheck.os.environ["VIRTUAL_ENV"] = saved
    shutil.rmtree(SCRATCH, ignore_errors=True)

# --------------------------------------------------------------------- check-evidence
print()
print("check-evidence.py - SubagentStop claims vs transcript")
shutil.rmtree(MARKERS, ignore_errors=True)
EV = SCRATCH / "evidence"
shutil.rmtree(EV, ignore_errors=True)
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
    [*edit("src/x.py"), *bash("ruff check src", "Exit code 1\nF401", ok=False), *bash("pytest tests -q", "12 passed")]
)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("earlier failure, later pass -> allow", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py"), *edit("src/y.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, "Refactored the loader. CHANGED: src/x.py, src/y.py"))
expect("edits, no run, no disclaimer -> block", rc, BLOCK, err)
expect_true("  ... reason lists the files", "src/x.py" in err and "src/y.py" in err, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(
    EVIDENCE, stop_event(p, a, "CHANGED: src/x.py\nRAN: nothing - validation not run, no test env here\nRISKS: unverified")
)
expect("edits, no run, explicit disclaimer -> allow", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, "CHANGED: src/x.py\nRAN: nothing\nDONE MEANS: not verified"))
expect("'not verified' is a disclaimer, not a claim -> allow", rc, ALLOW, err)

p, a = make_session([*bash("git log --oneline -3", "abc fix\ndef feat")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, "Found it at src/x.py:42; the loader drops the last row."))
expect("no edits, no claims -> allow", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, "Done. No type errors."))
expect("'no type errors' counts as a claim -> block", rc, BLOCK, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM, stop_hook_active=True))
expect("stop_hook_active -> allow (no loop)", rc, ALLOW, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("one strike: first stop blocked", rc, BLOCK, err)
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM))
expect("one strike: second stop allowed", rc, ALLOW, err)

rc, err = run_hook(EVIDENCE, stop_event(PROJECT / "nope.jsonl", "a0000000000000000", CLAIM))
expect("transcript missing -> fail open", rc, ALLOW, err)
expect_true("  ... but says so on stderr", "transcript not found" in err, err)

p, a = make_session([*edit("src/x.py")])
rc, err = run_hook(EVIDENCE, stop_event(p, a, CLAIM), bom=True)
expect("BOM-prefixed payload still blocks", rc, BLOCK, err)

proc = subprocess.run([sys.executable, str(EVIDENCE)], input=b"\xef\xbb\xbfnot json", capture_output=True)
expect("garbage payload fails open", proc.returncode, ALLOW, proc.stderr.decode())

shutil.rmtree(EV, ignore_errors=True)
shutil.rmtree(SCRATCH, ignore_errors=True)
shutil.rmtree(MARKERS, ignore_errors=True)

print()
if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All hook tests passed.")
