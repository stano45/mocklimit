"""Sliding-window (log) rate limiter implementation."""

from __future__ import annotations

import time

from loguru import logger

from .models import LimitResult

__all__ = ["SlidingWindowLimiter"]


class SlidingWindowLimiter:
    """Sliding-window in-memory rate limiter.

    Tracks a log of ``(timestamp, cost)`` entries per key and evaluates
    the total cost over a continuous trailing window of ``window_seconds``.
    Unlike the fixed-window limiter, there are no boundary-aligned resets;
    each entry expires individually.
    """

    __slots__ = ("_entries", "_max_requests", "_window_seconds")

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        """Create a limiter allowing *max_requests* per *window_seconds*."""
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._entries: dict[str, list[tuple[float, int]]] = {}
        logger.trace(
            "SlidingWindowLimiter created: max_requests={} window_seconds={}",
            max_requests,
            window_seconds,
        )

    def _get_state(self, key: str, now: float) -> tuple[int, float]:
        """Evict expired entries and return ``(current_count, retry_after)``.

        ``retry_after`` is the time until the oldest active entry expires
        (freeing capacity), or 0.0 if there are no entries.
        """
        cutoff = now - self._window_seconds
        entries = self._entries.get(key)
        if entries is None:
            entries = []
            self._entries[key] = entries

        evicted = 0
        while entries and entries[0][0] <= cutoff:
            entries.pop(0)
            evicted += 1
        if evicted:
            logger.trace(
                "Evicted {} stale entry(ies) for key '{}'",
                evicted,
                key,
            )

        current_count = sum(cost for _, cost in entries)
        retry_after = (entries[0][0] + self._window_seconds - now) if entries else 0.0

        logger.trace(
            "Sliding window state [key={}]: count={}/{} retry_after={:.2f}s",
            key,
            current_count,
            self._max_requests,
            retry_after,
        )
        return current_count, retry_after

    def peek(self, key: str, cost: int = 1) -> LimitResult:
        """Return what `check` would return without consuming budget."""
        now = time.time()
        current_count, retry_after = self._get_state(key, now)

        if current_count + cost > self._max_requests:
            return LimitResult(
                allowed=False,
                remaining=self._max_requests - current_count,
                limit=self._max_requests,
                reset_after_seconds=retry_after,
                retry_after_seconds=retry_after,
            )

        return LimitResult(
            allowed=True,
            remaining=self._max_requests - (current_count + cost),
            limit=self._max_requests,
            reset_after_seconds=retry_after,
            retry_after_seconds=0.0,
        )

    def check(self, key: str, cost: int = 1) -> LimitResult:
        """Check whether *key* may consume *cost* units of the budget.

        Returns a `LimitResult` describing the decision and timing metadata.
        """
        now = time.time()
        current_count, retry_after = self._get_state(key, now)

        if current_count + cost > self._max_requests:
            logger.debug(
                "SlidingWindow DENIED [key={}]: {}/{} used, cost={},"
                " retry_after={:.2f}s",
                key,
                current_count,
                self._max_requests,
                cost,
                retry_after,
            )
            return LimitResult(
                allowed=False,
                remaining=self._max_requests - current_count,
                limit=self._max_requests,
                reset_after_seconds=retry_after,
                retry_after_seconds=retry_after,
            )

        self._entries[key].append((now, cost))
        new_count = current_count + cost
        new_retry_after = self._entries[key][0][0] + self._window_seconds - now

        logger.debug(
            "SlidingWindow ALLOWED [key={}]: {}/{} used (cost={}), remaining={}",
            key,
            new_count,
            self._max_requests,
            cost,
            self._max_requests - new_count,
        )
        return LimitResult(
            allowed=True,
            remaining=self._max_requests - new_count,
            limit=self._max_requests,
            reset_after_seconds=new_retry_after,
            retry_after_seconds=0.0,
        )
