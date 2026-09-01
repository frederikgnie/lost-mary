#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_AGENTS="${HOME}/.claude/agents"
DEST_LIB="${HOME}/.claude/agent-library"
DEST_SKILLS="${HOME}/.claude/skills"
# Claude Code auto-loads ~/.claude/CLAUDE.md only. global-CLAUDE.md is inert
# unless that file imports it, so the installer maintains the import line.
CLAUDE_MD="${HOME}/.claude/CLAUDE.md"
IMPORT_LINE='@~/.claude/agent-library/global-CLAUDE.md'
DRY_RUN=0
NO_OVERWRITE=0
VERIFY=0
TIMESTAMP="$(date +%Y%m%d%H%M%S)"
DRIFT_COUNT=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [--dry-run] [--no-overwrite] [--verify]

Options:
  --dry-run       Print planned actions without writing files.
  --no-overwrite  Never replace existing destination files.
  --verify        Compare source files to installed destinations; no writes.
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
    --verify)
      VERIFY=1
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

verify_file() {
  local source_file="$1"
  local target_file="$2"

  if [[ ! -e "$target_file" && ! -L "$target_file" ]]; then
    log "MISSING: $target_file"
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
    return
  fi

  if cmp -s "$source_file" "$target_file" 2>/dev/null; then
    log "OK: $target_file"
  else
    log "DRIFT: $target_file"
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
  fi
}

count_import_lines() {
  # Exact-line count (CRLF-tolerant), not substring count: a commented or
  # quoted mention of the path must not satisfy the import requirement.
  awk -v line="$IMPORT_LINE" '{ t = $0; sub(/\r$/, "", t) } t == line { n++ } END { print n + 0 }' "$1"
}

