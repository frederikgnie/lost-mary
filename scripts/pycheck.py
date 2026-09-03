#!/usr/bin/env python3
"""pycheck: run ruff and ty on Python files, as a PostToolUse hook or a CLI.

Why this exists
---------------
Telling the model "run the type checker" is a rule it can forget. Running the
type checker *for* it on every edit is a mechanism it cannot skip. In hook mode
the diagnostics go straight back to the model as feedback on the edit it just
made, while the file is still in its working memory.

What it reports
---------------
Only what the edit introduced. For a git-tracked file the HEAD version is
checked as well and matching diagnostics are subtracted, so pre-existing
problems in a file you touched are counted, not replayed; a format nag is
raised only when the file was formatted at HEAD. Untracked files are all new.
Each project's own ruff/ty `exclude` settings are honoured (--force-exclude).

Hook mode      pycheck.py --hook               (PostToolUse on Edit|Write|MultiEdit)
CLI mode       pycheck.py [--all] <paths...>   (used by the /validate skill)
               pycheck.py [--all] --changed    (every modified/untracked .py in the
                                                git repo that contains the cwd)
`--all` disables the HEAD baseline and shows every diagnostic.

Exit codes: hook mode 0 = clean or not applicable, 2 = something to show
(stderr goes to the model). CLI mode 0 = clean, 1 = findings, 3 = usage error.

Tool discovery, in order: a `.venv` walking up from the file to one level above
the project root (a linked git worktree is mapped back onto its main checkout
first); a shared sibling env `<workspace>/<env-pkg>/.venv` only when it owns
the file (an editable-install .pth points into the project); the terminal's
ambient VIRTUAL_ENV; then ruff/ty on PATH, then `uvx`. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})
PY_SUFFIXES = frozenset({".py", ".pyi"})
SKIP_PARTS = frozenset({".venv", "venv", "site-packages", "node_modules", "__pycache__"})
MAX_LINES = 60
TOOL_TIMEOUT = 45
GIT_TIMEOUT = 20
# ruff/ty concise diagnostics: "<path>:<line>:<col>: <message>"
DIAG_PREFIX_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s*")
NOISE_PREFIXES = ("Found ", "[*]", "No fixes", "All checks passed", "warning: No Python files", "No Python files")


def utf8_stdio() -> None:
    """Emit UTF-8 regardless of the console code page; a Danish path or a
    diagnostic with non-ASCII in it must not reach the model as mojibake."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except ValueError:
                pass


def run(cmd: list[str], cwd: Path, timeout: int = TOOL_TIMEOUT) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {' '.join(cmd)}"
    except OSError as exc:
        return 127, "", f"could not run {cmd[0]}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def git(args: list[str], cwd: Path) -> tuple[int, str]:
    rc, out, _ = run(["git", *args], cwd, timeout=GIT_TIMEOUT)
    return rc, out.strip()


def same_or_under(path: Path, ancestor: Path) -> bool:
    try:
        path.resolve().relative_to(ancestor.resolve())
        return True
    except (ValueError, OSError):
        return False


def is_checkable(path: Path) -> bool:
    if path.suffix not in PY_SUFFIXES or not path.is_file():
        return False
    if SKIP_PARTS & set(path.parts):
        return False
    # Scratch scripts are throwaway; checking them is noise. (Tests opt back in.)
    if os.environ.get("PYCHECK_ALLOW_TEMP") == "1":
        return True
    return not same_or_under(path, Path(tempfile.gettempdir()))


# ----------------------------------------------------------------------------- project + venv


def project_root(start: Path) -> Path | None:
    """Nearest ancestor holding a pyproject.toml or a .git (dir or worktree file)."""
    for anc in [start, *start.parents]:
        if (anc / "pyproject.toml").is_file() or (anc / ".git").exists():
            return anc
    return None


def git_toplevel(start: Path) -> Path | None:
    if not start.is_dir():
        start = start.parent
    rc, out = git(["rev-parse", "--show-toplevel"], start)
    return Path(out) if rc == 0 and out else None


