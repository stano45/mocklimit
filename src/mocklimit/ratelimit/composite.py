"""Composite rate limiter that enforces multiple limits atomically."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from loguru import logger

from .models import CompositeLimitResult, LimitResult

if TYPE_CHECKING:
    from .fixed_window import FixedWindowLimiter

__all__ = ["CompositeLimit"]


class CompositeLimit:
    """Enforce several named limiters atomically per key.

    Uses a per-key ``threading.Lock`` so that the peek-then-consume
    sequence cannot be interleaved by another thread for the same key.
    """

    __slots__ = ("_limiters", "_lock_map_lock", "_locks")

    def __init__(self, limiters: list[tuple[str, FixedWindowLimiter]]) -> None:
        """Create a composite from *limiters* ``(name, limiter)`` pairs."""
        self._limiters = limiters
        self._lock_map_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _get_lock(self, key: str) -> threading.Lock:
        """Return the per-key lock, creating it if necessary."""
        with self._lock_map_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
                logger.trace("Created new per-key lock for '{}'", key)
            return lock

    def check(self, key: str, costs: dict[str, int]) -> CompositeLimitResult:
        """Atomically check all limiters and consume only if every one allows.

        Returns a `CompositeLimitResult` with per-limiter details.
        """
        lock = self._get_lock(key)
        with lock:
            per_limit: dict[str, LimitResult] = {}
            denied_by: str | None = None

            for name, limiter in self._limiters:
                result = limiter.peek(key, costs[name])
                per_limit[name] = result
                logger.trace(
                    "Composite peek [key={}, limiter={}]: allowed={} remaining={}",
                    key,
                    name,
                    result.allowed,
                    result.remaining,
                )
                if not result.allowed and denied_by is None:
                    denied_by = name

            if denied_by is not None:
                logger.debug(
                    "Composite DENIED [key={}]: denied_by='{}'",
                    key,
                    denied_by,
                )
                return CompositeLimitResult(
                    allowed=False,
                    denied_by=denied_by,
                    per_limit=per_limit,
                )

            for name, limiter in self._limiters:
                result = limiter.check(key, costs[name])
                per_limit[name] = result

            logger.debug("Composite ALLOWED [key={}]", key)
            return CompositeLimitResult(
                allowed=True,
                denied_by=None,
                per_limit=per_limit,
            )
