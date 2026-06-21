"""Shared fixtures for server tests.

The fixed-window limiter anchors its windows to the wall clock
(``int(time.time() // window_seconds)``). A burst of requests that happens to
straddle a real window boundary therefore splits its count across two windows,
so enforcement assertions (``assert 429 in statuses``) fail intermittently —
roughly ``burst_duration / window_seconds`` of the time (~7% for the 60s OpenAI
config, whose timing makes a 62-request burst take ~4s).

The ``aligned_clock`` autouse fixture removes that nondeterminism by anchoring
every limiter's clock to the *start* of a window at test start, while still
letting it advance via the monotonic clock. Bursts thus never straddle a
boundary, yet sleep-based behaviour (token-bucket refill) is preserved.
"""

from __future__ import annotations

import time

import pytest

# Anchor to a multiple of one day so any window size dividing 86400
# (10s, 60s, 600s, 3600s, ...) starts at offset 0 — bursts never straddle.
_SECONDS_PER_DAY = 86_400
_WINDOW_ALIGNED_EPOCH = (1_700_000_000 // _SECONDS_PER_DAY) * _SECONDS_PER_DAY

# Limiter modules that read ``time.time()`` for windowing/refill.
_LIMITER_MODULES = (
    "mocklimit.ratelimit.fixed_window",
    "mocklimit.ratelimit.sliding_window",
    "mocklimit.ratelimit.token_bucket",
)


class _AnchoredClock:
    """Wall clock anchored to a window boundary that advances monotonically."""

    def __init__(self, base: float) -> None:
        self._base = base
        self._mono0 = time.monotonic()

    def time(self) -> float:
        """Return window-aligned wall-clock time (offset 0 at fixture start)."""
        return self._base + (time.monotonic() - self._mono0)

    def monotonic(self) -> float:
        """Delegate to the real monotonic clock (unused by limiters today)."""
        return time.monotonic()


@pytest.fixture(autouse=True)
def aligned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anchor limiter clocks to a window start so request bursts are deterministic."""
    clock = _AnchoredClock(float(_WINDOW_ALIGNED_EPOCH))
    for module in _LIMITER_MODULES:
        monkeypatch.setattr(f"{module}.time", clock, raising=False)
