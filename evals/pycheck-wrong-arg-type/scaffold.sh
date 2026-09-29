#!/usr/bin/env bash
# Stage the package this case is about, in the run's working directory. The
# harness runs it as `bash scaffold.sh` with cwd set to the workspace, before the
# model starts; a non-zero exit aborts the run for free. Only under --scaffold.
set -euo pipefail

mkdir -p alerts
: > alerts/__init__.py

cat > alerts/oncall.py <<'PY'
"""Who gets paged."""

ONCALL_ADDRESS = "oncall@example.com"
PY

cat > alerts/notify.py <<'PY'
"""Outbound email, queued for the mail relay."""

_QUEUE: list[tuple[str, str, str]] = []


def send(to: list[str], subject: str, body: str) -> int:
    """Queue one email per recipient; return how many were queued."""
    for address in to:
        _QUEUE.append((address, subject, body))
    return len(to)
PY
