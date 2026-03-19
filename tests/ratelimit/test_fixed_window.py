"""Tests for the fixed-window rate limiter."""

from __future__ import annotations

from unittest.mock import patch

from mocklimit.ratelimit import FixedWindowLimiter


class TestAllowUnderLimit:
    """Requests under the limit are allowed."""

    def test_single_request_allowed(self) -> None:
        """A single request within budget is allowed with correct remaining."""
        with patch("time.time", return_value=1000.0):
            limiter = FixedWindowLimiter(max_requests=5, window_seconds=10.0)
            result = limiter.check("user-1")

        assert result.allowed is True
        assert result.remaining == 4
        assert result.limit == 5
        assert result.retry_after_seconds == 0.0
        assert result.reset_after_seconds > 0.0


class TestDenyAtLimit:
    """Requests exceeding the limit are denied."""

    def test_deny_after_exhausting_budget(self) -> None:
        """After max_requests calls the next one is denied."""
        with patch("time.time", return_value=1000.0):
            limiter = FixedWindowLimiter(max_requests=3, window_seconds=10.0)
            for _ in range(3):
                limiter.check("user-1")

            result = limiter.check("user-1")

        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after_seconds > 0.0


class TestWindowReset:
    """Requests are allowed again after the window resets."""

    def test_allowed_after_window_elapses(self) -> None:
        """Advancing time past the window boundary resets the counter."""
        limiter = FixedWindowLimiter(max_requests=2, window_seconds=10.0)

        with patch("time.time", return_value=1000.0):
            limiter.check("user-1")
            limiter.check("user-1")
            denied = limiter.check("user-1")

        assert denied.allowed is False

        with patch("time.time", return_value=1010.0):
            result = limiter.check("user-1")

        assert result.allowed is True
        assert result.remaining == 1


class TestMultipleKeysIndependent:
    """Different keys maintain independent counters."""

    def test_keys_do_not_interfere(self) -> None:
        """Exhausting one key does not affect another."""
        with patch("time.time", return_value=1000.0):
            limiter = FixedWindowLimiter(max_requests=1, window_seconds=10.0)
            first = limiter.check("key-a")
            second = limiter.check("key-b")
            denied = limiter.check("key-a")

        assert first.allowed is True
        assert second.allowed is True
        assert denied.allowed is False


class TestCostGreaterThanOne:
    """Requests with cost > 1 consume multiple units."""

    def test_cost_consumes_multiple_units(self) -> None:
        """A cost of 3 leaves remaining == 2 when max_requests == 5."""
        with patch("time.time", return_value=1000.0):
            limiter = FixedWindowLimiter(max_requests=5, window_seconds=10.0)
            result = limiter.check("user-1", cost=3)

        assert result.allowed is True
        assert result.remaining == 2


class TestCostExceedingRemainingDenied:
    """A request whose cost exceeds the remaining budget is denied."""

    def test_denied_when_cost_exceeds_remaining(self) -> None:
        """With 2 remaining, a cost-3 request must be denied."""
        with patch("time.time", return_value=1000.0):
            limiter = FixedWindowLimiter(max_requests=5, window_seconds=10.0)
            limiter.check("user-1", cost=3)
            result = limiter.check("user-1", cost=3)

        assert result.allowed is False
        assert result.remaining == 2
        assert result.retry_after_seconds > 0.0
