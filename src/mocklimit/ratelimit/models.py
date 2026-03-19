"""Rate limiting result models."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LimitResult"]


@dataclass(frozen=True, slots=True)
class LimitResult:
    """Outcome of a rate limit check."""

    allowed: bool
    remaining: int
    limit: int
    reset_after_seconds: float
    retry_after_seconds: float
