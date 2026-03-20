"""Tests for the composite rate limiter."""

from __future__ import annotations

import threading
from unittest.mock import patch

from mocklimit.ratelimit import CompositeLimit, FixedWindowLimiter


class TestPassesBoth:
    """A request within both limits is allowed and consumes budget."""

    def test_allowed_when_under_all_limits(self) -> None:
        """Both RPM and TPM have capacity; the request should pass."""
        with patch("time.time", return_value=1000.0):
            rpm = FixedWindowLimiter(max_requests=10, window_seconds=60.0)
            tpm = FixedWindowLimiter(max_requests=1000, window_seconds=60.0)
            composite = CompositeLimit([("rpm", rpm), ("tpm", tpm)])

            result = composite.check("user-1", costs={"rpm": 1, "tpm": 50})

        assert result.allowed is True
        assert result.denied_by is None
        assert result.per_limit["rpm"].allowed is True
        assert result.per_limit["rpm"].remaining == 9
        assert result.per_limit["tpm"].allowed is True
        assert result.per_limit["tpm"].remaining == 950


class TestFailsTPMNotRPM:
    """When TPM denies, RPM must not be consumed."""

    def test_rpm_counter_unchanged_on_tpm_denial(self) -> None:
        """Pre-fill TPM near capacity, then exceed it; RPM stays untouched."""
        with patch("time.time", return_value=1000.0):
            rpm = FixedWindowLimiter(max_requests=10, window_seconds=60.0)
            tpm = FixedWindowLimiter(max_requests=1000, window_seconds=60.0)

            tpm.check("user-1", cost=990)
            rpm_before = rpm.peek("user-1", cost=0)

            composite = CompositeLimit([("rpm", rpm), ("tpm", tpm)])
            result = composite.check("user-1", costs={"rpm": 1, "tpm": 50})

        assert result.allowed is False
        assert result.denied_by == "tpm"
        assert result.per_limit["tpm"].allowed is False

        with patch("time.time", return_value=1000.0):
            rpm_after = rpm.peek("user-1", cost=0)

        assert rpm_after.remaining == rpm_before.remaining


class TestFailsBoth:
    """When both limits are exceeded, the first limiter in order is reported."""

    def test_denied_by_first_limiter_when_both_exceeded(self) -> None:
        """Exhaust both; denied_by should be the first limiter listed."""
        with patch("time.time", return_value=1000.0):
            rpm = FixedWindowLimiter(max_requests=10, window_seconds=60.0)
            tpm = FixedWindowLimiter(max_requests=1000, window_seconds=60.0)

            rpm.check("user-1", cost=10)
            tpm.check("user-1", cost=1000)

            composite = CompositeLimit([("rpm", rpm), ("tpm", tpm)])
            result = composite.check("user-1", costs={"rpm": 1, "tpm": 1})

        assert result.allowed is False
        assert result.denied_by == "rpm"
        assert result.per_limit["rpm"].allowed is False
        assert result.per_limit["tpm"].allowed is False


class TestAtomicConcurrency:
    """Per-key locking prevents partial consumption under contention."""

    def test_no_overconsumption_under_contention(self) -> None:
        """Fire 20 threads at RPM=10; exactly 10 succeed, counter never overflows."""
        with patch("time.time", return_value=1000.0):
            rpm = FixedWindowLimiter(max_requests=10, window_seconds=60.0)
            tpm = FixedWindowLimiter(max_requests=10000, window_seconds=60.0)
            composite = CompositeLimit([("rpm", rpm), ("tpm", tpm)])

            num_threads = 20
            barrier = threading.Barrier(num_threads)
            results: list[bool] = []
            results_lock = threading.Lock()

            def worker() -> None:
                barrier.wait()
                r = composite.check("user-1", costs={"rpm": 1, "tpm": 1})
                with results_lock:
                    results.append(r.allowed)

            threads = [threading.Thread(target=worker) for _ in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            allowed_count = sum(results)
            denied_count = len(results) - allowed_count

            assert allowed_count == 10
            assert denied_count == 10

            rpm_state = rpm.peek("user-1", cost=0)

        assert rpm_state.remaining == 0
