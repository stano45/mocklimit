"""Integration tests for Prometheus metrics instrumentation."""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from mocklimit.server import create_app

_FIXTURES = Path(__file__).parent / "fixtures"
_OPENAPI_FIXTURES = Path(__file__).parent.parent / "openapi" / "fixtures"


def _make_client() -> TestClient:
    app = create_app(
        spec_path=str(_OPENAPI_FIXTURES / "openai_subset.yaml"),
        rate_config_path=str(_FIXTURES / "rate_config.yaml"),
    )
    return TestClient(app)


def _auth_header(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _post_chat(client: TestClient, key: str) -> int:
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        headers=_auth_header(key),
    )
    return resp.status_code


def _scrape(client: TestClient) -> str:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    return resp.text


def _metric_value(
    text: str,
    metric_name: str,
    labels: dict[str, str],
) -> float | None:
    """Extract the value of a Prometheus metric line matching *labels*."""
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if not line.startswith(metric_name):
            continue
        if all(f'{k}="{v}"' in line for k, v in labels.items()):
            parts = line.rsplit(" ", maxsplit=1)
            return float(parts[-1])
    return None


class TestMetricsEndpointExists:
    """GET /metrics returns Prometheus exposition format."""

    def test_returns_200(self) -> None:
        """The metrics endpoint is reachable."""
        client = _make_client()
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_content_type_is_plain_text(self) -> None:
        """Prometheus expects ``text/plain`` exposition."""
        client = _make_client()
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers.get("content-type", "")

    def test_contains_help_lines(self) -> None:
        """At least one HELP comment for our custom metrics is present."""
        client = _make_client()
        body = _scrape(client)
        assert "# HELP mocklimit_requests_total" in body


class TestRequestCounters:
    """mocklimit_requests_total tracks request counts by status."""

    def test_counts_allowed_requests(self) -> None:
        """Three allowed requests show count 3 with status=200."""
        client = _make_client()
        for _ in range(3):
            _post_chat(client, "key-count-ok")

        body = _scrape(client)
        val = _metric_value(
            body,
            "mocklimit_requests_total",
            {"scope_key": "key-count-ok", "status": "200"},
        )
        assert val == 3.0

    def test_counts_denied_requests(self) -> None:
        """Six requests split into 5x200 and 1x429."""
        client = _make_client()
        for _ in range(6):
            _post_chat(client, "key-count-deny")

        body = _scrape(client)
        ok_val = _metric_value(
            body,
            "mocklimit_requests_total",
            {"scope_key": "key-count-deny", "status": "200"},
        )
        deny_val = _metric_value(
            body,
            "mocklimit_requests_total",
            {"scope_key": "key-count-deny", "status": "429"},
        )
        assert ok_val == 5.0
        assert deny_val == 1.0


class TestRateLimitedCounter:
    """mocklimit_rate_limited_total fires only on 429s."""

    def test_not_present_before_denial(self) -> None:
        """A single allowed request does not create a rate_limited counter."""
        client = _make_client()
        _post_chat(client, "key-no-deny")

        body = _scrape(client)
        val = _metric_value(
            body,
            "mocklimit_rate_limited_total",
            {"scope_key": "key-no-deny"},
        )
        assert val is None

    def test_increments_on_denial(self) -> None:
        """After 7 requests (5 ok + 2 denied) the counter shows 2."""
        client = _make_client()
        for _ in range(7):
            _post_chat(client, "key-rl-counter")

        body = _scrape(client)
        val = _metric_value(
            body,
            "mocklimit_rate_limited_total",
            {"scope_key": "key-rl-counter"},
        )
        assert val == 2.0


class TestDurationHistogram:
    """mocklimit_request_duration_seconds records latencies."""

    def test_histogram_buckets_exist(self) -> None:
        """At least one bucket line is emitted after a request."""
        client = _make_client()
        _post_chat(client, "key-hist")

        body = _scrape(client)
        assert "mocklimit_request_duration_seconds_bucket" in body

    def test_count_matches_requests(self) -> None:
        """The histogram _count equals the number of requests made."""
        client = _make_client()
        for _ in range(4):
            _post_chat(client, "key-hist-count")

        body = _scrape(client)
        val = _metric_value(
            body,
            "mocklimit_request_duration_seconds_count",
            {"status": "200"},
        )
        assert val == 4.0


class TestRemainingGauge:
    """mocklimit_rate_limit_remaining reflects the latest window state."""

    def test_remaining_decreases(self) -> None:
        """After one request the remaining gauge equals limit - 1."""
        client = _make_client()
        _post_chat(client, "key-gauge")

        body = _scrape(client)
        val = _metric_value(
            body,
            "mocklimit_rate_limit_remaining",
            {"scope_key": "key-gauge", "policy": "openai_chat"},
        )
        assert val is not None
        assert val == 4.0

    def test_remaining_is_zero_after_exhaustion(self) -> None:
        """After exceeding the limit the gauge sits at zero."""
        client = _make_client()
        for _ in range(6):
            _post_chat(client, "key-gauge-zero")

        body = _scrape(client)
        val = _metric_value(
            body,
            "mocklimit_rate_limit_remaining",
            {"scope_key": "key-gauge-zero", "policy": "openai_chat"},
        )
        assert val == 0.0


class TestRegistryIsolation:
    """Each create_app() gets its own registry -- no cross-pollution."""

    def test_separate_apps_have_independent_metrics(self) -> None:
        """Requests to app A are invisible from app B's /metrics."""
        client_a = _make_client()
        client_b = _make_client()

        for _ in range(3):
            _post_chat(client_a, "key-iso")

        body_a = _scrape(client_a)
        body_b = _scrape(client_b)

        val_a = _metric_value(
            body_a,
            "mocklimit_requests_total",
            {"scope_key": "key-iso", "status": "200"},
        )
        val_b = _metric_value(
            body_b,
            "mocklimit_requests_total",
            {"scope_key": "key-iso", "status": "200"},
        )
        assert val_a == 3.0
        assert val_b is None
