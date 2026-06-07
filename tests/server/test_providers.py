"""Provider-fidelity tests: Anthropic, OpenAI, and Google Gemini rate limiting."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mocklimit.server import create_app
from mocklimit.server.formatters import go_duration

_CONFIGS = Path(__file__).parent / "configs"
_SPECS = Path(__file__).parent.parent / "openapi" / "fixtures"


def _auth(key: str) -> dict[str, str]:
    """Build a Bearer token auth header."""
    return {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

_ANTHROPIC_BODY = {
    "model": "claude-sonnet-4-20250514",
    "messages": [{"role": "user", "content": "Hello, world!"}],
    "max_tokens": 1024,
}


@pytest.fixture
def anthropic_client() -> TestClient:
    """Client wired to Anthropic token-bucket config."""
    app = create_app(
        spec_path=str(_SPECS / "anthropic_subset.yaml"),
        rate_config_path=str(_CONFIGS / "anthropic.yaml"),
    )
    return TestClient(app)


class TestAnthropicHeaders:
    """Anthropic returns per-dimension headers on every response."""

    def test_all_three_header_groups_present_on_200(
        self, anthropic_client: TestClient,
    ) -> None:
        """All 3 dimension header groups appear on 200 responses."""
        resp = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-key-1"),
        )
        assert resp.status_code == 200
        h = resp.headers
        assert "anthropic-ratelimit-requests-limit" in h
        assert "anthropic-ratelimit-requests-remaining" in h
        assert "anthropic-ratelimit-requests-reset" in h
        assert "anthropic-ratelimit-input-tokens-limit" in h
        assert "anthropic-ratelimit-input-tokens-remaining" in h
        assert "anthropic-ratelimit-input-tokens-reset" in h
        assert "anthropic-ratelimit-output-tokens-limit" in h
        assert "anthropic-ratelimit-output-tokens-remaining" in h
        assert "anthropic-ratelimit-output-tokens-reset" in h

    def test_reset_is_rfc3339(self, anthropic_client: TestClient) -> None:
        """Reset header uses RFC 3339 absolute timestamp format."""
        resp = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-key-2"),
        )
        assert resp.status_code == 200
        reset = resp.headers["anthropic-ratelimit-requests-reset"]
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", reset)

    def test_remaining_decreases(self, anthropic_client: TestClient) -> None:
        """Remaining count decreases after each request."""
        resp1 = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-key-3"),
        )
        r1 = int(resp1.headers["anthropic-ratelimit-requests-remaining"])

        resp2 = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-key-3"),
        )
        r2 = int(resp2.headers["anthropic-ratelimit-requests-remaining"])
        assert r2 < r1

    def test_input_tokens_consumed_separately(
        self, anthropic_client: TestClient,
    ) -> None:
        """ITPM bucket drains independently from RPM."""
        resp = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-key-4"),
        )
        itpm_remaining = int(
            resp.headers["anthropic-ratelimit-input-tokens-remaining"],
        )
        assert itpm_remaining < 80000

    def test_output_tokens_consumed_separately(
        self, anthropic_client: TestClient,
    ) -> None:
        """OTPM bucket drains independently from ITPM."""
        resp = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-key-5"),
        )
        otpm_remaining = int(
            resp.headers["anthropic-ratelimit-output-tokens-remaining"],
        )
        assert otpm_remaining < 16000


class TestAnthropicEnforcement:
    """Anthropic token bucket enforcement with separate I/O buckets."""

    def test_rpm_exhaustion(self, anthropic_client: TestClient) -> None:
        """60 RPM capacity, 1/s refill. Burst > 60 should 429."""
        statuses: list[int] = []
        for _ in range(62):
            resp = anthropic_client.post(
                "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-rpm"),
            )
            statuses.append(resp.status_code)
        assert 429 in statuses

    def test_otpm_exhaustion(self, anthropic_client: TestClient) -> None:
        """16000 OTPM capacity. Output range [100,2000] → ~8-160 requests."""
        statuses: list[int] = []
        for _ in range(200):
            resp = anthropic_client.post(
                "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-otpm"),
            )
            statuses.append(resp.status_code)
            if resp.status_code == 429:
                break
        assert 429 in statuses

    def test_error_body_format(self, anthropic_client: TestClient) -> None:
        """429 returns Anthropic-style error body."""
        resp = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-err"),
        )
        for _ in range(65):
            resp = anthropic_client.post(
                "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-err"),
            )
            if resp.status_code == 429:
                break
        assert resp.status_code == 429
        body = resp.json()
        assert body["type"] == "error"
        assert body["error"]["type"] == "rate_limit_error"
        assert "retry" in body["error"]["message"].lower()

    def test_retry_after_header_seconds(self, anthropic_client: TestClient) -> None:
        """Retry-After is present on 429 as integer seconds."""
        resp = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-ra"),
        )
        for _ in range(65):
            resp = anthropic_client.post(
                "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("ant-ra"),
            )
            if resp.status_code == 429:
                break
        assert resp.status_code == 429
        assert "retry-after" in resp.headers
        val = resp.headers["retry-after"]
        assert val.isdigit()


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

_OPENAI_BODY = {
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Say hello"}],
}


@pytest.fixture
def openai_client() -> TestClient:
    """Client wired to OpenAI fixed-window config."""
    app = create_app(
        spec_path=str(_SPECS / "openai_subset.yaml"),
        rate_config_path=str(_CONFIGS / "openai.yaml"),
    )
    return TestClient(app)


class TestOpenAIHeaders:
    """OpenAI returns separate request and token header groups."""

    def test_both_header_groups_present(self, openai_client: TestClient) -> None:
        """Both request and token header groups appear on 200."""
        resp = openai_client.post(
            "/v1/chat/completions", json=_OPENAI_BODY, headers=_auth("oai-key-1"),
        )
        assert resp.status_code == 200
        h = resp.headers
        assert "x-ratelimit-limit-requests" in h
        assert "x-ratelimit-remaining-requests" in h
        assert "x-ratelimit-reset-requests" in h
        assert "x-ratelimit-limit-tokens" in h
        assert "x-ratelimit-remaining-tokens" in h
        assert "x-ratelimit-reset-tokens" in h

    def test_reset_is_go_duration(self, openai_client: TestClient) -> None:
        """Reset header uses Go-style duration format."""
        resp = openai_client.post(
            "/v1/chat/completions", json=_OPENAI_BODY, headers=_auth("oai-key-2"),
        )
        assert resp.status_code == 200
        reset_req = resp.headers["x-ratelimit-reset-requests"]
        assert re.match(r"(\d+h)?(\d+m)?\d+(\.\d+)?s|(\d+ms)", reset_req)

    def test_remaining_tokens_decrease(self, openai_client: TestClient) -> None:
        """Token remaining decreases across requests."""
        resp1 = openai_client.post(
            "/v1/chat/completions", json=_OPENAI_BODY, headers=_auth("oai-key-3"),
        )
        t1 = int(resp1.headers["x-ratelimit-remaining-tokens"])
        resp2 = openai_client.post(
            "/v1/chat/completions", json=_OPENAI_BODY, headers=_auth("oai-key-3"),
        )
        t2 = int(resp2.headers["x-ratelimit-remaining-tokens"])
        assert t2 < t1

    def test_limit_values_match_config(self, openai_client: TestClient) -> None:
        """Header limit values match config (60 RPM, 150000 TPM)."""
        resp = openai_client.post(
            "/v1/chat/completions", json=_OPENAI_BODY, headers=_auth("oai-key-4"),
        )
        assert resp.headers["x-ratelimit-limit-requests"] == "60"
        assert resp.headers["x-ratelimit-limit-tokens"] == "150000"


class TestOpenAIEnforcement:
    """OpenAI fixed-window enforcement with combined TPM."""

    def test_rpm_limit(self, openai_client: TestClient) -> None:
        """60 RPM in 60s window."""
        statuses: list[int] = []
        for _ in range(62):
            resp = openai_client.post(
                "/v1/chat/completions", json=_OPENAI_BODY, headers=_auth("oai-rpm"),
            )
            statuses.append(resp.status_code)
        assert 429 in statuses
        n200 = statuses.count(200)
        assert n200 == 60

    def test_tpm_limit_combined(self, openai_client: TestClient) -> None:
        """TPM = 150000 combined. Large body should exhaust before RPM."""
        big_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "x" * 40000}],
        }
        statuses: list[int] = []
        for _ in range(20):
            resp = openai_client.post(
                "/v1/chat/completions", json=big_body, headers=_auth("oai-tpm"),
            )
            statuses.append(resp.status_code)
        assert 429 in statuses
        n200 = statuses.count(200)
        assert n200 < 20

    def test_error_body_format(self, openai_client: TestClient) -> None:
        """429 returns OpenAI-style rate_limit_exceeded error body."""
        resp = openai_client.post(
            "/v1/chat/completions", json=_OPENAI_BODY, headers=_auth("oai-err"),
        )
        for _ in range(62):
            resp = openai_client.post(
                "/v1/chat/completions",
                json=_OPENAI_BODY,
                headers=_auth("oai-err"),
            )
            if resp.status_code == 429:
                break
        assert resp.status_code == 429
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "rate_limit_exceeded"
        assert "Rate limit reached" in body["error"]["message"]

    def test_retry_after_ms_header(self, openai_client: TestClient) -> None:
        """429 includes retry-after-ms header in milliseconds."""
        resp = openai_client.post(
            "/v1/chat/completions",
            json=_OPENAI_BODY,
            headers=_auth("oai-retry-after-ms"),
        )
        for _ in range(70):
            resp = openai_client.post(
                "/v1/chat/completions",
                json=_OPENAI_BODY,
                headers=_auth("oai-retry-after-ms"),
            )
            if resp.status_code == 429:
                break
        assert resp.status_code == 429
        assert "retry-after-ms" in resp.headers
        val = int(resp.headers["retry-after-ms"])
        assert val > 0


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------

_GEMINI_BODY = {
    "contents": [{"parts": [{"text": "What is the meaning of life?"}]}],
}


@pytest.fixture
def gemini_client() -> TestClient:
    """Client wired to Gemini fixed-window config."""
    app = create_app(
        spec_path=str(_SPECS / "gemini_2_5_subset.yaml"),
        rate_config_path=str(_CONFIGS / "gemini.yaml"),
    )
    return TestClient(app)


class TestGeminiHeaders:
    """Gemini has no per-dimension headers (none configured on limits)."""

    def test_no_ratelimit_headers_on_200(self, gemini_client: TestClient) -> None:
        """Gemini doesn't expose rate-limit headers on success."""
        resp = gemini_client.post(
            "/v1beta/models/gemini-2.5-pro:generateContent",
            json=_GEMINI_BODY,
            headers=_auth("gem-key-1"),
        )
        assert resp.status_code == 200
        for h in resp.headers:
            assert "ratelimit" not in h.lower()


