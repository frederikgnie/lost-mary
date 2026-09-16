#!/usr/bin/env bash
# Stage the package this case is about, in the run's working directory.
#
# Why this exists: the first two readings of spawn-contract scored 0.00 in both
# arms with "Agent called 0x". The prompt used to say the checkout was on a
# build box the model could not reach, which makes delegating work on files that
# cannot exist the obviously futile move - so the model described the brief
# instead of spawning, and a grader keyed on `tool_used: Agent` scores that zero.
# With a real package under the run's cwd, and no Edit or Write in the case's
# allowed_tools, delegating is the only route from this session to a changed file.
#
# The harness runs this with `bash <this file>` and cwd set to the run's working
# directory, before the model starts and before anything is spent; a non-zero
# exit aborts the run for free. It only runs under `--scaffold`.
set -euo pipefail

mkdir -p billing tests/billing

: > billing/__init__.py
: > tests/__init__.py
: > tests/billing/__init__.py

cat > billing/rate_limit.py <<'PY'
"""Per-service request throttling for the billing API."""

from __future__ import annotations

import time


class RateLimiter:
    """A token bucket holding at most `burst` tokens and refilling `rate` per second."""

    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last = time.time()

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self._last
        if elapsed <= 0:
            # Never hand out tokens for time that did not pass.
            return
        self._last = now
        self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)

    def allow(self, cost: float = 1.0) -> bool:
        """Take `cost` tokens if the bucket holds them; report whether it did."""
        self._refill()
        if self._tokens < cost:
            return False
        self._tokens -= cost
        return True
PY

cat > billing/auth.py <<'PY'
"""Bearer tokens issued to the internal services that call billing."""

from __future__ import annotations

ISSUED: dict[str, str] = {"meter": "tok-8f21", "invoice": "tok-4b07"}


def authenticate(service: str, token: str) -> bool:
    """True when `token` is the token issued to `service`."""
    return ISSUED.get(service) == token
PY

cat > tests/billing/test_rate_limit.py <<'PY'
from billing.rate_limit import RateLimiter


def test_burst_is_spent_then_refused() -> None:
    limiter = RateLimiter(rate=1.0, burst=2)
    assert limiter.allow()
    assert limiter.allow()
    assert not limiter.allow()
PY
