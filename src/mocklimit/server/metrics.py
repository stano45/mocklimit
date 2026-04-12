"""Prometheus metrics instrumentation for mocklimit."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    make_asgi_app,  # pyright: ignore[reportUnknownVariableType]
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp

__all__ = ["MetricsTracker"]


class MetricsTracker:
    """Per-app-instance Prometheus metrics.

    Each ``create_app()`` call should create its own ``MetricsTracker``
    so that tests (which spin up many apps) never hit
    ``Duplicated timeseries`` errors.
    """

    def __init__(self) -> None:
        """Initialise a fresh collector registry and all metric families."""
        self.registry = CollectorRegistry()

        self.requests_total = Counter(
            "mocklimit_requests_total",
            "Total requests to rate-limited endpoints",
            ["endpoint", "method", "scope_key", "status"],
            registry=self.registry,
        )
        self.rate_limited_total = Counter(
            "mocklimit_rate_limited_total",
            "Total requests denied by rate limiting",
            ["endpoint", "method", "scope_key"],
            registry=self.registry,
        )
        self.request_duration = Histogram(
            "mocklimit_request_duration_seconds",
            "Request handling duration in seconds",
            ["endpoint", "method", "status"],
            buckets=(
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.15,
                0.2,
                0.25,
                0.5,
                1.0,
            ),
            registry=self.registry,
        )
        self.remaining_gauge = Gauge(
            "mocklimit_rate_limit_remaining",
            "Remaining requests in the current rate limit window",
            ["endpoint", "policy", "scope_key"],
            registry=self.registry,
        )

    def observe_request(  # noqa: PLR0913
        self,
        *,
        endpoint: str,
        method: str,
        scope_key: str,
        status: int,
        duration_seconds: float,
        remaining: int,
        policy: str,
    ) -> None:
        """Record a single request across all metric families."""
        status_str = str(status)

        self.requests_total.labels(
            endpoint=endpoint,
            method=method,
            scope_key=scope_key,
            status=status_str,
        ).inc()

        if status == 429:  # noqa: PLR2004
            self.rate_limited_total.labels(
                endpoint=endpoint,
                method=method,
                scope_key=scope_key,
            ).inc()

        self.request_duration.labels(
            endpoint=endpoint,
            method=method,
            status=status_str,
        ).observe(duration_seconds)

        self.remaining_gauge.labels(
            endpoint=endpoint,
            policy=policy,
            scope_key=scope_key,
        ).set(remaining)

    def make_asgi_app(self) -> ASGIApp:
        """Return an ASGI app that serves ``/`` as the Prometheus scrape endpoint."""
        return make_asgi_app(registry=self.registry)  # type: ignore[return-value]
