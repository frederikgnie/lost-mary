#!/usr/bin/env bash
# hooks/run.sh <script.py> [args...] - run a plugin hook script with a Python that actually exists.
#
# Why this exists: a versioned manifest cannot carry a machine-specific interpreter path, and on
# Windows the `python3` on PATH is often the Python Install Manager shim, which finds no runtimes
# once the profile directories are redirected. `claude plugin eval` sandboxes exactly those, so every
# plugin hook failed open in the with-arm - `hook_non_blocking_error`, exit 1, "NoInstallsError: No
# runtimes are installed" (observed 2026-09-17, Claude Code 2.1.274) - and the suite had been
# measuring the agents alone. Tried in order: $LOST_MARY_PYTHON, python3, python, the user's
# pymanager runtime directories. Stdin (the hook payload) passes through untouched. Fails OPEN with
# a notice when nothing works, like every hook in this library.
set -u

script="$1"
shift

works() {
    "$1" -c "import sys" </dev/null >/dev/null 2>&1
}

for candidate in "${LOST_MARY_PYTHON:-}" python3 python; do
    if [ -n "$candidate" ] && works "$candidate"; then
        exec "$candidate" "$script" "$@"
    fi
done

local_appdata=""
if [ -n "${LOCALAPPDATA:-}" ] && command -v cygpath >/dev/null 2>&1; then
    local_appdata="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null)"
fi
for candidate in "${local_appdata:-/nonexistent}"/Python/pythoncore-3*/python.exe \
    /c/Users/*/AppData/Local/Python/pythoncore-3*/python.exe; do
    if [ -x "$candidate" ] && works "$candidate"; then
        exec "$candidate" "$script" "$@"
    fi
done

echo "hooks/run.sh: no working python for $script - allowing" >&2
exit 0
