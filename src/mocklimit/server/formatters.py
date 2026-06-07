"""Header value formatters for reset and retry-after."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from .config import FormatConfig, ResetFormat, RetryAfterUnit

__all__ = ["format_reset", "format_retry_after", "go_duration"]


def format_reset(seconds: float, fmt: ResetFormat) -> str:
    """Format a reset-after-seconds value according to the configured format."""
    match fmt:
        case ResetFormat.relative_seconds:
            return f"{seconds:.1f}s"
        case ResetFormat.go_duration:
            return go_duration(seconds)
        case ResetFormat.rfc3339:
            ts = datetime.now(tz=UTC).timestamp() + seconds
            dt = datetime.fromtimestamp(ts, tz=UTC)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_retry_after(seconds: float, cfg: FormatConfig) -> tuple[str, str]:
    """Return (header_name, header_value) for the retry-after signal."""
    ra = cfg.retry_after
    match ra.unit:
        case RetryAfterUnit.seconds:
            value = str(math.ceil(seconds))
        case RetryAfterUnit.milliseconds:
            value = str(math.ceil(seconds * 1000))
    return ra.header, value


def go_duration(seconds: float) -> str:
    """Format seconds as a Go-style duration string.

    Examples: "12ms", "4.253s", "1m30s", "2h5m0s"
    """
    if seconds <= 0:
        return "0s"

    if seconds < 1:
        ms = seconds * 1000
        if ms < 1:
            return "0s"
        return f"{ms:.0f}ms"

    hours = int(seconds // 3600)
    remainder = seconds - hours * 3600
    minutes = int(remainder // 60)
    secs = remainder - minutes * 60

    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}m")

    if secs > 0:
        if secs == int(secs):
            parts.append(f"{int(secs)}s")
        else:
            parts.append(f"{secs:.3f}s")
    elif parts:
        parts.append("0s")

    return "".join(parts)