def main_checkout(start: Path) -> Path | None:
    """Root of the main checkout - differs from the toplevel inside a linked worktree."""
    if not start.is_dir():
        start = start.parent
    rc, out = git(["rev-parse", "--git-common-dir"], start)
    if rc != 0 or not out:
        return None
    common = Path(out)
    if not common.is_absolute():
        common = (start / common).resolve()
    return common.parent if common.name == ".git" else None


def venv_anchor(start: Path) -> Path:
    """Where the venv search begins: the file's directory, mapped onto the main
    checkout when the file lives in a linked worktree (`.claude/worktrees/...`),
    whose own tree never carries the environment."""
    top = git_toplevel(start)
    main = main_checkout(start)
    if top is not None and main is not None and top.resolve() != main.resolve():
        try:
            return main / start.resolve().relative_to(top.resolve())
        except ValueError:
            return main
    return start


def _venv_at(candidate: Path) -> Path | None:
    return candidate if (candidate / "pyvenv.cfg").is_file() else None


def site_packages(venv: Path) -> list[Path]:
    found = []
    windows = venv / "Lib" / "site-packages"
    if windows.is_dir():
        found.append(windows)
    found.extend(p for p in (venv / "lib").glob("python*/site-packages") if p.is_dir())
    return found


def venv_owns(venv: Path, target: Path) -> bool:
    """True when an editable-install .pth in `venv` points at a directory that
    contains `target` - the signal that this env is the project's own."""
    for packages in site_packages(venv):
        for pth in packages.glob("*.pth"):
            try:
                lines = pth.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line or line.startswith(("import ", "#")):
                    continue
                candidate = Path(line)
                if candidate.is_absolute() and candidate.is_dir() and same_or_under(target, candidate):
                    return True
    return False


def find_venv(start: Path) -> tuple[Path | None, str]:
    """Return (venv, how-it-was-found). See the module docstring for the order."""
    anchor = venv_anchor(start)
    root = project_root(anchor)
    ceiling = root.parent if root is not None else anchor.parent
    chain = [anchor, *anchor.parents]

    for anc in chain:
        found = _venv_at(anc / ".venv")
        if found:
            return found, "project .venv"
        if anc == ceiling:
            break

    for anc in chain:
        try:
            children = [c for c in anc.iterdir() if c.is_dir() and not c.name.startswith(".")]
        except OSError:
            children = []
        for child in children:
            found = _venv_at(child / ".venv")
            if found and venv_owns(found, anchor):
                return found, "shared venv that owns this project"
        if anc == ceiling:
            break

    env = os.environ.get("VIRTUAL_ENV")
    if env and _venv_at(Path(env)):
        return Path(env), "ambient VIRTUAL_ENV"
    return None, "no venv found"


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


def has_ruff_config(start: Path) -> bool:
    """ruff format is only meaningful where the project chose a style."""
    for anc in [start, *start.parents]:
        if (anc / "ruff.toml").is_file() or (anc / ".ruff.toml").is_file():
            return True
        pyproject = anc / "pyproject.toml"
        if pyproject.is_file():
            try:
                if "[tool.ruff" in pyproject.read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                pass
    return False


# ----------------------------------------------------------------------------- checking


@dataclass
class CheckResult:
    new: list[str] = field(default_factory=list)
    preexisting: int = 0
    notes: list[str] = field(default_factory=list)
    tools: str = ""

    @property
    def has_output(self) -> bool:
        return bool(self.new or self.notes)


def diag_key(line: str) -> str:
    """A diagnostic without its location, so the HEAD copy and the working copy compare."""
    return DIAG_PREFIX_RE.sub("", line).strip()


