"""Tests for the sliding-window rate limiter."""

from __future__ import annotations

from unittest.mock import patch

from mocklimit.ratelimit import SlidingWindowLimiter


class TestAllowUnderLimit:
    """Requests under the limit are allowed."""

    def test_single_request_allowed(self) -> None:
        """A single request within budget is allowed with correct remaining."""
        with patch("time.time", return_value=1000.0):
            limiter = SlidingWindowLimiter(max_requests=5, window_seconds=10.0)
            result = limiter.check("user-1")

        assert result.allowed is True
        assert result.remaining == 4
        assert result.limit == 5
        assert result.retry_after_seconds == 0.0


class TestDenyAtLimit:
    """Requests exceeding the limit are denied."""

    def test_deny_after_exhausting_budget(self) -> None:
        """After max_requests calls the next one is denied."""
        with patch("time.time", return_value=1000.0):
            limiter = SlidingWindowLimiter(max_requests=3, window_seconds=10.0)
            for _ in range(3):
                limiter.check("user-1")

            result = limiter.check("user-1")

        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after_seconds > 0.0


class TestWindowSlides:
    """Entries expire individually, not all at once."""

    def test_early_entries_expire_while_later_ones_remain(self) -> None:
        """Requests at t=0 expire at t=10, but a request at t=5 persists until t=15."""
        limiter = SlidingWindowLimiter(max_requests=3, window_seconds=10.0)

        with patch("time.time", return_value=1000.0):
            limiter.check("user-1")
            limiter.check("user-1")

        with patch("time.time", return_value=1005.0):
            limiter.check("user-1")

        with patch("time.time", return_value=1005.0):
            denied = limiter.check("user-1")
        assert denied.allowed is False

        with patch("time.time", return_value=1010.1):
            result = limiter.check("user-1")
        assert result.allowed is True
        assert result.remaining == 1

    def test_all_entries_eventually_expire(self) -> None:
        """All entries expire once the full window has passed."""
        limiter = SlidingWindowLimiter(max_requests=2, window_seconds=10.0)

        with patch("time.time", return_value=1000.0):
            limiter.check("user-1")
            limiter.check("user-1")

        with patch("time.time", return_value=1010.1):
            result = limiter.check("user-1")

        assert result.allowed is True
        assert result.remaining == 1


class TestRetryAfterAccuracy:
    """retry_after_seconds reflects the oldest entry's expiry."""

    def test_retry_after_points_to_oldest_entry_expiry(self) -> None:
        """retry_after reflects time until the oldest entry expires."""
        limiter = SlidingWindowLimiter(max_requests=2, window_seconds=10.0)

        with patch("time.time", return_value=1000.0):
            limiter.check("user-1")

        with patch("time.time", return_value=1003.0):
            limiter.check("user-1")

        with patch("time.time", return_value=1005.0):
            result = limiter.check("user-1")

        assert result.allowed is False
        assert 4.9 < result.retry_after_seconds < 5.1


class TestMultipleKeysIndependent:
    """Different keys maintain independent counters."""

    def test_keys_do_not_interfere(self) -> None:
        """Exhausting one key does not affect another."""
        with patch("time.time", return_value=1000.0):
            limiter = SlidingWindowLimiter(max_requests=1, window_seconds=10.0)
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
            limiter = SlidingWindowLimiter(max_requests=5, window_seconds=10.0)
            result = limiter.check("user-1", cost=3)

        assert result.allowed is True
        assert result.remaining == 2


class TestCostExceedingRemainingDenied:
    """A request whose cost exceeds the remaining budget is denied."""

    def test_denied_when_cost_exceeds_remaining(self) -> None:
        """With 2 remaining, a cost-3 request must be denied."""
        with patch("time.time", return_value=1000.0):
            limiter = SlidingWindowLimiter(max_requests=5, window_seconds=10.0)
            limiter.check("user-1", cost=3)
            result = limiter.check("user-1", cost=3)

        assert result.allowed is False
        assert result.remaining == 2
        assert result.retry_after_seconds > 0.0


class TestPeekDoesNotConsume:
    """peek() reports state without consuming budget."""

    def test_peek_does_not_change_state(self) -> None:
        """Multiple peeks followed by a check still show full budget."""
        with patch("time.time", return_value=1000.0):
            limiter = SlidingWindowLimiter(max_requests=2, window_seconds=10.0)
            peek1 = limiter.peek("user-1")
            peek2 = limiter.peek("user-1")
            check = limiter.check("user-1")

        assert peek1.allowed is True
        assert peek1.remaining == 1
        assert peek2.allowed is True
        assert peek2.remaining == 1
        assert check.allowed is True
        assert check.remaining == 1
