"""Token bucket rate limiter with continuous refill."""

from __future__ import annotations

import threading
import time

from loguru import logger

from .models import LimitResult

__all__ = ["TokenBucketLimiter"]


def _time_to_fill(capacity: int, tokens: float, rate: float) -> float:
    if rate <= 0:
        return 0.0
    return (capacity - tokens) / rate


class TokenBucketLimiter:
    """Token bucket with continuous refill.

    Tokens accumulate at ``refill_rate`` per second up to ``capacity``.
    Each check consumes ``cost`` tokens if available.
    """

    __slots__ = ("_buckets", "_capacity", "_lock", "_refill_rate")

    def __init__(self, capacity: int, refill_rate: float) -> None:
        """Create a limiter with *capacity* burst and *refill_rate* tokens/s."""
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        logger.trace(
            "TokenBucketLimiter created: capacity={} refill_rate={}/s",
            capacity,
            refill_rate,
        )

    def _refill(self, key: str, now: float) -> float:
        """Refill bucket for *key* and return current token count."""
        entry = self._buckets.get(key)
        if entry is None:
            self._buckets[key] = (float(self._capacity), now)
            return float(self._capacity)

        tokens, last_time = entry
        elapsed = now - last_time
        tokens = min(self._capacity, tokens + elapsed * self._refill_rate)
        self._buckets[key] = (tokens, now)
        return tokens

    def peek(self, key: str, cost: int = 1) -> LimitResult:
        """Return what ``check`` would return without consuming budget."""
        now = time.time()
        with self._lock:
            tokens = self._refill(key, now)

        remaining = int(tokens)
        reset_after = _time_to_fill(self._capacity, tokens, self._refill_rate)

        if tokens < cost:
            deficit = cost - tokens
            retry_after = (
                deficit / self._refill_rate if self._refill_rate > 0 else 0.0
            )
            return LimitResult(
                allowed=False,
                remaining=remaining,
                limit=self._capacity,
                reset_after_seconds=reset_after,
                retry_after_seconds=retry_after,
            )

        return LimitResult(
            allowed=True,
            remaining=remaining - cost,
            limit=self._capacity,
            reset_after_seconds=reset_after,
            retry_after_seconds=0.0,
        )

    def check(self, key: str, cost: int = 1) -> LimitResult:
        """Consume *cost* tokens from *key*'s bucket if available."""
        now = time.time()
        with self._lock:
            tokens = self._refill(key, now)

            if tokens < cost:
                deficit = cost - tokens
                retry_after = (
                    deficit / self._refill_rate
                    if self._refill_rate > 0
                    else 0.0
                )
                reset_after = _time_to_fill(
                    self._capacity, tokens, self._refill_rate,
                )
                logger.debug(
                    "TokenBucket DENIED [key={}]: {:.0f}/{} tokens,"
                    " cost={}, retry_after={:.2f}s",
                    key,
                    tokens,
                    self._capacity,
                    cost,
                    retry_after,
                )
                return LimitResult(
                    allowed=False,
                    remaining=int(tokens),
                    limit=self._capacity,
                    reset_after_seconds=reset_after,
                    retry_after_seconds=retry_after,
                )

            tokens -= cost
            self._buckets[key] = (tokens, now)
            reset_after = _time_to_fill(
                self._capacity, tokens, self._refill_rate,
            )
            logger.debug(
                "TokenBucket ALLOWED [key={}]: {:.0f}/{} tokens"
                " remaining (cost={})",
                key,
                tokens,
                self._capacity,
                cost,
            )
            return LimitResult(
                allowed=True,
                remaining=int(tokens),
                limit=self._capacity,
                reset_after_seconds=reset_after,
                retry_after_seconds=0.0,
            )
