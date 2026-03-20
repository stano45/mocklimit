"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from mocklimit.openapi import RouteDefinition, generate_all_responses, parse_spec
from mocklimit.ratelimit import (
    CompositeLimit,
    CompositeLimitResult,
    FixedWindowLimiter,
    LimitResult,
)

from .config import EndpointConfig, PolicyConfig, RateLimitConfig, load_config
from .stats import StatsTracker

__all__ = ["create_app"]

_BEARER_PREFIX = "Bearer "
_RNG = random.SystemRandom()


@dataclass
class _RouteContext:
    """Everything a rate-limited handler needs, bundled for readability."""

    route_key: str
    dummy_body: dict[str, Any]
    ep_cfg: EndpointConfig
    policy: PolicyConfig
    limiter: CompositeLimit
    costs: dict[str, int]
    stats: StatsTracker


def _extract_scope_key(request: Request, policy: PolicyConfig) -> str:
    """Derive the rate-limit key from the request based on policy scope."""
    if policy.scope == "api_key":
        auth: str | None = request.headers.get("authorization")
        if auth and auth.startswith(_BEARER_PREFIX):
            return auth[len(_BEARER_PREFIX) :]
        return "anonymous"
    client = request.client
    if client is not None:
        return client.host
    return "unknown"


def _most_restrictive(result: CompositeLimitResult) -> LimitResult:
    """Return the single ``LimitResult`` most relevant for response headers."""
    if result.denied_by is not None:
        return result.per_limit[result.denied_by]
    return min(result.per_limit.values(), key=lambda r: r.remaining)


def _rate_limit_headers(
    lr: LimitResult,
    headers_cfg: PolicyConfig,
) -> dict[str, str]:
    """Build rate-limit response headers from a ``LimitResult``."""
    hdr = headers_cfg.headers
    out: dict[str, str] = {
        hdr.limit: str(lr.limit),
        hdr.remaining: str(lr.remaining),
        hdr.reset: f"{lr.reset_after_seconds:.1f}s",
    }
    if not lr.allowed:
        out["Retry-After"] = str(math.ceil(lr.retry_after_seconds))
    return out


def _build_limiters(config: RateLimitConfig) -> dict[str, CompositeLimit]:
    """Instantiate a ``CompositeLimit`` for every policy in *config*."""
    limiters: dict[str, CompositeLimit] = {}
    for name, policy in config.policies.items():
        pairs: list[tuple[str, FixedWindowLimiter]] = [
            (
                f"limit_{i}",
                FixedWindowLimiter(
                    max_requests=lc.max_requests,
                    window_seconds=lc.window_seconds,
                ),
            )
            for i, lc in enumerate(policy.limits)
        ]
        limiters[name] = CompositeLimit(pairs)
    return limiters


def _build_route_table(
    routes: list[RouteDefinition],
    config: RateLimitConfig,
) -> list[dict[str, str | None]]:
    """Build the JSON-serializable route listing for ``/mocklimit/routes``."""
    table: list[dict[str, str | None]] = []
    for route in routes:
        ep_cfg = config.endpoints.get(route.path)
        policy_name: str | None = None
        if ep_cfg is not None and route.method in ep_cfg.methods:
            policy_name = ep_cfg.policy
        table.append({
            "path": route.path,
            "method": route.method,
            "policy": policy_name,
        })
    return table


async def _estimate_tokens(
    request: Request,
    ep_cfg: EndpointConfig,
) -> dict[str, int]:
    """Compute estimated token usage for a request."""
    te = ep_cfg.token_estimation
    if te is None:
        return {}
    body = await request.body()
    prompt_tokens = len(body) // 4
    completion_tokens = _RNG.randint(te.output[0], te.output[1])
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _extract_base_path(spec_path: str) -> str:
    """Read the first ``servers[].url`` from the spec and return its path.

    For example ``https://api.openai.com/v1`` yields ``/v1``.
    Returns an empty string when no server URL is defined.
    """
    raw = Path(spec_path).read_text(encoding="utf-8")
    spec: dict[str, Any] = yaml.safe_load(raw)
    servers: list[dict[str, Any]] = spec.get("servers", [])
    if not servers:
        return ""
    url: str = servers[0].get("url", "")
    return urlparse(url).path.rstrip("/")


