"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from mocklimit.openapi import RouteDefinition, generate_all_responses, parse_spec
from mocklimit.ratelimit import (
    CompositeLimit,
    CompositeLimitResult,
    FixedWindowLimiter,
    LimitResult,
    SlidingWindowLimiter,
    TokenBucketLimiter,
)

from .config import (
    ComponentConfig,
    ComponentStrategy,
    EndpointConfig,
    PolicyConfig,
    RateLimitConfig,
    load_config,
)
from .error_templates import ErrorContext, render_error_body
from .formatters import format_reset, format_retry_after
from .metrics import MetricsTracker
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
    stats: StatsTracker
    metrics: MetricsTracker


def _extract_scope_key(request: Request, policy: PolicyConfig) -> str:
    """Derive the rate-limit key from the request based on policy scope."""
    if policy.scope == "api_key":
        auth: str | None = request.headers.get("authorization")
        if auth and auth.startswith(_BEARER_PREFIX):
            key = auth[len(_BEARER_PREFIX) :]
            preview = key[:8] if len(key) > 8 else key  # noqa: PLR2004
            logger.trace("Extracted API key scope: {}…", preview)
            return key
        logger.warning(
            "No Bearer token in Authorization header, "
            "using 'anonymous' scope key",
        )
        return "anonymous"
    client = request.client
    if client is not None:
        logger.trace("Extracted IP scope key: {}", client.host)
        return client.host
    logger.warning("No client information available, using 'unknown' scope key")
    return "unknown"


def _most_restrictive(result: CompositeLimitResult) -> LimitResult:
    """Return the single ``LimitResult`` most relevant for response headers."""
    if result.denied_by is not None:
        return result.per_limit[result.denied_by]
    return min(result.per_limit.values(), key=lambda r: r.remaining)


def _rate_limit_headers(
    result: CompositeLimitResult,
    policy: PolicyConfig,
) -> dict[str, str]:
    """Build rate-limit response headers from all per-limit results.

    Emits one header group per limit that has a ``headers`` config.
    Falls back to policy-level headers with the most-restrictive result.
    """
    fmt = policy.format
    out: dict[str, str] = {}

    has_per_limit_headers = False
    for i, lc in enumerate(policy.limits):
        if lc.headers is None:
            continue
        has_per_limit_headers = True
        lr = result.per_limit.get(f"limit_{i}")
        if lr is None:
            continue
        out[lc.headers.limit] = str(lr.limit)
        out[lc.headers.remaining] = str(lr.remaining)
        out[lc.headers.reset] = format_reset(lr.reset_after_seconds, fmt.reset)

    if not has_per_limit_headers and policy.headers is not None:
        lr = _most_restrictive(result)
        hdr = policy.headers
        out[hdr.limit] = str(lr.limit)
        out[hdr.remaining] = str(lr.remaining)
        out[hdr.reset] = format_reset(lr.reset_after_seconds, fmt.reset)

    if not result.allowed:
        lr = _most_restrictive(result)
        header_name, header_value = format_retry_after(lr.retry_after_seconds, fmt)
        out[header_name] = header_value

    return out


def _build_limiters(config: RateLimitConfig) -> dict[str, CompositeLimit]:
    """Instantiate a ``CompositeLimit`` for every policy in *config*."""
    limiters: dict[str, CompositeLimit] = {}
    for name, policy in config.policies.items():
        pairs: list[tuple[str, Any]] = []
        for i, lc in enumerate(policy.limits):
            if policy.strategy == "token_bucket" or lc.capacity is not None:
                pairs.append((
                    f"limit_{i}",
                    TokenBucketLimiter(
                        capacity=lc.capacity or lc.limit or 0,
                        refill_rate=lc.refill_rate or (
                            (lc.limit or 0) / (lc.window_seconds or 60)
                        ),
                    ),
                ))
            elif policy.strategy == "sliding_window":
                pairs.append((
                    f"limit_{i}",
                    SlidingWindowLimiter(
                        max_requests=lc.limit or 0,
                        window_seconds=lc.window_seconds or 60,
                    ),
                ))
            else:
                pairs.append((
                    f"limit_{i}",
                    FixedWindowLimiter(
                        max_requests=lc.limit or 0,
                        window_seconds=lc.window_seconds or 60,
                    ),
                ))
        limiters[name] = CompositeLimit(pairs)
        logger.debug(
            "Built limiter for policy '{}': strategy={}, {} limit(s), scope={}",
            name,
            policy.strategy,
            len(policy.limits),
            policy.scope,
        )
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


