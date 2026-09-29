#!/usr/bin/env bash
# Stage the package this case is about, in the run's working directory. The
# harness runs it as `bash scaffold.sh` with cwd set to the workspace, before the
# model starts; a non-zero exit aborts the run for free. Only under --scaffold.
set -euo pipefail

mkdir -p client
: > client/__init__.py

cat > client/fetch.py <<'PY'
"""HTTP fetch helpers for the price feeds."""

import json
import urllib.request


def _get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def fetch(url: str, timeout: float = 10.0) -> bytes:
    return _get(url, timeout)


def fetch_json(url: str) -> object:
    return json.loads(_get(url, 5.0))
PY
