"""mocklimit — configurable mock API server with realistic rate limiting."""

from loguru import logger

from .logging import configure_logging

__all__ = ["configure_logging"]
__version__ = "0.1.0"

logger.disable("mocklimit")
