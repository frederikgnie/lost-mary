#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HOME}/.claude/agents"

mkdir -p "$DEST"

for file in "$ROOT_DIR"/agents/*.md; do
  name="$(basename "$file")"
  target="$DEST/$name"

  if [[ -e "$target" && ! -L "$target" ]]; then
    backup="${target}.backup.$(date +%Y%m%d%H%M%S)"
    echo "Backing up existing $target -> $backup"
    mv "$target" "$backup"
  fi

  cp "$file" "$target"
  echo "Installed $name"
done

echo
echo "Claude Code global agents installed in: $DEST"
echo "Restart the current Claude Code session only if this is the first time you created the agents directory."
