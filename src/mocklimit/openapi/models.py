"""OpenAPI route definition models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["RouteDefinition"]


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    """A single API route extracted from an OpenAPI spec."""

    path: str
    method: str
    response_schema: dict[str, Any] = field(default_factory=dict)
    operation_id: str | None = None
