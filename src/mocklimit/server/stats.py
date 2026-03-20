"""Thread-safe request statistics tracker."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

__all__ = ["StatsTracker"]


@dataclass
class _KeyStats:
    """Mutable counters for a single (endpoint, key) pair."""

    total_requests: int = 0
    total_429s: int = 0


@dataclass
class StatsTracker:
    """Per-endpoint, per-key request statistics.

    All mutating methods are guarded by a lock so the tracker is safe
    to use from FastAPI's thread-pool backed sync handlers.
    """

    _data: dict[str, dict[str, _KeyStats]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_request(self, endpoint: str, key: str) -> None:
        """Increment the total-requests counter for *endpoint* / *key*."""
        with self._lock:
            self._ensure(endpoint, key).total_requests += 1

    def record_limited(self, endpoint: str, key: str) -> None:
        """Increment the total-429s counter for *endpoint* / *key*."""
        with self._lock:
            self._ensure(endpoint, key).total_429s += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of current statistics."""
        with self._lock:
            return {
                endpoint: {
                    key: {
                        "total_requests": ks.total_requests,
                        "total_429s": ks.total_429s,
                    }
                    for key, ks in keys.items()
                }
                for endpoint, keys in self._data.items()
            }

    def _ensure(self, endpoint: str, key: str) -> _KeyStats:
        """Return the ``_KeyStats`` for *endpoint* / *key*, creating if needed.

        Must be called while holding ``self._lock``.
        """
        by_key = self._data.get(endpoint)
        if by_key is None:
            by_key = {}
            self._data[endpoint] = by_key
        ks = by_key.get(key)
        if ks is None:
            ks = _KeyStats()
            by_key[key] = ks
        return ks