def head_version(path: Path, top: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(top.resolve()).as_posix()
    except ValueError:
        return None
    rc, _ = git(["ls-files", "--error-unmatch", "--", rel], top)
    if rc != 0:
        return None
    rc, out, _ = run(["git", "show", f"HEAD:{rel}"], top, timeout=GIT_TIMEOUT)
    return out if rc == 0 else None


class Checker:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.root = project_root(self.path.parent) or self.path.parent
        self.venv, how = find_venv(self.path.parent)
        self.ruff = tool_command("ruff", self.venv)
        self.ty = tool_command("ty", self.venv)
        self.ruff_config = has_ruff_config(self.path.parent)
        used = [n for n, c in (("ruff", self.ruff), ("ty", self.ty)) if c]
        where = f"{how}: {self.venv}" if self.venv else how
        self.tools = f"{'+'.join(used) or 'no tools'} - {where}"

    def _diagnostics(self, cmd: list[str], target: Path, notes: list[str], label: str) -> list[str]:
        rc, out, err = run([*cmd, str(target)], self.root)
        text = (out + "\n" + err).strip()
        if rc == 1:
            return [line for line in text.splitlines() if line.strip() and not line.startswith(NOISE_PREFIXES)]
        if rc != 0:
            notes.append(f"{label} failed to run (exit {rc}): {text[:300]}")
        return []

    def collect(self, target: Path, notes: list[str]) -> tuple[list[str], bool | None]:
        """Diagnostics for `target` and whether ruff would reformat it (None = not checked)."""
        lines: list[str] = []
        would_reformat: bool | None = None
        if self.ruff is None:
            notes.append("ruff: not found (venv, PATH, or uvx) - lint skipped")
        else:
            lines += self._diagnostics(
                [*self.ruff, "check", "--no-fix", "--force-exclude", "--output-format", "concise"],
                target,
                notes,
                "ruff check",
            )
            if self.ruff_config:
                rc, _, err = run([*self.ruff, "format", "--check", "--force-exclude", str(target)], self.root)
                if rc in (0, 1):
                    would_reformat = rc == 1
                else:
                    notes.append(f"ruff format --check failed to run (exit {rc}): {err[:200]}")
        if self.ty is None:
            notes.append("ty: not found (venv, PATH, or uvx) - typecheck skipped")
        else:
            cmd = [*self.ty, "check", "--force-exclude", "--output-format", "concise"]
            if self.venv is not None:
                cmd += ["--python", str(self.venv)]
            lines += self._diagnostics(cmd, target, notes, "ty check")
        return lines, would_reformat

    def check(self, *, baseline: bool = True) -> CheckResult:
        result = CheckResult(tools=self.tools)
        current, cur_fmt = self.collect(self.path, result.notes)

        baseline_keys: Counter[str] = Counter()
        base_fmt: bool | None = None
        have_baseline = False
        if baseline:
            top = git_toplevel(self.path.parent)
            head = head_version(self.path, top) if top is not None else None
            if head is not None:
                # The HEAD copy must sit beside the file so relative imports and
                # per-directory config resolve the same way. Removed immediately.
                tmp = self.path.parent / f"__pycheck_head_{uuid.uuid4().hex[:8]}{self.path.suffix}"
                try:
                    tmp.write_text(head, encoding="utf-8", newline="")
                    base_lines, base_fmt = self.collect(tmp, [])
                    baseline_keys = Counter(diag_key(line) for line in base_lines)
                    have_baseline = True
                except OSError:
                    have_baseline = False
                finally:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

        for line in current:
            key = diag_key(line)
            if baseline_keys.get(key, 0) > 0:
                baseline_keys[key] -= 1
                result.preexisting += 1
            else:
                result.new.append(line)

        if cur_fmt:
            if have_baseline and base_fmt:
                result.preexisting += 1  # already unformatted at HEAD: not this edit's doing
            else:
                result.new.append(f'ruff format: would reformat {self.path.name} (run `ruff format "{self.path}"`)')
        return result


def check_file(path: Path, *, baseline: bool = True) -> CheckResult:
    return Checker(path).check(baseline=baseline)


# ----------------------------------------------------------------------------- hook mode


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
        # that occasionally misses - but it must never go quiet.
        print(f"pycheck: unreadable hook payload ({exc}); skipping.", file=sys.stderr)
        return 0
    if not isinstance(event, dict):
        print("pycheck: hook payload was not an object; skipping.", file=sys.stderr)
        return 0
    tool = event.get("tool_name")
    if tool is None:
        print(f"pycheck: payload has no tool_name (keys: {sorted(event)}); skipping.", file=sys.stderr)
        return 0
    if tool not in EDIT_TOOLS:
        return 0
    tool_input = event.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str):
        keys = sorted(tool_input) if isinstance(tool_input, dict) else type(tool_input).__name__
        print(f"pycheck: {tool} payload has no tool_input.file_path ({keys}); skipping.", file=sys.stderr)
        return 0
    path = Path(file_path)
    if not is_checkable(path):
        return 0

    result = check_file(path)
    if not result.has_output:
        return 0
    header = f"pycheck: {len(result.new)} new finding(s) in {path}  [{result.tools}]"
    if result.preexisting:
        header += f"  ({result.preexisting} pre-existing not shown)"
    print(header, file=sys.stderr)
    shown = result.new[:MAX_LINES]
    if shown:
        print("\n".join(shown), file=sys.stderr)
    if len(result.new) > len(shown):
        print(f"... {len(result.new) - len(shown)} more line(s) not shown", file=sys.stderr)
    for note in result.notes:
        print(f"note: {note}", file=sys.stderr)
    if result.new:
        print(
            "Fix these before moving on, or say explicitly why they are acceptable. "
            f'Re-run: "{sys.executable}" "{Path(__file__).resolve()}" "{path}"',
            file=sys.stderr,
        )
    return 2


