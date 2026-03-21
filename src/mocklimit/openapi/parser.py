"""OpenAPI spec parser for extracting route definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import jsonref
import yaml
from loguru import logger

from .models import RouteDefinition

__all__ = ["parse_spec"]

_HTTP_METHODS = frozenset({
    "get", "put", "post", "delete",
    "options", "head", "patch", "trace",
})


def _as_str_dict(value: object) -> dict[str, Any] | None:
    """Cast *value* to a string-keyed dict if it is one, else ``None``."""
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None


def _extract_response_schema(operation: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON schema for the first 2xx response, or empty dict."""
    responses = _as_str_dict(operation.get("responses"))
    if not responses:
        return {}

    for status_code in sorted(responses):
        if not status_code.startswith("2"):
            continue
        response_dict = _as_str_dict(responses[status_code])
        if response_dict is None:
            continue
        content = _as_str_dict(response_dict.get("content"))
        if content is None:
            continue
        json_media = _as_str_dict(content.get("application/json"))
        if json_media is None:
            continue
        schema = _as_str_dict(json_media.get("schema"))
        if schema is not None:
            return schema

    return {}


def parse_spec(path: str) -> list[RouteDefinition]:
    """Parse an OpenAPI YAML/JSON file and return route definitions.

    Iterates over all paths and HTTP methods, extracting the response schema
    from the first 2xx response with ``application/json`` content.  Missing
    responses or schemas are represented as empty dicts.
    """
    logger.debug("Parsing OpenAPI spec from '{}'", path)
    raw = Path(path).read_text(encoding="utf-8")
    spec: dict[str, Any] = jsonref.replace_refs(yaml.safe_load(raw))

    paths: dict[str, Any] | None = spec.get("paths")
    if not paths:
        logger.warning("No paths found in OpenAPI spec '{}'", path)
        return []

    routes: list[RouteDefinition] = []
    for route_path, path_item_raw in paths.items():
        path_item = _as_str_dict(path_item_raw)
        if path_item is None:
            logger.warning("Path item for '{}' is not a dict, skipping", route_path)
            continue
        for method, operation_raw in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            operation = _as_str_dict(operation_raw)
            if operation is None:
                logger.warning(
                    "Operation {} {} is not a dict, skipping",
                    method.upper(),
                    route_path,
                )
                continue
            op_id: str | None = operation.get("operationId")
            schema = _extract_response_schema(operation)
            if not schema:
                logger.debug(
                    "No response schema for {} {} (operationId={})",
                    method.upper(),
                    route_path,
                    op_id,
                )
            routes.append(
                RouteDefinition(
                    path=route_path,
                    method=method.upper(),
                    response_schema=schema,
                    operation_id=op_id,
                ),
            )
            logger.debug(
                "Parsed route: {} {} (operationId={})",
                method.upper(),
                route_path,
                op_id,
            )

    logger.info("Parsed OpenAPI spec: {} routes from '{}'", len(routes), path)
    return routes
