"""Integration tests for the ``cap_field`` output clamp.

Mirrors real LLM accounting: a ``random`` output component declaring
``cap_field: max_tokens`` never charges more output tokens than the caller's
``max_tokens``. The draw range here ([400, 500]) sits well above the test caps,
so the cap binds deterministically when present.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mocklimit.server import create_app

_OPENAPI_FIXTURES = Path(__file__).parent.parent / "openapi" / "fixtures"

_CAP_CONFIG = """\
policies:
  chat:
    strategy: fixed_window
    limits:
      - dimension: requests
        limit: 100000
        window_seconds: 60
      - dimension: tokens
        limit: 100000000
        window_seconds: 60
    scope: api_key
endpoints:
  /chat/completions:
    methods: [POST]
    policy: chat
    resources:
      requests:
        input: {strategy: fixed, value: 1}
        output: {strategy: fixed, value: 0}
      tokens:
        input: {strategy: characters_div_4}
        output: {strategy: random, range: [400, 500], cap_field: max_tokens}
"""


@pytest.fixture
def cap_client(tmp_path: Path) -> TestClient:
    """Return a client whose output draw [400,500] is capped by max_tokens."""
    cfg = tmp_path / "cap_field.yaml"
    cfg.write_text(_CAP_CONFIG)
    app = create_app(
        spec_path=str(_OPENAPI_FIXTURES / "openai_subset.yaml"),
        rate_config_path=str(cfg),
    )
    return TestClient(app)


def _post(client: TestClient, body: dict[str, object]) -> int:
    body = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}], **body}
    resp = client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": "Bearer cap-key"},
    )
    assert resp.status_code == 200
    return int(resp.json()["usage"]["tokens"])


class TestCapField:
    """``cap_field: max_tokens`` clamps the simulated completion length."""

    def test_output_clamped_to_max_tokens(self, cap_client: TestClient) -> None:
        """A small max_tokens clamps output far below the [400,500] draw range."""
        for _ in range(20):
            tokens = _post(cap_client, {"max_tokens": 50})
            # input("hi" body) is tiny; output is clamped to 50, so total << 400.
            assert tokens <= 250

    def test_output_uncapped_without_max_tokens(self, cap_client: TestClient) -> None:
        """Without max_tokens the output draw is returned uncapped (>= 400)."""
        for _ in range(20):
            tokens = _post(cap_client, {})
            assert tokens >= 400

    def test_bool_max_tokens_is_not_a_cap(self, cap_client: TestClient) -> None:
        """A boolean max_tokens is ignored (bool subclasses int) -> uncapped."""
        tokens = _post(cap_client, {"max_tokens": True})
        assert tokens >= 400
