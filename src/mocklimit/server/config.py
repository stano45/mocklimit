"""Rate limit configuration models and loader."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from loguru import logger
from pydantic import BaseModel, ValidationError

__all__ = ["EndpointConfig", "RateLimitConfig", "load_config"]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ComponentStrategy(StrEnum):
    """Strategy for computing a cost component value."""

    fixed = "fixed"
    random = "random"
    characters_div_4 = "characters_div_4"


class ResetFormat(StrEnum):
    """How to format the reset header value."""

    relative_seconds = "relative_seconds"  # "4.3s"
    go_duration = "go_duration"  # "12ms", "4.253s", "6m0s"
    rfc3339 = "rfc3339"  # "2026-06-07T15:30:00Z"


class RetryAfterUnit(StrEnum):
    """Unit for retry-after header."""

    seconds = "seconds"
    milliseconds = "milliseconds"


class ErrorProvider(StrEnum):
    """Built-in error body template provider."""

    openai = "openai"
    anthropic = "anthropic"
    google = "google"


# ---------------------------------------------------------------------------
# Resource / timing config
# ---------------------------------------------------------------------------


class ComponentConfig(BaseModel):
    """A single cost component (input or output) of a resource.

    ``strategy`` determines how the value is computed:

    - ``fixed``: always returns ``value``.
    - ``random``: uniform random integer in ``range``.
    - ``characters_div_4``: ``len(request_body) // 4``.

    ``cap_field`` (optional): name of a top-level integer field in the JSON
    request body that caps the computed value (e.g. ``max_tokens``). Real LLM
    APIs never emit more completion tokens than the caller's ``max_tokens``, so
    setting ``cap_field: max_tokens`` on a ``random`` output component clamps the
    drawn completion length to the requested cap when the field is present. When
    the field is absent or unparseable, the uncapped value is used.
    """

    strategy: ComponentStrategy
    value: int | None = None
    range: tuple[int, int] | None = None
    cap_field: str | None = None


class ResourceConfig(BaseModel):
    """A named resource definition with input/output cost components.

    Total cost per request = input + output.
    """

    input: ComponentConfig
    output: ComponentConfig


class TimingScaleConfig(BaseModel):
    """Latency scaling config that references a resource's output."""

    resource: str
    component: str = "output"
    ms_per_unit: float


class TimingConfig(BaseModel):
    """Response timing configuration for an endpoint.

    ``base_ms``: random base latency range [min, max].
    ``scale``: optional, adds latency proportional to a resource component.
    """

    base_ms: tuple[int, int] = (0, 0)
    scale: TimingScaleConfig | None = None


# ---------------------------------------------------------------------------
# Header / format config
# ---------------------------------------------------------------------------


class HeadersConfig(BaseModel):
    """Mapping of semantic header roles to header names."""

    limit: str
    remaining: str
    reset: str


class RetryAfterConfig(BaseModel):
    """How to emit the retry-after signal on 429."""

    header: str = "Retry-After"
    unit: RetryAfterUnit = RetryAfterUnit.seconds


class FormatConfig(BaseModel):
    """Format settings for header values."""

    reset: ResetFormat = ResetFormat.relative_seconds
    retry_after: RetryAfterConfig = RetryAfterConfig()


class ErrorTemplateConfig(BaseModel):
    """Error body configuration for 429 responses."""

    provider: ErrorProvider


# ---------------------------------------------------------------------------
# Limit config
# ---------------------------------------------------------------------------


class LimitConfig(BaseModel):
    """A single rate limit definition.

    ``dimension`` names a resource (or resource.component) from the endpoint's
    ``resources`` map. Supports dotted notation: ``tokens.input``, ``tokens.output``.

    For ``strategy: token_bucket``, use ``capacity`` and ``refill_rate`` instead
    of ``limit``/``window_seconds``.
    """

    limit: int | None = None
    max_requests: int | None = None
    window_seconds: float | None = None
    capacity: int | None = None
    refill_rate: float | None = None
    dimension: str = "requests"
    headers: HeadersConfig | None = None

    def model_post_init(self, _context: Any, /) -> None:  # noqa: ANN401
        if self.capacity is not None:
            return
        if self.limit is None and self.max_requests is None:
            msg = "Either 'limit' or 'max_requests' (or 'capacity') must be set"
            raise ValueError(msg)
        if self.limit is None:
            self.limit = self.max_requests


# ---------------------------------------------------------------------------
# Policy / endpoint / top-level
# ---------------------------------------------------------------------------


class PolicyConfig(BaseModel):
    """A named rate limiting policy."""

    strategy: str
    limits: list[LimitConfig]
    scope: str
    response_latency_ms: tuple[int, int] = (0, 0)
    headers: HeadersConfig | None = None
    format: FormatConfig = FormatConfig()
    error_template: ErrorTemplateConfig | None = None


class EndpointConfig(BaseModel):
    """Configuration for a single API endpoint.

    ``resources`` defines named resource estimators.  Each limit whose
    ``dimension`` matches a resource name uses that resource's estimated
    value as the per-request cost. Dotted dimensions (e.g. ``tokens.input``)
    reference a specific component.

    ``timing`` controls response latency.
    """

    methods: list[str]
    policy: str
    resources: dict[str, ResourceConfig] | None = None
    timing: TimingConfig | None = None


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
            "Policy '{}': strategy={}, {} limit(s), scope={}",
            name,
            policy.strategy,
            len(policy.limits),
            policy.scope,
        )

    for ep_path, ep_cfg in config.endpoints.items():
        res_names = list((ep_cfg.resources or {}).keys())
        logger.debug(
            "Endpoint '{}': methods={}, policy='{}'{}",
            ep_path,
            ep_cfg.methods,
            ep_cfg.policy,
            f", resources={res_names}" if res_names else "",
        )

    logger.info(
        "Rate-limit config loaded: {} policies, {} endpoints",
        len(config.policies),
        len(config.endpoints),
    )
    return config
