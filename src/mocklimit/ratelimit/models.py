"""Rate limiting result models and protocols."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["CompositeLimitResult", "LimitResult", "Limiter"]


@runtime_checkable
class Limiter(Protocol):
    """Structural interface shared by all rate limiter implementations."""

    def peek(self, key: str, cost: int = 1) -> LimitResult:
        """Return what `check` would return without consuming budget."""
        ...

    def check(self, key: str, cost: int = 1) -> LimitResult:
        """Check whether *key* may consume *cost* units and record if allowed."""
        ...


@dataclass(frozen=True, slots=True)
class LimitResult:
    """Outcome of a rate limit check."""

    allowed: bool
    remaining: int
    limit: int
    reset_after_seconds: float
    retry_after_seconds: float


@dataclass(frozen=True, slots=True)
class CompositeLimitResult:
    """Outcome of a composite rate limit check across multiple limiters."""

    allowed: bool
    denied_by: str | None
    per_limit: dict[str, LimitResult]
