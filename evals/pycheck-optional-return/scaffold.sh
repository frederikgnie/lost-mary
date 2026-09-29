#!/usr/bin/env bash
# Stage the package this case is about, in the run's working directory. The
# harness runs it as `bash scaffold.sh` with cwd set to the workspace, before the
# model starts; a non-zero exit aborts the run for free. Only under --scaffold.
set -euo pipefail

mkdir -p fx
: > fx/__init__.py

cat > fx/types.py <<'PY'
"""Shared type aliases for the fx package."""

# A rate is missing for currencies the nightly feed did not publish.
Rate = float | None
PY

cat > fx/rates.py <<'PY'
"""Reference FX rates, refreshed nightly from the treasury feed."""

from fx.types import Rate

_RATES: dict[str, float] = {"EUR": 1.0, "USD": 0.92, "GBP": 1.17, "DKK": 0.134}


def lookup_rate(code: str) -> Rate:
    """EUR per one unit of `code`."""
    return _RATES.get(code.upper())
PY
