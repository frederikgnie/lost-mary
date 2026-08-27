#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_AGENTS="${HOME}/.claude/agents"
DEST_LIB="${HOME}/.claude/agent-library"
DRY_RUN=0
NO_OVERWRITE=0
TIMESTAMP="$(date +%Y%m%d%H%M%S)"

usage() {
  cat <<'EOF'
Usage: ./install.sh [--dry-run] [--no-overwrite]

Options:
  --dry-run       Print planned actions without writing files.
  --no-overwrite  Never replace existing destination files.
  -h, --help      Show this help.

Notes:
  - Existing files are backed up with a .backup.<timestamp> suffix unless --no-overwrite is set.
  - Installer manages only ~/.claude/agents and ~/.claude/agent-library.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-overwrite)
      NO_OVERWRITE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log() {
  printf '%s\n' "$*"
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "[dry-run] $*"
  else
    "$@"
  fi
}

install_file() {
  local source_file="$1"
  local target_file="$2"

  if [[ -e "$target_file" || -L "$target_file" ]]; then
    if cmp -s "$source_file" "$target_file" 2>/dev/null; then
      log "Unchanged: $target_file"
      return
    fi

    if [[ "$NO_OVERWRITE" -eq 1 ]]; then
      log "Skipped (exists): $target_file"
      return
    fi

    local backup_file="${target_file}.backup.${TIMESTAMP}"
    log "Backing up existing $target_file -> $backup_file"
    run mv "$target_file" "$backup_file"
  fi

  run cp "$source_file" "$target_file"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Planned install $(basename "$target_file")"
  else
    log "Installed $(basename "$target_file")"
  fi
}

run mkdir -p "$DEST_AGENTS"
run mkdir -p "$DEST_LIB/orchestration"
run mkdir -p "$DEST_LIB/scripts"

for file in "$ROOT_DIR"/agents/*.md; do
  name="$(basename "$file")"
  target="$DEST_AGENTS/$name"
  install_file "$file" "$target"
done

install_file "$ROOT_DIR/orchestration/handoff.md" "$DEST_LIB/orchestration/handoff.md"
install_file "$ROOT_DIR/orchestration/handoff.schema.json" "$DEST_LIB/orchestration/handoff.schema.json"
install_file "$ROOT_DIR/scripts/validate-handoff.py" "$DEST_LIB/scripts/validate-handoff.py"
install_file "$ROOT_DIR/scripts/validate-handoff.sh" "$DEST_LIB/scripts/validate-handoff.sh"
install_file "$ROOT_DIR/scripts/validate-handoff.ps1" "$DEST_LIB/scripts/validate-handoff.ps1"

if [[ "$DRY_RUN" -eq 0 ]]; then
  chmod +x "$DEST_LIB/scripts/validate-handoff.sh"
else
  log "[dry-run] chmod +x $DEST_LIB/scripts/validate-handoff.sh"
fi

echo
echo "Claude Code global agents installed in: $DEST_AGENTS"
echo "Claude Code shared orchestration assets installed in: $DEST_LIB"
echo "Restart the current Claude Code session only if this is the first time you created the agents directory."
