#!/usr/bin/env python3
"""Install the Codex adapter from canonical Claude sources. Python 3.11+, stdlib only.

No Claude files or Codex global configuration are changed. Re-run after pulling
updates; --verify checks all bundled bytes without writing. --dest names the
skill directory itself, not its parent. Existing managed edits are backed up.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = ".lost-mary-managed"
IDENTITY = b"lost-mary codex adapter v1\n"


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


def install(dest: Path, files: dict[str, bytes], *, verify: bool = False, dry_run: bool = False) -> int:
    # Never follow a redirected managed file out of this skill's directory.
    resolved = dest.resolve()
    for relative in files:
        path = dest / relative
        if path.resolve() != resolved / relative:
            raise ValueError(f"refusing redirected managed path: {path}")
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
    if verify:
        for name in changed:
            print(f"DRIFT {name}")
        print("Codex adapter differs; reinstall." if changed else "Codex adapter verified.")
        return int(bool(changed))
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    for name in changed:
        path = dest / name
        print(f"{'WOULD WRITE' if dry_run else 'WRITE'} {path}")
        if dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_name(f"{path.name}.backup.{stamp}"))
        path.write_bytes(files[name])
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
