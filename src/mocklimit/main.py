"""CLI entry point for the mocklimit server."""

from __future__ import annotations

import argparse
import sys

import uvicorn

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
    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run the requested command."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command != "serve":
        parser.print_help()
        sys.exit(1)

    app = create_app(args.spec, args.rate_config)
    uvicorn.run(app, host=args.host, port=args.port)
