"""Tests for sub-second fixed windows and the quantized rate limiter."""

from __future__ import annotations

from unittest.mock import patch

from mocklimit.ratelimit import FixedWindowLimiter, QuantizedLimiter


class TestSubSecondFixedWindow:
    """FixedWindowLimiter with a 100ms window enforces per-window limits."""

    def test_allows_then_denies_within_window(self) -> None:
        """One request allowed, second denied inside the same 100ms window."""
        limiter = FixedWindowLimiter(max_requests=1, window_seconds=0.1)

        with patch("time.time", return_value=1000.0):
            first = limiter.check("k")
            second = limiter.check("k")

        assert first.allowed is True
        assert first.remaining == 0
        assert second.allowed is False
        assert second.remaining == 0

    def test_allows_again_after_window_rolls(self) -> None:
        """Advancing 100ms crosses into the next window and resets budget."""
        limiter = FixedWindowLimiter(max_requests=1, window_seconds=0.1)

        with patch("time.time", return_value=1000.0):
            limiter.check("k")

        with patch("time.time", return_value=1000.1):
            result = limiter.check("k")

        assert result.allowed is True
        assert result.remaining == 0


class TestInnerLimitDenies:
    """Inner window catches bursts the outer window would allow."""

    def test_11th_request_denied_by_inner(self) -> None:
        """600/60s outer, 10/1s inner: 11th request at same instant denied."""
        limiter = QuantizedLimiter(
            outer_max_requests=600,
            outer_window_seconds=60.0,
            inner_max_requests=10,
            inner_window_seconds=1.0,
        )

        with patch("time.time", return_value=1000.0):
            results = [limiter.check("k") for _ in range(11)]

        for r in results[:10]:
            assert r.allowed is True
        assert results[10].allowed is False
        assert results[10].retry_after_seconds > 0.0


class TestInnerResetsAfterOneSecond:
    """Inner window resets after its 1-second duration."""

    def test_next_batch_allowed_after_inner_reset(self) -> None:
        """Exhaust inner at t=1000, advance to t=1001, next 10 succeed."""
        limiter = QuantizedLimiter(
            outer_max_requests=600,
            outer_window_seconds=60.0,
            inner_max_requests=10,
            inner_window_seconds=1.0,
        )

        with patch("time.time", return_value=1000.0):
            for _ in range(10):
                limiter.check("k")

        with patch("time.time", return_value=1001.0):
            results = [limiter.check("k") for _ in range(10)]

        for r in results:
            assert r.allowed is True


class TestFullOuterWindow:
    """600 requests spread evenly at 10/s over 60 seconds all pass."""

    def test_600_requests_at_10_per_second(self) -> None:
        """Send exactly 10 requests per second for 60 seconds.

        Start at t=960 (a 60-second window boundary) so all 600
        requests land in the same outer window [960, 1020).
        """
        limiter = QuantizedLimiter(
            outer_max_requests=600,
            outer_window_seconds=60.0,
            inner_max_requests=10,
            inner_window_seconds=1.0,
        )

        base = 960.0
        for second in range(60):
            with patch("time.time", return_value=base + second):
                for _ in range(10):
                    result = limiter.check("k")
                    assert result.allowed is True


class TestOuterLimitDenies:
    """Request 601 is denied by the outer window even when inner has room."""

    def test_601st_request_denied(self) -> None:
        """After 600 allowed requests the next one hits the outer ceiling.

        All requests use base=960 so they stay in outer window 16
        [960, 1020).  The 601st request at t=1019.5 is still inside
        the same outer window but exceeds its 600-request budget.
        """
        limiter = QuantizedLimiter(
            outer_max_requests=600,
            outer_window_seconds=60.0,
            inner_max_requests=10,
            inner_window_seconds=1.0,
        )

        base = 960.0
        for second in range(60):
            with patch("time.time", return_value=base + second):
                for _ in range(10):
                    limiter.check("k")

        with patch("time.time", return_value=base + 59.5):
            result = limiter.check("k")

        assert result.allowed is False
        assert result.retry_after_seconds > 0.0
