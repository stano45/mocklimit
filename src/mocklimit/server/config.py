"""Rate limit configuration models and loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, ValidationError

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
    logger.debug("Loading rate-limit config from '{}'", path)
    raw = Path(path).read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(raw)

    try:
        config = RateLimitConfig.model_validate(data)
    except ValidationError:
        logger.error("Rate-limit config validation failed for '{}'", path)
        raise

    for name, policy in config.policies.items():
        logger.debug(
            "Policy '{}': strategy={}, {} limit(s), scope={}, latency={}-{}ms",
            name,
            policy.strategy,
            len(policy.limits),
            policy.scope,
            policy.response_latency_ms[0],
            policy.response_latency_ms[1],
        )

    for ep_path, ep_cfg in config.endpoints.items():
        logger.debug(
            "Endpoint '{}': methods={}, policy='{}'{}",
            ep_path,
            ep_cfg.methods,
            ep_cfg.policy,
            ", token_estimation=enabled" if ep_cfg.token_estimation else "",
        )

    logger.info(
        "Rate-limit config loaded: {} policies, {} endpoints",
        len(config.policies),
        len(config.endpoints),
    )
    return config
