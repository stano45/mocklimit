"""CLI entry point for the mocklimit server."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn
from loguru import logger

from .logging import configure_logging
from .server.app import create_app


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="mocklimit",
        description="Configurable mock API server with realistic rate limiting",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Start the mock server")
    serve.add_argument("--spec", required=True, help="Path to the OpenAPI spec YAML")
    serve.add_argument(
        "--rate-config",
        required=True,
        help="Path to the rate-limit config YAML",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    serve.add_argument(
        "--log-level",
        default=os.environ.get("MOCKLIMIT_LOG_LEVEL", "INFO"),
        help="Log level: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL "
        "(env: MOCKLIMIT_LOG_LEVEL, default: INFO)",
    )
    serve.add_argument(
        "--log-format",
        default=None,
        help="Custom loguru format string (optional)",
    )
    serve.add_argument(
        "--log-json",
        action="store_true",
        default=False,
        help="Emit JSON-serialized log lines (useful for log aggregators)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run the requested command."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command != "serve":
        parser.print_help()
        sys.exit(1)

    configure_logging(
        level=args.log_level,
        fmt=args.log_format,
        serialize=args.log_json,
    )

    logger.info("Starting mocklimit server on {}:{}", args.host, args.port)
    logger.debug("OpenAPI spec: {}", args.spec)
    logger.debug("Rate-limit config: {}", args.rate_config)

    app = create_app(args.spec, args.rate_config)
    uvicorn.run(app, host=args.host, port=args.port)
