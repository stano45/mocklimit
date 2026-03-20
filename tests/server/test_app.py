"""Integration tests for the FastAPI mock server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import openai
import pytest
from starlette.testclient import TestClient

from mocklimit.server import create_app

_FIXTURES = Path(__file__).parent / "fixtures"
_OPENAPI_FIXTURES = Path(__file__).parent.parent / "openapi" / "fixtures"


@pytest.fixture
def app_client() -> TestClient:
    """Return a ``TestClient`` wired to the test spec and rate config."""
    app = create_app(
        spec_path=str(_OPENAPI_FIXTURES / "openai_subset.yaml"),
        rate_config_path=str(_FIXTURES / "rate_config.yaml"),
    )
    return TestClient(app)


def _auth_header(key: str) -> dict[str, str]:
    """Build an ``Authorization: Bearer`` header dict."""
    return {"Authorization": f"Bearer {key}"}


class TestAllowedRequest:
    """Requests under the rate limit return 200 with correct headers."""

    def test_returns_200_with_dummy_body(self, app_client: TestClient) -> None:
        """A single POST to a rate-limited endpoint returns the dummy response."""
        resp = app_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers=_auth_header("key-a"),
        )

        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert "id" in body
        assert "choices" in body
        assert body["object"] == "chat.completion"

    def test_rate_limit_headers_present(self, app_client: TestClient) -> None:
        """Allowed responses carry the configured rate-limit headers."""
        resp = app_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": []},
            headers=_auth_header("key-b"),
        )

        assert resp.headers.get("x-ratelimit-limit-requests") == "5"
        assert "x-ratelimit-remaining-requests" in resp.headers
        assert "x-ratelimit-reset-requests" in resp.headers


class TestDeniedRequest:
    """Requests over the rate limit return 429 with correct body and headers."""

    def test_returns_429_after_limit_exceeded(
        self,
        app_client: TestClient,
    ) -> None:
        """After 5 allowed requests the 6th gets 429."""
        for _ in range(5):
            app_client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": []},
                headers=_auth_header("key-flood"),
            )

        resp = app_client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers=_auth_header("key-flood"),
        )

        assert resp.status_code == 429
        body: dict[str, Any] = resp.json()
        assert isinstance(body, dict)
        assert "retry-after" in resp.headers

    def test_retry_after_header_set(self, app_client: TestClient) -> None:
        """A 429 response includes a ``Retry-After`` header."""
        for _ in range(5):
            app_client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": []},
                headers=_auth_header("key-retry"),
            )

        resp = app_client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers=_auth_header("key-retry"),
        )

        assert resp.status_code == 429
        retry_after = resp.headers.get("retry-after")
        assert retry_after is not None
        assert int(retry_after) > 0


class TestIndependentAPIKeys:
    """Different API keys maintain independent rate limits."""

    def test_key_b_unaffected_by_key_a(self, app_client: TestClient) -> None:
        """Exhausting key-a does not block key-b."""
        for _ in range(5):
            app_client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": []},
                headers=_auth_header("key-ind-a"),
            )

        denied = app_client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers=_auth_header("key-ind-a"),
        )
        assert denied.status_code == 429

        allowed = app_client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers=_auth_header("key-ind-b"),
        )
        assert allowed.status_code == 200


class TestStatsEndpoint:
    """``GET /mocklimit/stats`` reports correct request counts."""

    def test_stats_reflect_requests_and_denials(
        self,
        app_client: TestClient,
    ) -> None:
        """After 6 requests (5 allowed + 1 denied) stats are accurate."""
        key = "key-stats"
        for _ in range(6):
            app_client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": []},
                headers=_auth_header(key),
            )

        resp = app_client.get("/mocklimit/stats")
        assert resp.status_code == 200
        data: dict[str, Any] = resp.json()

        endpoint_stats = data["POST /chat/completions"][key]
        assert endpoint_stats["total_requests"] == 6
        assert endpoint_stats["total_429s"] == 1


class TestRoutesEndpoint:
    """``GET /mocklimit/routes`` lists all spec routes with policy info."""

    def test_lists_all_routes(self, app_client: TestClient) -> None:
        """Every route from the OpenAPI spec appears in the listing."""
        resp = app_client.get("/mocklimit/routes")
        assert resp.status_code == 200
        routes: list[dict[str, str | None]] = resp.json()

        paths_methods = {(r["path"], r["method"]) for r in routes}
        assert ("/chat/completions", "POST") in paths_methods
        assert ("/chat/completions", "GET") in paths_methods
        assert ("/embeddings", "POST") in paths_methods

    def test_marks_rate_limited_routes(self, app_client: TestClient) -> None:
        """Rate-limited routes show the policy name; others show null."""
        resp = app_client.get("/mocklimit/routes")
        routes: list[dict[str, str | None]] = resp.json()

        chat_post = next(
            r for r in routes
            if r["path"] == "/chat/completions" and r["method"] == "POST"
        )
        assert chat_post["policy"] == "openai_chat"

        embeddings = next(
            r for r in routes if r["path"] == "/embeddings"
        )
        assert embeddings["policy"] is None


class TestPlainEndpoint:
    """Endpoints without a rate-limit policy return 200 immediately."""

    def test_unprotected_endpoint_returns_200(
        self,
        app_client: TestClient,
    ) -> None:
        """GET /v1/chat/completions has no policy and returns a dummy response."""
        resp = app_client.get("/v1/chat/completions")

        assert resp.status_code == 200
        assert "x-ratelimit-limit-requests" not in resp.headers

    def test_embeddings_returns_200(self, app_client: TestClient) -> None:
        """POST /v1/embeddings has no policy and returns a dummy response."""
        resp = app_client.post(
            "/v1/embeddings",
            json={"model": "text-embedding-ada-002", "input": "hello"},
        )

        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert "data" in body


class TestPrefixMounting:
    """Spec routes are only reachable under the /v1 prefix."""

    def test_without_prefix_returns_404(self, app_client: TestClient) -> None:
        """A request to /chat/completions (no /v1) gets 404."""
        resp = app_client.post(
            "/chat/completions",
            json={"model": "gpt-4", "messages": []},
            headers=_auth_header("key-no-prefix"),
        )

        assert resp.status_code == 404


class TestTokenEstimation:
    """Token estimation adds a ``usage`` field to the response."""

    def test_usage_field_present(self, app_client: TestClient) -> None:
        """POST /v1/chat/completions includes prompt and completion token counts."""
        resp = app_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers=_auth_header("key-tok"),
        )

        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        usage = body["usage"]
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        expected = usage["prompt_tokens"] + usage["completion_tokens"]
        assert usage["total_tokens"] == expected


class TestOpenAISDKIntegration:
    """The OpenAI Python SDK works against the mock server."""

    def test_chat_completion_returns_valid_response(
        self,
        app_client: TestClient,
    ) -> None:
        """``client.chat.completions.create()`` returns a parseable response."""
        sdk = openai.OpenAI(
            api_key="test-sdk-key",
            base_url="http://testserver/v1",
            http_client=app_client,
        )

        completion = sdk.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": "Say hello"}],
        )

        assert completion.id is not None
        assert completion.object == "chat.completion"
        assert len(completion.choices) >= 1
        assert completion.choices[0].message.content is not None

    def test_rate_limit_raises_sdk_error(self, app_client: TestClient) -> None:
        """Exceeding the limit causes ``openai.RateLimitError``."""
        sdk = openai.OpenAI(
            api_key="test-sdk-limit",
            base_url="http://testserver/v1",
            http_client=app_client,
            max_retries=0,
        )

        for _ in range(5):
            sdk.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": "hi"}],
            )

        with pytest.raises(openai.RateLimitError):
            sdk.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": "hi"}],
            )
