#!/usr/bin/env python3
"""Install the Codex adapter from canonical Claude sources. Python 3.11+, stdlib only.

No Claude files or Codex global configuration are changed. Re-run after pulling
updates; --verify checks all bundled bytes without writing. --dest names the
skill directory itself, not its parent. A managed file edited since the last
install is backed up before it is replaced; one the installer wrote is not.
A file the bundle no longer ships (RETIRED) is renamed aside, never deleted.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ".lost-mary-managed"
IDENTITY = b"lost-mary codex adapter v1\n"
# sha256 of each managed file as last written, so only local edits get a backup.
STATE = ".lost-mary-installed.json"
# Paths an earlier bundle shipped and this one does not. Retire, never delete.
RETIRED: tuple[str, ...] = ()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    # A failed write/replacement must leave the previous working file intact.
    descriptor, temporary = tempfile.mkstemp(prefix=".lost-mary-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def bundle(root: Path) -> dict[str, bytes]:
    """Package current sources verbatim; no generated procedures to maintain."""
    paths = {
        "SKILL.md": "codex/SKILL.md",
        "agents/openai.yaml": "codex/openai.yaml",
        "references/procedure.md": "skills/lost-mary/SKILL.md",
        "references/validate.md": "skills/validate/SKILL.md",
        "references/pr.md": "skills/pr/SKILL.md",
    }
    for role in ("explore", "implement", "review"):
        paths[f"references/{role}.md"] = f"agents/{role}.md"
    for script in ("pycheck.py", "test-scope.py"):
        paths[f"scripts/{script}"] = f"scripts/{script}"
    return {MARKER: IDENTITY, **{dest: (root / src).read_bytes() for dest, src in paths.items()}}


def install(
    dest: Path,
    files: dict[str, bytes],
    *,
    verify: bool = False,
    dry_run: bool = False,
    retired: tuple[str, ...] = RETIRED,
) -> int:
    # Never follow a redirected managed file out of this skill's directory.
    resolved = dest.resolve()
    for relative in [*files, STATE, *retired]:
        path = dest / relative
        if path.resolve() != resolved / relative:
            raise ValueError(f"refusing redirected managed path: {path}")
        if path.exists() and not path.is_file():
            raise ValueError(f"managed file is not a regular file: {path}")
    if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
        raise ValueError(f"not an ordinary skill directory: {dest}")
    if (
        dest.exists()
        and any(dest.iterdir())
        and (not (dest / MARKER).is_file() or (dest / MARKER).read_bytes() != IDENTITY)
    ):
        raise ValueError(f"refusing unowned skill directory: {dest}")

    changed = [
        name for name, data in files.items() if not (dest / name).is_file() or (dest / name).read_bytes() != data
    ]
    stale = [name for name in retired if (dest / name).is_file()]
    if verify:
        for name in changed:
            print(f"DRIFT {name}")
        for name in stale:
            print(f"RETIRED {name}")
        print("Codex adapter differs; reinstall." if changed or stale else "Codex adapter verified.")
        return int(bool(changed or stale))
    try:
        written = json.loads((dest / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        written = {}  # no record yet: back up every file it replaces
    if not isinstance(written, dict):
        written = {}
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    for name in stale:
        path = dest / name
        print(f"{'WOULD RETIRE' if dry_run else 'RETIRE'} {path}")
        if not dry_run:
            path.rename(path.with_name(f"{path.name}.retired.{stamp}"))
    for name in changed:
        path = dest / name
        print(f"{'WOULD WRITE' if dry_run else 'WRITE'} {path}")
        if dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and written.get(name) != digest(path.read_bytes()):
            shutil.copy2(path, path.with_name(f"{path.name}.backup.{stamp}"))
        write_atomic(path, files[name])
    if not dry_run:
        record = (json.dumps({name: digest(data) for name, data in sorted(files.items())}, indent=2) + "\n").encode()
        current = b""
        with contextlib.suppress(OSError):
            current = (dest / STATE).read_bytes()
        if current != record:
            write_atomic(dest / STATE, record)
    print(f"{'Dry run' if dry_run else 'Installed'}: {dest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path.home() / ".agents" / "skills" / "lost-mary")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        return install(args.dest.expanduser(), bundle(ROOT), verify=args.verify, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"install-codex: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
