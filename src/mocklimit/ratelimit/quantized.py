"""Quantized rate limiter that enforces an outer limit at a finer inner granularity."""

from __future__ import annotations

import threading

from .fixed_window import FixedWindowLimiter
from .models import LimitResult

__all__ = ["QuantizedLimiter"]


class QuantizedLimiter:
    """Enforce an outer rate limit using a finer-grained inner window.

    Simulates behaviour like OpenAI's "600 RPM enforced as 10 RPS":
    the *outer* window defines the headline limit while the *inner*
    window prevents sub-window bursts that the outer window alone
    would allow.

    Uses a per-key ``threading.Lock`` so that the peek-then-consume
    sequence cannot be interleaved by another thread for the same key.
    """

    __slots__ = ("_inner", "_lock_map_lock", "_locks", "_outer")

    def __init__(
        self,
        outer_max_requests: int,
        outer_window_seconds: float,
        inner_max_requests: int,
        inner_window_seconds: float,
    ) -> None:
        """Create a quantized limiter with *outer* and *inner* windows."""
        self._outer = FixedWindowLimiter(outer_max_requests, outer_window_seconds)
        self._inner = FixedWindowLimiter(inner_max_requests, inner_window_seconds)
        self._lock_map_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _get_lock(self, key: str) -> threading.Lock:
        """Return the per-key lock, creating it if necessary."""
        with self._lock_map_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    @staticmethod
    def _merge_allowed(outer: LimitResult, inner: LimitResult) -> LimitResult:
        """Combine two allowed results into a single merged result."""
        return LimitResult(
            allowed=True,
            remaining=min(outer.remaining, inner.remaining),
            limit=outer.limit,
            reset_after_seconds=min(
                outer.reset_after_seconds,
                inner.reset_after_seconds,
            ),
            retry_after_seconds=0.0,
        )

    @staticmethod
    def _pick_denial(outer: LimitResult, inner: LimitResult) -> LimitResult:
        """Return the most relevant denial when at least one limiter denied."""
        if not outer.allowed and not inner.allowed:
            return min(
                outer,
                inner,
                key=lambda r: r.retry_after_seconds,
            )
        if not outer.allowed:
            return outer
        return inner

    def peek(self, key: str, cost: int = 1) -> LimitResult:
        """Return what `check` would return without consuming budget."""
        outer = self._outer.peek(key, cost)
        inner = self._inner.peek(key, cost)

        if outer.allowed and inner.allowed:
            return self._merge_allowed(outer, inner)
        return self._pick_denial(outer, inner)

    def check(self, key: str, cost: int = 1) -> LimitResult:
        """Atomically check both windows and consume only if both allow."""
        lock = self._get_lock(key)
        with lock:
            outer_peek = self._outer.peek(key, cost)
            inner_peek = self._inner.peek(key, cost)

            if not outer_peek.allowed or not inner_peek.allowed:
                return self._pick_denial(outer_peek, inner_peek)

            outer_result = self._outer.check(key, cost)
            inner_result = self._inner.check(key, cost)

            return self._merge_allowed(outer_result, inner_result)
