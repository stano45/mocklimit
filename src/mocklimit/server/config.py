"""Rate limit configuration models and loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

__all__ = ["EndpointConfig", "RateLimitConfig", "load_config"]


class LimitConfig(BaseModel):
    """A single rate limit window definition."""

    max_requests: int
    window_seconds: float


class HeadersConfig(BaseModel):
    """Mapping of semantic header roles to header names."""

    limit: str
    remaining: str
    reset: str


class PolicyConfig(BaseModel):
    """A named rate limiting policy."""

    strategy: Literal["fixed_window"]
    limits: list[LimitConfig]
    scope: Literal["api_key", "ip"]
    response_latency_ms: tuple[int, int]
    headers: HeadersConfig


class TokenEstimationConfig(BaseModel):
    """Token estimation strategy for an endpoint."""

    input: Literal["characters_div_4"]
    output: tuple[int, int]


class EndpointConfig(BaseModel):
    """Configuration for a single API endpoint."""

    methods: list[str]
    policy: str
    token_estimation: TokenEstimationConfig | None = None


class RateLimitConfig(BaseModel):
    """Top-level rate limiting configuration."""

    policies: dict[str, PolicyConfig]
    endpoints: dict[str, EndpointConfig]


def load_config(path: str) -> RateLimitConfig:
    """Read a YAML config file and return a validated ``RateLimitConfig``."""
    raw = Path(path).read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(raw)
    return RateLimitConfig.model_validate(data)
