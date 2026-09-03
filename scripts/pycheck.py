#!/usr/bin/env python3
"""pycheck: run ruff and ty on Python files, as a PostToolUse hook or a CLI.

Why this exists
---------------
Telling the model "run the type checker" is a rule it can forget. Running the
type checker *for* it on every edit is a mechanism it cannot skip. In hook mode
the diagnostics go straight back to the model as feedback on the edit it just
made, while the file is still in its working memory.

Hook mode      pycheck.py --hook          (PostToolUse on Edit|Write|MultiEdit)
CLI mode       pycheck.py <paths...>      (used by the /validate skill)
               pycheck.py --changed       (every modified/untracked .py in this git repo)

Exit codes: hook mode 0 = clean or not applicable, 2 = diagnostics (stderr is
shown to the model). CLI mode 0 = clean, 1 = diagnostics, 3 = usage error.

Tool discovery, in order: the venv that owns the file (VIRTUAL_ENV, then a
`.venv` walking up from the file to one level above the nearest project root,
including the shared-sibling layout `<workspace>/<env-pkg>/.venv`), then PATH,
then `uvx`. Stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})
PY_SUFFIXES = frozenset({".py", ".pyi"})
SKIP_PARTS = frozenset({".venv", "venv", "site-packages", "node_modules", "__pycache__"})
MAX_LINES = 60
TOOL_TIMEOUT = 45


def is_checkable(path: Path) -> bool:
    if path.suffix not in PY_SUFFIXES or not path.is_file():
        return False
    if SKIP_PARTS & set(path.parts):
        return False
    # Scratch scripts are throwaway; checking them is noise.
    try:
        path.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
        return False
    except ValueError:
        return True


def project_root(start: Path) -> Path | None:
    """Nearest ancestor holding a pyproject.toml or .git."""
    for anc in [start, *start.parents]:
        if (anc / "pyproject.toml").is_file() or (anc / ".git").exists():
            return anc
    return None


def _venv_at(candidate: Path) -> Path | None:
    return candidate if (candidate / "pyvenv.cfg").is_file() else None


def find_venv(start: Path) -> Path | None:
    """Locate the virtualenv that owns `start`.

    Searches from the file's directory up to one level above the project root,
    so a workspace that keeps one shared env beside several package repos
    (`<ws>/EU_env/.venv` next to `<ws>/pkg-a/`, `<ws>/pkg-b/`) is found without
    ever wandering into unrelated projects further up the tree. The ambient
    VIRTUAL_ENV is only a fallback: the terminal that launched Claude Code may
    have some other project's env active.
    """
    root = project_root(start)
    ceiling = root.parent if root is not None else start.parent
    for anc in [start, *start.parents]:
        found = _venv_at(anc / ".venv")
        if found:
            return found
        try:
            for child in anc.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    found = _venv_at(child / ".venv")
                    if found:
                        return found
        except OSError:
            pass
        if anc == ceiling:
            break
    env = os.environ.get("VIRTUAL_ENV")
    if env and _venv_at(Path(env)):
        return Path(env)
    return None


def tool_command(name: str, venv: Path | None) -> list[str] | None:
    exe = f"{name}.exe" if os.name == "nt" else name
    if venv is not None:
        for sub in ("Scripts", "bin"):
            candidate = venv / sub / exe
            if candidate.is_file():
                return [str(candidate)]
    on_path = shutil.which(name)
    if on_path:
        return [on_path]
    if shutil.which("uvx"):
        return ["uvx", name]
    return None


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TOOL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {TOOL_TIMEOUT}s: {' '.join(cmd)}"
    except OSError as exc:
        return 127, f"could not run {cmd[0]}: {exc}"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def check_file(path: Path) -> tuple[list[str], str]:
    """Return (diagnostic lines, one-line description of the tools used)."""
    path = path.resolve()
    root = project_root(path.parent) or path.parent
    venv = find_venv(path.parent)
    lines: list[str] = []
    used: list[str] = []

    ruff = tool_command("ruff", venv)
    if ruff is None:
        lines.append("ruff: not found (venv, PATH, or uvx) - lint skipped")
    else:
        used.append("ruff")
        rc, out = run([*ruff, "check", "--no-fix", "--output-format", "concise", str(path)], root)
        if rc == 1 and out:
            lines.extend(
                line
                for line in out.splitlines()
                if line.strip() and not line.startswith(("Found ", "[*]", "No fixes"))
            )
        elif rc not in (0, 1):
            lines.append(f"ruff check failed to run (exit {rc}): {out[:300]}")
        rc, out = run([*ruff, "format", "--check", str(path)], root)
        if rc == 1:
            lines.append(f"ruff format: would reformat {path.name} (run `ruff format {path}`)")

    ty = tool_command("ty", venv)
    if ty is None:
        lines.append("ty: not found (venv, PATH, or uvx) - typecheck skipped")
    else:
        used.append("ty")
        cmd = [*ty, "check", "--output-format", "concise"]
        if venv is not None:
            cmd += ["--python", str(venv)]
        rc, out = run([*cmd, str(path)], root)
        if rc == 1 and out:
            lines.extend(
                line
                for line in out.splitlines()
                if line.strip() and not line.startswith(("Found ", "All checks"))
            )
        elif rc not in (0, 1):
            lines.append(f"ty check failed to run (exit {rc}): {out[:300]}")

    env_note = f" via {venv}" if venv is not None else " (no venv found)"
    return lines, f"{'+'.join(used) or 'no tools'}{env_note}"


def read_stdin_json() -> Any:
    """Bytes + utf-8-sig: PowerShell prepends a BOM when piping to a native exe,
    and text-mode stdin on Windows would mangle it."""
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read().decode("utf-8-sig", "replace") if buffer is not None else sys.stdin.read()
    return json.loads(raw.strip())


def hook_main() -> int:
    if os.environ.get("PYCHECK_DISABLE") == "1":
        return 0
    try:
        event = read_stdin_json()
    except (json.JSONDecodeError, ValueError) as exc:
        # Fail open, loudly: a hook that wedges every edit is worse than one
        # that occasionally misses.
        print(f"pycheck: unreadable hook payload ({exc}); skipping.", file=sys.stderr)
        return 0
    if not isinstance(event, dict) or event.get("tool_name") not in EDIT_TOOLS:
        return 0
    tool_input = event.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str):
        return 0
    path = Path(file_path)
    if not is_checkable(path):
        return 0

    lines, tools = check_file(path)
    if not lines:
        return 0
    shown = lines[:MAX_LINES]
    more = len(lines) - len(shown)
    print(f"pycheck: {len(lines)} finding(s) in {path}  [{tools}]", file=sys.stderr)
    print("\n".join(shown), file=sys.stderr)
    if more:
        print(f"... {more} more line(s) not shown", file=sys.stderr)
    print(
        "Fix these before moving on, or say explicitly why they are acceptable. "
        f"Re-run: python {Path(__file__).resolve()} {path}",
        file=sys.stderr,
    )
    return 2


def changed_python_files(cwd: Path) -> list[Path] | None:
    rc, out = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd)
    if rc != 0:
        return None
    files: list[Path] = []
    for line in out.splitlines():
        if len(line) < 4 or line[0] == "D" or line[1] == "D":
            continue
        name = line[3:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        candidate = cwd / name.strip().strip('"')
        if candidate.suffix in PY_SUFFIXES and candidate.is_file():
            files.append(candidate)
    return files


def cli_main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 3
    targets: list[Path] = []
    if argv == ["--changed"]:
        found = changed_python_files(Path.cwd())
        if found is None:
            print(
                "pycheck: not a git repository. In a multi-repo workspace run "
                "`pycheck.py --changed` inside each package, or pass paths.",
                file=sys.stderr,
            )
            return 3
        targets = found
    else:
        for arg in argv:
            p = Path(arg)
            if p.is_dir():
                targets.extend(sorted(q for q in p.rglob("*.py") if is_checkable(q)))
            elif is_checkable(p):
                targets.append(p)
    if not targets:
        print("pycheck: nothing to check.")
        return 0

    total = 0
    for path in targets:
        lines, tools = check_file(path)
        if lines:
            total += len(lines)
            print(f"== {path}  [{tools}]")
            print("\n".join(lines))
    print(f"pycheck: {len(targets)} file(s), {total} finding(s).")
    return 1 if total else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(hook_main() if args == ["--hook"] else cli_main(args))
