"""Provider-specific error body templates for 429 responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ErrorProvider, FormatConfig, LimitConfig
from .formatters import format_reset

__all__ = ["render_error_body"]


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """All context needed to render an error body."""

    provider: ErrorProvider
    denied_by_limit: LimitConfig
    limit: int
    reset_seconds: float
    retry_seconds: float
    fmt: FormatConfig


def render_error_body(ctx: ErrorContext) -> dict[str, Any]:
    """Render a provider-specific 429 error body."""
    match ctx.provider:
        case ErrorProvider.openai:
            return _openai_error(ctx)
        case ErrorProvider.anthropic:
            return _anthropic_error(ctx)
        case ErrorProvider.google:
            return _google_error(ctx)


def _openai_error(ctx: ErrorContext) -> dict[str, Any]:
    reset_str = format_reset(ctx.reset_seconds, ctx.fmt.reset)
    dimension = ctx.denied_by_limit.dimension
    return {
        "error": {
            "message": (
                f"Rate limit reached for {dimension} in organization"
                f" on tokens per min (TPM): Limit {ctx.limit},"
                f" Used {ctx.limit}, Requested 1."
                f" Please retry after {reset_str}."
            ),
            "type": "tokens" if "token" in dimension else "requests",
            "param": None,
            "code": "rate_limit_exceeded",
        },
    }


def _anthropic_error(ctx: ErrorContext) -> dict[str, Any]:
    dimension = ctx.denied_by_limit.dimension
    return {
        "type": "error",
        "error": {
            "type": "rate_limit_error",
            "message": (
                f"Number of {dimension} has exceeded your rate limit."
                f" Please retry after {ctx.retry_seconds:.0f} seconds."
            ),
        },
    }


def _google_error(ctx: ErrorContext) -> dict[str, Any]:
    dimension = ctx.denied_by_limit.dimension
    metric = (
        f"generativelanguage.googleapis.com"
        f"/{dimension}_count"
    )
    return {
        "error": {
            "code": 429,
            "message": (
                f"Resource has been exhausted (e.g. check quota)."
                f" Please retry in {ctx.retry_seconds:.6f}s."
            ),
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": (
                        "type.googleapis.com/google.rpc.QuotaFailure"
                    ),
                    "violations": [
                        {
                            "quotaMetric": metric,
                            "quotaLimit": str(ctx.limit),
                            "quotaDimensions": {"model": "unknown"},
                        },
                    ],
                },
                {
                    "@type": (
                        "type.googleapis.com/google.rpc.RetryInfo"
                    ),
                    "retryDelay": f"{ctx.retry_seconds:.6f}s",
                },
            ],
        },
    }