def _evaluate_component(body: bytes, comp: ComponentConfig) -> int:
    """Evaluate a single input/output component to produce a cost value."""
    match comp.strategy:
        case ComponentStrategy.fixed:
            return comp.value if comp.value is not None else 1
        case ComponentStrategy.random:
            r = comp.range or (1, 1)
            return _RNG.randint(r[0], r[1])
        case ComponentStrategy.characters_div_4:
            return len(body) // 4


async def _estimate_resources(
    request: Request,
    ep_cfg: EndpointConfig,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Compute per-resource estimated values for a request.

    Returns (totals, inputs, outputs):
    - totals[name] = input + output
    - inputs[name] = just the input component
    - outputs[name] = just the output component
    """
    if not ep_cfg.resources:
        return {}, {}, {}

    body = await request.body()
    totals: dict[str, int] = {}
    inputs: dict[str, int] = {}
    outputs: dict[str, int] = {}
    for name, cfg in ep_cfg.resources.items():
        input_val = _evaluate_component(body, cfg.input)
        output_val = _evaluate_component(body, cfg.output)
        totals[name] = input_val + output_val
        inputs[name] = input_val
        outputs[name] = output_val
        logger.debug(
            "Resource estimation: {}={} (in={}, out={})",
            name, totals[name], input_val, output_val,
        )

    return totals, inputs, outputs


def _resolve_costs(
    policy: PolicyConfig,
    totals: dict[str, int],
    inputs: dict[str, int],
    outputs: dict[str, int],
) -> dict[str, int]:
    """Build per-limiter cost dict from the policy's limit configs.

    Supports dotted dimensions: ``tokens.input``, ``tokens.output``.
    Plain dimension names use the total (input + output).
    Falls back to 1 if the resource is missing or zero.
    """
    costs: dict[str, int] = {}
    for i, lc in enumerate(policy.limits):
        dim = lc.dimension
        if "." in dim:
            resource, component = dim.split(".", 1)
            if component == "input":
                val = inputs.get(resource)
            elif component == "output":
                val = outputs.get(resource)
            else:
                val = totals.get(resource)
        else:
            val = totals.get(dim)
        costs[f"limit_{i}"] = val if val is not None and val > 0 else 1
    return costs


def _extract_base_path(spec_path: str) -> str:
    """Read the first ``servers[].url`` from the spec and return its path."""
    raw = Path(spec_path).read_text(encoding="utf-8")
    spec: dict[str, Any] = yaml.safe_load(raw)
    servers: list[dict[str, Any]] = spec.get("servers", [])
    if not servers:
        logger.debug("No servers defined in spec, using empty base path")
        return ""
    url: str = servers[0].get("url", "")
    base = urlparse(url).path.rstrip("/")
    logger.debug("Extracted base path '{}' from server URL '{}'", base, url)
    return base


def create_app(spec_path: str, rate_config_path: str) -> FastAPI:
    """Build a fully-wired FastAPI application."""
    logger.info(
        "Creating mocklimit app from spec='{}' config='{}'",
        spec_path,
        rate_config_path,
    )

    routes = parse_spec(spec_path)
    responses = generate_all_responses(spec_path)
    config = load_config(rate_config_path)
    limiters = _build_limiters(config)
    stats = StatsTracker()
    metrics = MetricsTracker()
    route_table = _build_route_table(routes, config)
    base_path = _extract_base_path(spec_path)

    app = FastAPI(title="mocklimit")
    router = APIRouter(prefix=base_path)

    limited_count = 0
    plain_count = 0

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
                stats=stats,
                metrics=metrics,
            )
            _register_limited_route(router, route, ctx)
            limited_count += 1
            logger.debug(
                "Registered rate-limited route: {} (policy='{}')",
                route_key,
                ep_cfg.policy,
            )
        else:
            _register_plain_route(router, route, dummy_body)
            plain_count += 1
            if ep_cfg is None:
                logger.warning("No rate-limit policy for route {}", route_key)
            logger.debug("Registered plain route: {}", route_key)

    app.include_router(router)

    async def get_stats(_request: Request) -> JSONResponse:
        return JSONResponse(content=stats.snapshot())

    async def get_routes(_request: Request) -> JSONResponse:
        return JSONResponse(content=route_table)

    app.add_api_route("/mocklimit/stats", get_stats, methods=["GET"])
    app.add_api_route("/mocklimit/routes", get_routes, methods=["GET"])
    app.mount("/metrics", metrics.make_asgi_app())

    logger.info(
        "App ready: {} routes registered ({} rate-limited, {} plain) under '{}'",
        limited_count + plain_count,
        limited_count,
        plain_count,
        base_path or "/",
    )

    return app


def _register_limited_route(
    router: APIRouter,
    route: RouteDefinition,
    ctx: _RouteContext,
) -> None:
    """Register a rate-limited route on *router*."""

    async def handler(request: Request) -> JSONResponse:
        start = time.monotonic()
        scope_key = _extract_scope_key(request, ctx.policy)
        ctx.stats.record_request(ctx.route_key, scope_key)

        totals, inputs, outputs = await _estimate_resources(request, ctx.ep_cfg)
        costs = _resolve_costs(ctx.policy, totals, inputs, outputs)

        result = ctx.limiter.check(scope_key, costs)
        lr = _most_restrictive(result)
        headers = _rate_limit_headers(result, ctx.policy)

        if not result.allowed:
            ctx.stats.record_limited(ctx.route_key, scope_key)
            duration = time.monotonic() - start
            ctx.metrics.observe_request(
                endpoint=ctx.route_key,
                method=request.method,
                scope_key=scope_key,
                status=429,
                duration_seconds=duration,
                remaining=lr.remaining,
                policy=ctx.ep_cfg.policy,
            )
            logger.info(
                "{} {} -> 429 (denied_by={}, remaining={}, costs={}) [{:.1f}ms]",
                request.method,
                request.url.path,
                result.denied_by,
                lr.remaining,
                costs,
                duration * 1000,
            )

            if ctx.policy.error_template:
                denied_idx = (
                    int(result.denied_by.split("_")[1])
                    if result.denied_by
                    else 0
                )
                denied_limit = ctx.policy.limits[denied_idx]
                error_body = render_error_body(ErrorContext(
                    provider=ctx.policy.error_template.provider,
                    denied_by_limit=denied_limit,
                    limit=lr.limit,
                    reset_seconds=lr.reset_after_seconds,
                    retry_seconds=lr.retry_after_seconds,
                    fmt=ctx.policy.format,
                ))
                return JSONResponse(
                    status_code=429,
                    content=error_body,
                    headers=headers,
                )

            return JSONResponse(
                status_code=429,
                content=ctx.dummy_body,
                headers=headers,
            )

        latency_min, latency_max = (0, 0)
        if ctx.ep_cfg.timing:
            latency_min, latency_max = ctx.ep_cfg.timing.base_ms
        elif ctx.policy.response_latency_ms != (0, 0):
            latency_min, latency_max = ctx.policy.response_latency_ms

        delay_ms = _RNG.uniform(latency_min, latency_max) if latency_max > 0 else 0.0

        if ctx.ep_cfg.timing and ctx.ep_cfg.timing.scale:
            sc = ctx.ep_cfg.timing.scale
            output_val = outputs.get(sc.resource, 0)
            delay_ms += output_val * sc.ms_per_unit

        if delay_ms > 0:
            logger.debug(
                "Simulating {:.0f}ms response latency for {}",
                delay_ms,
                ctx.route_key,
            )
            await asyncio.sleep(delay_ms / 1000)

        body: dict[str, Any] = dict(ctx.dummy_body)
        if totals:
            body["usage"] = totals

        duration = time.monotonic() - start
        ctx.metrics.observe_request(
            endpoint=ctx.route_key,
            method=request.method,
            scope_key=scope_key,
            status=200,
            duration_seconds=duration,
            remaining=lr.remaining,
            policy=ctx.ep_cfg.policy,
        )
        logger.info(
            "{} {} -> 200 (remaining={}, costs={}) [{:.1f}ms]",
            request.method,
            request.url.path,
            lr.remaining,
            costs,
            duration * 1000,
        )
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
    route_key = f"{route.method} {route.path}"

    async def handler(request: Request) -> JSONResponse:
        logger.info("{} {} -> 200 (no rate limit)", request.method, request.url.path)
        return JSONResponse(content=body)

    router.add_api_route(
        route.path,
        handler,
        methods=[route.method],
        name=route.operation_id or route_key,
    )