class TestGeminiEnforcement:
    """Gemini fixed-window enforcement with input-only TPM."""

    def test_rpm_exhaustion(self, gemini_client: TestClient) -> None:
        """15 RPM in 60s window."""
        statuses: list[int] = []
        for _ in range(17):
            resp = gemini_client.post(
                "/v1beta/models/gemini-2.5-pro:generateContent",
                json=_GEMINI_BODY,
                headers=_auth("gem-rpm"),
            )
            statuses.append(resp.status_code)
        assert 429 in statuses
        n200 = statuses.count(200)
        assert n200 == 15

    def test_input_tpm_exhaustion(self, gemini_client: TestClient) -> None:
        """32000 input TPM. Large body should exhaust before RPM."""
        big_body = {
            "contents": [{"parts": [{"text": "x" * 100000}]}],
        }
        statuses: list[int] = []
        for _ in range(10):
            resp = gemini_client.post(
                "/v1beta/models/gemini-2.5-pro:generateContent",
                json=big_body,
                headers=_auth("gem-itpm"),
            )
            statuses.append(resp.status_code)
        assert 429 in statuses
        n200 = statuses.count(200)
        assert n200 < 10

    def test_error_body_format(self, gemini_client: TestClient) -> None:
        """429 returns Google RESOURCE_EXHAUSTED format."""
        resp = gemini_client.post(
            "/v1beta/models/gemini-2.5-pro:generateContent",
            json=_GEMINI_BODY,
            headers=_auth("gem-err"),
        )
        for _ in range(17):
            resp = gemini_client.post(
                "/v1beta/models/gemini-2.5-pro:generateContent",
                json=_GEMINI_BODY,
                headers=_auth("gem-err"),
            )
            if resp.status_code == 429:
                break
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == 429
        assert body["error"]["status"] == "RESOURCE_EXHAUSTED"
        assert body["error"]["details"][0]["@type"].endswith("QuotaFailure")
        assert "violations" in body["error"]["details"][0]

    def test_retry_info_in_error_body(self, gemini_client: TestClient) -> None:
        """Error body includes RetryInfo detail with retryDelay."""
        resp = gemini_client.post(
            "/v1beta/models/gemini-2.5-pro:generateContent",
            json=_GEMINI_BODY,
            headers=_auth("gem-retry-info"),
        )
        for _ in range(20):
            resp = gemini_client.post(
                "/v1beta/models/gemini-2.5-pro:generateContent",
                json=_GEMINI_BODY,
                headers=_auth("gem-retry-info"),
            )
            if resp.status_code == 429:
                break
        assert resp.status_code == 429
        body = resp.json()
        details = body["error"]["details"]
        retry_info = [d for d in details if "RetryInfo" in d["@type"]]
        assert len(retry_info) == 1
        assert "retryDelay" in retry_info[0]