ensure_import() {
  # Additive and idempotent: never rewrites the user's own global CLAUDE.md.
  # Also self-healing: editors and hand-pastes have been observed to duplicate
  # the import line (the installer itself always checks first). Converge back
  # to exactly one occurrence rather than merely tolerating the drift.
  local count=0
  if [[ -f "$CLAUDE_MD" ]]; then
    count="$(count_import_lines "$CLAUDE_MD")"
  fi
  if [[ "$count" -ge 1 ]]; then
    if [[ "$count" -eq 1 ]]; then
      log "Unchanged: import already present in $CLAUDE_MD"
      return 0
    fi
    if [[ "$NO_OVERWRITE" -eq 1 ]]; then
      log "WARNING: import line appears $count times in $CLAUDE_MD (--no-overwrite: leaving as is)"
      return 0
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
      log "[dry-run] dedupe import line in $CLAUDE_MD ($count -> 1, after backup)"
      return 0
    fi
    cp "$CLAUDE_MD" "${CLAUDE_MD}.backup.${TIMESTAMP}"
    log "Backing up existing $CLAUDE_MD -> ${CLAUDE_MD}.backup.${TIMESTAMP}"
    awk -v line="$IMPORT_LINE" '{ t = $0; sub(/\r$/, "", t) } t == line { if (seen++) next } { print }' \
      "$CLAUDE_MD" > "${CLAUDE_MD}.dedupe.tmp"
    mv "${CLAUDE_MD}.dedupe.tmp" "$CLAUDE_MD"
    log "Deduplicated import line in $CLAUDE_MD ($count -> 1)"
    return 0
  fi

  if [[ ! -f "$CLAUDE_MD" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
      log "[dry-run] create $CLAUDE_MD with import line"
      return 0
    fi
    mkdir -p "$(dirname "$CLAUDE_MD")"
    {
      printf '# Global Claude Code Instructions\n\n'
      printf '%s\n' "$IMPORT_LINE"
    } > "$CLAUDE_MD"
    log "Created $CLAUDE_MD with agent-library import"
    return 0
  fi

  if [[ "$NO_OVERWRITE" -eq 1 ]]; then
    log "Skipped (--no-overwrite): $CLAUDE_MD not modified"
    log "MANUAL ACTION REQUIRED — add this line to $CLAUDE_MD or the library stays inert:"
    log "    $IMPORT_LINE"
    return 0
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "[dry-run] append import line to $CLAUDE_MD (after backup)"
    return 0
  fi

  cp "$CLAUDE_MD" "${CLAUDE_MD}.backup.${TIMESTAMP}"
  log "Backing up existing $CLAUDE_MD -> ${CLAUDE_MD}.backup.${TIMESTAMP}"
  printf '\n%s\n' "$IMPORT_LINE" >> "$CLAUDE_MD"
  log "Appended agent-library import to $CLAUDE_MD"
}

verify_import() {
  if [[ ! -f "$CLAUDE_MD" ]]; then
    log "DRIFT: $CLAUDE_MD does not exist (global-CLAUDE.md would be inert)"
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
    return
  fi
  local count
  count="$(count_import_lines "$CLAUDE_MD")"
  if [[ "$count" -eq 1 ]]; then
    log "OK: import present exactly once in $CLAUDE_MD"
  elif [[ "$count" -eq 0 ]]; then
    log "DRIFT: $CLAUDE_MD does not import $IMPORT_LINE (global-CLAUDE.md would be inert)"
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
  else
    log "DRIFT: import line appears $count times in $CLAUDE_MD (run the installer to dedupe)"
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
  fi
}

if [[ "$VERIFY" -eq 0 ]]; then
  run mkdir -p "$DEST_AGENTS"
  run mkdir -p "$DEST_SKILLS"
  run mkdir -p "$DEST_LIB/orchestration"
  run mkdir -p "$DEST_LIB/scripts"
fi

for file in "$ROOT_DIR"/agents/*.md; do
  name="$(basename "$file")"
  target="$DEST_AGENTS/$name"
  if [[ "$VERIFY" -eq 1 ]]; then
    verify_file "$file" "$target"
  else
    install_file "$file" "$target"
  fi
done

# Skills become slash commands (~/.claude/skills/<name>/SKILL.md -> /<name>).
for skill_dir in "$ROOT_DIR"/skills/*/; do
  [[ -f "${skill_dir}SKILL.md" ]] || continue
  skill_name="$(basename "$skill_dir")"
  skill_target="$DEST_SKILLS/$skill_name"
  if [[ "$VERIFY" -eq 1 ]]; then
    verify_file "${skill_dir}SKILL.md" "$skill_target/SKILL.md"
  else
    run mkdir -p "$skill_target"
    install_file "${skill_dir}SKILL.md" "$skill_target/SKILL.md"
  fi
done

if [[ "$VERIFY" -eq 1 ]]; then
  verify_file "$ROOT_DIR/orchestration/handoff.md" "$DEST_LIB/orchestration/handoff.md"
  verify_file "$ROOT_DIR/orchestration/handoff.schema.json" "$DEST_LIB/orchestration/handoff.schema.json"
  verify_file "$ROOT_DIR/orchestration/worktree-rules.md" "$DEST_LIB/orchestration/worktree-rules.md"
  verify_file "$ROOT_DIR/orchestration/team-lead.md" "$DEST_LIB/orchestration/team-lead.md"
  verify_file "$ROOT_DIR/orchestration/delegation-rules.md" "$DEST_LIB/orchestration/delegation-rules.md"
  verify_file "$ROOT_DIR/orchestration/playbooks.md" "$DEST_LIB/orchestration/playbooks.md"
  verify_file "$ROOT_DIR/orchestration/principles.md" "$DEST_LIB/orchestration/principles.md"
  verify_file "$ROOT_DIR/scripts/validate-handoff.py" "$DEST_LIB/scripts/validate-handoff.py"
  verify_file "$ROOT_DIR/scripts/validate-handoff.sh" "$DEST_LIB/scripts/validate-handoff.sh"
  verify_file "$ROOT_DIR/scripts/validate-handoff.ps1" "$DEST_LIB/scripts/validate-handoff.ps1"
  verify_file "$ROOT_DIR/scripts/guard-readonly-bash.py" "$DEST_LIB/scripts/guard-readonly-bash.py"
  verify_file "$ROOT_DIR/scripts/check-handoff-hook.py" "$DEST_LIB/scripts/check-handoff-hook.py"
  verify_file "$ROOT_DIR/capabilities.md" "$DEST_LIB/capabilities.md"
  verify_file "$ROOT_DIR/global-CLAUDE.md" "$DEST_LIB/global-CLAUDE.md"
  verify_import
else
  install_file "$ROOT_DIR/orchestration/handoff.md" "$DEST_LIB/orchestration/handoff.md"
  install_file "$ROOT_DIR/orchestration/handoff.schema.json" "$DEST_LIB/orchestration/handoff.schema.json"
  install_file "$ROOT_DIR/orchestration/worktree-rules.md" "$DEST_LIB/orchestration/worktree-rules.md"
  install_file "$ROOT_DIR/orchestration/team-lead.md" "$DEST_LIB/orchestration/team-lead.md"
  install_file "$ROOT_DIR/orchestration/delegation-rules.md" "$DEST_LIB/orchestration/delegation-rules.md"
  install_file "$ROOT_DIR/orchestration/playbooks.md" "$DEST_LIB/orchestration/playbooks.md"
  install_file "$ROOT_DIR/orchestration/principles.md" "$DEST_LIB/orchestration/principles.md"
  install_file "$ROOT_DIR/scripts/validate-handoff.py" "$DEST_LIB/scripts/validate-handoff.py"
  install_file "$ROOT_DIR/scripts/validate-handoff.sh" "$DEST_LIB/scripts/validate-handoff.sh"
  install_file "$ROOT_DIR/scripts/validate-handoff.ps1" "$DEST_LIB/scripts/validate-handoff.ps1"
  install_file "$ROOT_DIR/scripts/guard-readonly-bash.py" "$DEST_LIB/scripts/guard-readonly-bash.py"
  install_file "$ROOT_DIR/scripts/check-handoff-hook.py" "$DEST_LIB/scripts/check-handoff-hook.py"
  install_file "$ROOT_DIR/capabilities.md" "$DEST_LIB/capabilities.md"
  install_file "$ROOT_DIR/global-CLAUDE.md" "$DEST_LIB/global-CLAUDE.md"
  ensure_import
fi

if [[ "$VERIFY" -eq 1 ]]; then
  echo
  if [[ "$DRIFT_COUNT" -eq 0 ]]; then
    echo "Verify result: OK (no drift)."
    exit 0
  fi
  echo "Verify result: DRIFT detected in $DRIFT_COUNT file(s)."
  exit 1
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  chmod +x "$DEST_LIB/scripts/validate-handoff.sh"
  chmod +x "$DEST_LIB/scripts/guard-readonly-bash.py"
  chmod +x "$DEST_LIB/scripts/check-handoff-hook.py"
else
  log "[dry-run] chmod +x $DEST_LIB/scripts/validate-handoff.sh"
  log "[dry-run] chmod +x $DEST_LIB/scripts/guard-readonly-bash.py"
  log "[dry-run] chmod +x $DEST_LIB/scripts/check-handoff-hook.py"
fi

echo
echo "Claude Code global agents installed in: $DEST_AGENTS"
echo "Claude Code skills (slash commands) installed in: $DEST_SKILLS"
echo "Claude Code shared orchestration assets installed in: $DEST_LIB"
echo "Operating rules imported into: $CLAUDE_MD"
echo
echo "Hooks are NOT installed automatically (they live in settings.json, which"
echo "this installer does not touch). Merge the 'hooks' block from"
echo "settings.example.json into ~/.claude/settings.json to enable enforcement."
echo "Restart the current Claude Code session only if this is the first time you created the agents directory."
