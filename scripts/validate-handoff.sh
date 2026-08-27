#!/usr/bin/env bash
# Thin wrapper so leads can run: ./scripts/validate-handoff.sh handoff.json
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/validate-handoff.py" "$@"