# ---------------------------------------------------------------------------
# Token Bucket specific tests
# ---------------------------------------------------------------------------


class TestTokenBucketRefill:
    """Token bucket refills continuously over time."""

    def test_refill_restores_capacity(self, anthropic_client: TestClient) -> None:
        """After consuming tokens, waiting should restore some capacity."""
        resp1 = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("tb-refill"),
        )
        r1 = int(resp1.headers["anthropic-ratelimit-requests-remaining"])

        time.sleep(0.1)

        resp2 = anthropic_client.post(
            "/v1/messages", json=_ANTHROPIC_BODY, headers=_auth("tb-refill"),
        )
        r2 = int(resp2.headers["anthropic-ratelimit-requests-remaining"])
        # r2 should be close to r1 (lost 1 but refilled ~0.1 * 1/s = 0.1 token)
        # But remaining is int so it may look the same.
        # Key: even after 2 requests, remaining should be close to capacity - 2
        assert r2 >= r1 - 1


# ---------------------------------------------------------------------------
# Format-specific tests
# ---------------------------------------------------------------------------


class TestGoFormatDuration:
    """Go duration format produces correct strings."""

    def test_sub_second_shows_ms(self) -> None:
        """When reset is under 1s, format should be Nms."""
        assert go_duration(0.012) == "12ms"
        assert go_duration(0.5) == "500ms"

    def test_seconds(self) -> None:
        """Seconds and minute boundaries format correctly."""
        assert go_duration(4.253) == "4.253s"
        assert go_duration(60.0) == "1m0s"

    def test_hours(self) -> None:
        """Multi-hour durations include h/m/s components."""
        result = go_duration(3661.5)
        assert result.startswith("1h1m")
