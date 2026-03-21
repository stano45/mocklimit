"""Fixed-window rate limiter implementation."""

from __future__ import annotations

import time

from loguru import logger

from .models import LimitResult

__all__ = ["FixedWindowLimiter"]


class FixedWindowLimiter:
    """Fixed-window in-memory rate limiter.

    Divides time into consecutive windows of ``window_seconds`` length and
    allows up to ``max_requests`` units of cost per key per window.
    """

    __slots__ = ("_max_requests", "_window_seconds", "_windows")

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        """Create a limiter allowing *max_requests* per *window_seconds*."""
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._windows: dict[str, dict[int, int]] = {}
        logger.trace(
            "FixedWindowLimiter created: max_requests={} window_seconds={}",
            max_requests,
            window_seconds,
        )

    def _get_window_state(self, key: str) -> tuple[int, int, float]:
        """Return ``(current_window, current_count, reset_after)`` for *key*.

        Cleans up stale windows as a side-effect.
        """
        now = time.time()
        current_window = int(now // self._window_seconds)
        next_window_start = (current_window + 1) * self._window_seconds
        reset_after = next_window_start - now

        key_windows = self._windows.get(key)
        if key_windows is None:
            key_windows = {}
            self._windows[key] = key_windows
        else:
            stale = [w for w in key_windows if w < current_window]
            if stale:
                for w in stale:
                    del key_windows[w]
                logger.trace(
                    "Cleaned {} stale window(s) for key '{}'",
                    len(stale),
                    key,
                )

        current_count = key_windows.get(current_window, 0)
        logger.trace(
            "Window state [key={}]: window={} count={}/{} reset_after={:.2f}s",
            key,
            current_window,
            current_count,
            self._max_requests,
            reset_after,
        )
        return current_window, current_count, reset_after

    def peek(self, key: str, cost: int = 1) -> LimitResult:
        """Return what `check` would return without consuming budget."""
        _, current_count, reset_after = self._get_window_state(key)

        if current_count + cost > self._max_requests:
            return LimitResult(
                allowed=False,
                remaining=self._max_requests - current_count,
                limit=self._max_requests,
                reset_after_seconds=reset_after,
                retry_after_seconds=reset_after,
            )

        return LimitResult(
            allowed=True,
            remaining=self._max_requests - (current_count + cost),
            limit=self._max_requests,
            reset_after_seconds=reset_after,
            retry_after_seconds=0.0,
        )

    def check(self, key: str, cost: int = 1) -> LimitResult:
        """Check whether *key* may consume *cost* units of the budget.

        Returns a `LimitResult` describing the decision and timing metadata.
        """
        current_window, current_count, reset_after = self._get_window_state(key)

        if current_count + cost > self._max_requests:
            logger.debug(
                "FixedWindow DENIED [key={}]: {}/{} used, cost={}, retry_after={:.2f}s",
                key,
                current_count,
                self._max_requests,
                cost,
                reset_after,
            )
            return LimitResult(
                allowed=False,
                remaining=self._max_requests - current_count,
                limit=self._max_requests,
                reset_after_seconds=reset_after,
                retry_after_seconds=reset_after,
            )

        new_count = current_count + cost
        self._windows[key][current_window] = new_count

        logger.debug(
            "FixedWindow ALLOWED [key={}]: {}/{} used (cost={}), remaining={}",
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
            reset_after_seconds=reset_after,
            retry_after_seconds=0.0,
        )
