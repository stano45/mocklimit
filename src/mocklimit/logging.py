"""Centralized logging configuration for mocklimit."""

from __future__ import annotations

import sys

from loguru import logger

__all__ = ["configure_logging"]

_DEFAULT_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def configure_logging(
    *,
    level: str = "INFO",
    fmt: str | None = None,
    serialize: bool = False,
) -> None:
    """Configure mocklimit logging via loguru.

    Call this once at startup to enable and configure log output.
    Library users who import mocklimit as a dependency should call this
    explicitly; the CLI entrypoint calls it automatically.

    Parameters
    ----------
    level:
        Minimum log level (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL).
    fmt:
        Custom loguru format string.  ``None`` uses the built-in default.
    serialize:
        If ``True``, emit JSON-serialized log records (useful for Docker
        log aggregators).

    """
    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        format=fmt or _DEFAULT_FORMAT,
        serialize=serialize,
    )
    logger.enable("mocklimit")