# ----------------------------------------------------------------------------- CLI mode


def changed_python_files(cwd: Path) -> tuple[Path, list[Path]] | None:
    """Modified and untracked .py files of the repo containing `cwd`, from any cwd."""
    rc, top_str = git(["rev-parse", "--show-toplevel"], cwd)
    if rc != 0 or not top_str:
        return None
    top = Path(top_str)
    rc, out, _ = run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], top, timeout=GIT_TIMEOUT)
    if rc != 0:
        return None
    files: list[Path] = []
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, name = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            i += 1  # the original path follows as its own NUL-terminated entry
        if "D" in status:
            continue
        candidate = top / name
        if candidate.suffix in PY_SUFFIXES and candidate.is_file() and is_checkable(candidate):
            files.append(candidate)
    return top, files


def cli_main(argv: list[str]) -> int:
    show_all = "--all" in argv
    argv = [a for a in argv if a != "--all"]
    if not argv or argv[0] in ("-h", "--help"):
        print((__doc__ or "").strip(), file=sys.stderr)
        return 3
    targets: list[Path] = []
    if argv == ["--changed"]:
        found = changed_python_files(Path.cwd())
        if found is None:
            print(
                "pycheck: not inside a git repository. In a multi-repo workspace run "
                "`pycheck.py --changed` inside a package, or pass paths.",
                file=sys.stderr,
            )
            return 3
        top, targets = found
        print(f"pycheck: repository {top}")
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

    total_new = total_pre = 0
    for path in targets:
        result = check_file(path, baseline=not show_all)
        total_new += len(result.new)
        total_pre += result.preexisting
        if result.has_output:
            print(f"== {path}  [{result.tools}]")
            print("\n".join(result.new))
            for note in result.notes:
                print(f"note: {note}")
    summary = f"pycheck: {len(targets)} file(s), {total_new} new finding(s)"
    if total_pre:
        summary += f", {total_pre} pre-existing not shown (use --all)"
    print(summary + ".")
    return 1 if total_new else 0


if __name__ == "__main__":
    utf8_stdio()
    args = sys.argv[1:]
    sys.exit(hook_main() if args == ["--hook"] else cli_main(args))