def create_app(spec_path: str, rate_config_path: str) -> FastAPI:
    """Build a fully-wired FastAPI application.

    Parses the OpenAPI *spec_path* for route definitions and dummy
    responses, reads the rate-limit YAML at *rate_config_path*, and
    registers all routes under the spec's server base path (e.g.
    ``/v1``) with the appropriate rate-limiting behaviour.
    """
    routes = parse_spec(spec_path)
    responses = generate_all_responses(spec_path)
    config = load_config(rate_config_path)
    limiters = _build_limiters(config)
    stats = StatsTracker()
    route_table = _build_route_table(routes, config)
    base_path = _extract_base_path(spec_path)

    app = FastAPI(title="mocklimit")
    router = APIRouter(prefix=base_path)

    for route in routes:
        route_key = f"{route.method} {route.path}"
        dummy_body = responses.get(route_key, {})
        ep_cfg = config.endpoints.get(route.path)

        if ep_cfg is not None and route.method in ep_cfg.methods:
            policy = config.policies[ep_cfg.policy]
            ctx = _RouteContext(
                route_key=route_key,
                dummy_body=dummy_body,
                ep_cfg=ep_cfg,
                policy=policy,
                limiter=limiters[ep_cfg.policy],
                costs={f"limit_{i}": 1 for i in range(len(policy.limits))},
                stats=stats,
            )
            _register_limited_route(router, route, ctx)
        else:
            _register_plain_route(router, route, dummy_body)

    app.include_router(router)

    async def get_stats(_request: Request) -> JSONResponse:
        """Return per-endpoint, per-key request statistics."""
        return JSONResponse(content=stats.snapshot())

    async def get_routes(_request: Request) -> JSONResponse:
        """Return the list of registered routes and their policies."""
        return JSONResponse(content=route_table)

    app.add_api_route("/mocklimit/stats", get_stats, methods=["GET"])
    app.add_api_route("/mocklimit/routes", get_routes, methods=["GET"])

    return app


def _register_limited_route(
    router: APIRouter,
    route: RouteDefinition,
    ctx: _RouteContext,
) -> None:
    """Register a rate-limited route on *router*."""

    async def handler(request: Request) -> JSONResponse:
        scope_key = _extract_scope_key(request, ctx.policy)
        ctx.stats.record_request(ctx.route_key, scope_key)

        result = ctx.limiter.check(scope_key, ctx.costs)
        lr = _most_restrictive(result)
        headers = _rate_limit_headers(lr, ctx.policy)

        if not result.allowed:
            ctx.stats.record_limited(ctx.route_key, scope_key)
            return JSONResponse(
                status_code=429,
                content=ctx.dummy_body,
                headers=headers,
            )

        latency_min, latency_max = ctx.policy.response_latency_ms
        if latency_max > 0:
            delay_s = _RNG.uniform(latency_min / 1000, latency_max / 1000)
            await asyncio.sleep(delay_s)

        body: dict[str, Any] = dict(ctx.dummy_body)
        usage = await _estimate_tokens(request, ctx.ep_cfg)
        if usage:
            body["usage"] = usage

        return JSONResponse(content=body, headers=headers)

    router.add_api_route(
        route.path,
        handler,
        methods=[route.method],
        name=route.operation_id or ctx.route_key,
    )


def _register_plain_route(
    router: APIRouter,
    route: RouteDefinition,
    dummy_body: dict[str, Any],
) -> None:
    """Register a route that returns the dummy response with no limits."""
    body = dict(dummy_body)

    async def handler(_request: Request) -> JSONResponse:
        return JSONResponse(content=body)

    router.add_api_route(
        route.path,
        handler,
        methods=[route.method],
        name=route.operation_id or f"{route.method} {route.path}",
    )
