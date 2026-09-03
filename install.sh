#!/usr/bin/env bash
# Install the agent library (v2) into ~/.claude. See README.md.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_AGENTS="${HOME}/.claude/agents"
DEST_LIB="${HOME}/.claude/agent-library"
DEST_SKILLS="${HOME}/.claude/skills"
SETTINGS="${HOME}/.claude/settings.json"
# Claude Code auto-loads ~/.claude/CLAUDE.md only. global-CLAUDE.md is inert
# unless that file imports it, so the installer maintains the import line.
CLAUDE_MD="${HOME}/.claude/CLAUDE.md"
IMPORT_LINE='@~/.claude/agent-library/global-CLAUDE.md'
DRY_RUN=0
NO_OVERWRITE=0
VERIFY=0
TIMESTAMP="$(date +%Y%m%d%H%M%S)"
DRIFT_COUNT=0

# What v2 manages under ~/.claude/agent-library.
LIB_FILES=(scripts/pycheck.py scripts/check-evidence.py global-CLAUDE.md capabilities.md)
# What v1 installed and v2 no longer ships. Retired by renaming, never deleted:
# a stale agent file would keep registering a role the docs no longer describe,
# and a stale hook script would keep an old settings.json entry alive.
RETIRED_AGENTS=(architect researcher implementer debugger tester reviewer security-reviewer)
RETIRED_LIB=(orchestration scripts/check-handoff-hook.py scripts/guard-readonly-bash.py scripts/validate-handoff.py scripts/validate-handoff.sh scripts/validate-handoff.ps1)
V1_HOOK_PATTERN='check-handoff-hook\.py|guard-readonly-bash\.py'

usage() {
  cat <<'EOF'
Usage: ./install.sh [--dry-run] [--no-overwrite] [--verify]

Options:
  --dry-run       Print planned actions without writing files.
  --no-overwrite  Never replace or retire existing destination files.
  --verify        Compare source files to installed destinations; no writes.
  -h, --help      Show this help.

Notes:
  - Existing files are backed up as <name>.backup.<timestamp>; files this
    library used to ship (v1) are retired as <name>.retired.<timestamp>.
  - The installer manages ~/.claude/agents/<role>.md for its own roles,
    ~/.claude/skills/<name>/, ~/.claude/agent-library/, and the single import
    line in ~/.claude/CLAUDE.md. It never touches settings.json.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-overwrite) NO_OVERWRITE=1; shift ;;
    --verify) VERIFY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

log() { printf '%s\n' "$*"; }

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "[dry-run] $*"
  else
    "$@"
  fi
}

install_file() {
  local source_file="$1" target_file="$2"
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
  local source_file="$1" target_file="$2"
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

retire_path() {
  local target="$1"
  [[ -e "$target" || -L "$target" ]] || return 0
  if [[ "$NO_OVERWRITE" -eq 1 ]]; then
    log "WARNING: v1 file still installed: $target (--no-overwrite: leaving as is)"
    return 0
  fi
  local moved="${target}.retired.${TIMESTAMP}"
  log "Retiring v1 file: $target -> $moved"
  run mv "$target" "$moved"
}

verify_retired() {
  local target="$1"
  if [[ -e "$target" || -L "$target" ]]; then
    log "STALE: $target (v1 file still installed; run the installer to retire it)"
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
  fi
}

count_import_lines() {
  # Exact-line count (CRLF-tolerant), not substring count.
  awk -v line="$IMPORT_LINE" '{ t = $0; sub(/\r$/, "", t) } t == line { n++ } END { print n + 0 }' "$1"
}

ensure_import() {
  # Additive, idempotent and self-healing: converge to exactly one import line
  # without ever rewriting the user's own content.
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
    log "MANUAL ACTION REQUIRED - add this line to $CLAUDE_MD or the library stays inert:"
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

check_settings() {
  # Read-only. The installer never edits settings.json, but --verify should
  # say whether the hooks it depends on are wired, and whether v1 entries
  # linger (they would point at retired scripts and fail on every event).
  if [[ ! -f "$SETTINGS" ]]; then
    log "NOTE: $SETTINGS not found - hooks are not enabled (see settings.example.json)"
    return
  fi
  if grep -Eq "$V1_HOOK_PATTERN" "$SETTINGS"; then
    log "STALE: $SETTINGS still references v1 hook scripts - replace the hooks block with settings.example.json"
    DRIFT_COUNT=$((DRIFT_COUNT + 1))
  fi
  if grep -q 'pycheck.py' "$SETTINGS"; then
    log "OK: $SETTINGS wires pycheck.py"
  else
    log "NOTE: $SETTINGS does not wire pycheck.py - hooks not enabled (merge settings.example.json)"
  fi
}

if [[ "$VERIFY" -eq 0 ]]; then
  run mkdir -p "$DEST_AGENTS" "$DEST_SKILLS" "$DEST_LIB/scripts"
fi

for file in "$ROOT_DIR"/agents/*.md; do
  name="$(basename "$file")"
  if [[ "$VERIFY" -eq 1 ]]; then
    verify_file "$file" "$DEST_AGENTS/$name"
  else
    install_file "$file" "$DEST_AGENTS/$name"
  fi
done
for name in "${RETIRED_AGENTS[@]}"; do
  if [[ "$VERIFY" -eq 1 ]]; then
    verify_retired "$DEST_AGENTS/$name.md"
  else
    retire_path "$DEST_AGENTS/$name.md"
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

for rel in "${LIB_FILES[@]}"; do
  if [[ "$VERIFY" -eq 1 ]]; then
    verify_file "$ROOT_DIR/$rel" "$DEST_LIB/$rel"
  else
    install_file "$ROOT_DIR/$rel" "$DEST_LIB/$rel"
  fi
done
for rel in "${RETIRED_LIB[@]}"; do
  if [[ "$VERIFY" -eq 1 ]]; then
    verify_retired "$DEST_LIB/$rel"
  else
    retire_path "$DEST_LIB/$rel"
  fi
done

if [[ "$VERIFY" -eq 1 ]]; then
  verify_import
  check_settings
  echo
  if [[ "$DRIFT_COUNT" -eq 0 ]]; then
    echo "Verify result: OK (no drift)."
    exit 0
  fi
  echo "Verify result: DRIFT detected in $DRIFT_COUNT item(s)."
  exit 1
fi

ensure_import

if [[ "$DRY_RUN" -eq 0 ]]; then
  chmod +x "$DEST_LIB/scripts/pycheck.py" "$DEST_LIB/scripts/check-evidence.py"
else
  log "[dry-run] chmod +x $DEST_LIB/scripts/pycheck.py $DEST_LIB/scripts/check-evidence.py"
fi

echo
echo "Agents installed in:          $DEST_AGENTS  (explore, implement, review)"
echo "Skills installed in:          $DEST_SKILLS  (/lost-mary, /validate, /pr)"
echo "Hook scripts + rules in:      $DEST_LIB"
echo "Operating rules imported by:  $CLAUDE_MD"
echo
echo "Hooks are NOT installed automatically (they live in settings.json, which this"
echo "installer never touches). Merge the 'hooks' block from settings.example.json"
echo "into ~/.claude/settings.json, then restart Claude Code. Run ./install.sh --verify"
echo "afterwards; it reports whether the hooks are wired."
