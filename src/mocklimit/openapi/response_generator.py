"""Generate minimal valid JSON responses from OpenAPI JSON schemas."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

__all__ = ["generate_all_responses", "generate_dummy_response"]

_HTTP_METHODS = frozenset({
    "get", "put", "post", "delete",
    "options", "head", "patch", "trace",
})


def _as_str_dict(value: object) -> dict[str, Any] | None:
    """Cast *value* to a string-keyed dict if it is one, else ``None``."""
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None


_PRIMITIVE_DEFAULTS: dict[str, str | int | float | bool] = {
    "string": "mock_string",
    "integer": 1,
    "number": 1.0,
    "boolean": True,
}


def _resolve_ref(ref: str, all_schemas: dict[str, Any]) -> dict[str, Any]:
    """Resolve a ``$ref`` string like ``#/components/schemas/Foo``."""
    name = ref.rsplit("/", maxsplit=1)[-1]
    schema = all_schemas.get(name)
    if isinstance(schema, dict):
        return cast("dict[str, Any]", schema)
    return {}


def _generate_array(
    schema: dict[str, Any],
    all_schemas: dict[str, Any] | None,
) -> list[Any]:
    """Generate a single-element array from *schema*'s ``items``."""
    items_schema = schema.get("items")
    if isinstance(items_schema, dict):
        return [_generate_value(cast("dict[str, Any]", items_schema), all_schemas)]
    return [None]


def _generate_for_type(
    schema_type: str,
    schema: dict[str, Any],
    all_schemas: dict[str, Any] | None,
) -> str | int | float | bool | list[Any] | dict[str, Any] | None:
    """Dispatch value generation based on the JSON Schema ``type``."""
    if schema_type in _PRIMITIVE_DEFAULTS:
        return _PRIMITIVE_DEFAULTS[schema_type]
    if schema_type == "array":
        return _generate_array(schema, all_schemas)
    if schema_type == "object":
        return _generate_object(schema, all_schemas)
    return None


def _generate_value(
    schema: dict[str, Any],
    all_schemas: dict[str, Any] | None,
) -> str | int | float | bool | list[Any] | dict[str, Any] | None:
    """Recursively generate a dummy value matching *schema*."""
    if "$ref" in schema:
        if all_schemas is None:
            return None
        return _generate_value(_resolve_ref(schema["$ref"], all_schemas), all_schemas)

    if "enum" in schema:
        values: list[Any] = schema["enum"]
        return cast("str | int | float | bool", values[0]) if values else None

    schema_type: str | None = schema.get("type")
    if schema_type is None:
        return None
    return _generate_for_type(schema_type, schema, all_schemas)


def _generate_object(
    schema: dict[str, Any],
    all_schemas: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a dummy object from the ``properties`` declared in *schema*."""
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] | None = schema.get("required")

    result: dict[str, Any] = {}

    keys = [k for k in properties if k in required] if required else list(properties)
    for key in keys:
        prop_schema = properties[key]
        if isinstance(prop_schema, dict):
            result[key] = _generate_value(
                cast("dict[str, Any]", prop_schema),
                all_schemas,
            )
        else:
            result[key] = None

    return result


def generate_dummy_response(
    schema: dict[str, Any],
    all_schemas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a minimal valid JSON object from a JSON schema.

    Rules:
    - ``string`` -> ``"mock_string"``
    - ``integer`` -> ``1``
    - ``number`` -> ``1.0``
    - ``boolean`` -> ``True``
    - ``array`` -> single-element list with a dummy item
    - ``enum`` -> first value
    - ``object`` -> recurse into properties
    - ``$ref`` -> resolved via *all_schemas*
    - no type -> ``None``

    When ``required`` is specified only required fields are generated.
    """
    value = _generate_value(schema, all_schemas)
    return value if isinstance(value, dict) else {}


def _extract_raw_response_schema(operation: dict[str, Any]) -> dict[str, Any]:
    """Return the raw response schema for the first 2xx response."""
    responses = _as_str_dict(operation.get("responses"))
    if not responses:
        return {}

    for status_code in sorted(responses):
        if not status_code.startswith("2"):
            continue
        response_obj = _as_str_dict(responses[status_code])
        if response_obj is None:
            continue
        content = _as_str_dict(response_obj.get("content"))
        if content is None:
            continue
        json_media = _as_str_dict(content.get("application/json"))
        if json_media is None:
            continue
        schema = _as_str_dict(json_media.get("schema"))
        if schema is not None:
            return schema

    return {}


def generate_all_responses(spec_path: str) -> dict[str, dict[str, Any]]:
    """Parse an OpenAPI spec and return dummy responses for every route.

    Returns a mapping of ``"METHOD /path"`` to a generated response dict.
    ``$ref`` values are resolved against the spec's ``components/schemas``.
    """
    raw = Path(spec_path).read_text(encoding="utf-8")
    spec: dict[str, Any] = yaml.safe_load(raw)

    components = _as_str_dict(spec.get("components"))
    all_schemas: dict[str, Any] = (
        cast("dict[str, Any]", components.get("schemas", {}))
        if components is not None
        else {}
    )

    paths: dict[str, Any] | None = _as_str_dict(spec.get("paths"))
    if not paths:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for route_path, path_item_raw in paths.items():
        path_item = _as_str_dict(path_item_raw)
        if path_item is None:
            continue
        for method, operation_raw in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            operation = _as_str_dict(operation_raw)
            if operation is None:
                continue
            response_schema = _extract_raw_response_schema(operation)
            if response_schema:
                key = f"{method.upper()} {route_path}"
                result[key] = generate_dummy_response(response_schema, all_schemas)

    return result
