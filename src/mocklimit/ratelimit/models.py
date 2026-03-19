"""Rate limiting result models."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CompositeLimitResult", "LimitResult"]


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
