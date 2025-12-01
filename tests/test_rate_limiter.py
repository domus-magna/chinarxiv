"""Tests for figure pipeline rate limiter."""
from __future__ import annotations

import os
import threading
import time
from unittest import mock

import pytest

from src.figure_pipeline.rate_limiter import (
    AdaptiveRateLimiter,
    RateLimiterConfig,
    get_rate_limiter,
    is_rate_limit_error,
)


class TestIsRateLimitError:
    """Tests for is_rate_limit_error function."""

    def test_429_is_rate_limit(self):
        """Verify 429 status code is detected as rate limit."""
        assert is_rate_limit_error(429, "Rate limit exceeded") is True

    def test_503_is_rate_limit(self):
        """Verify 503 status code is detected as rate limit (service unavailable)."""
        assert is_rate_limit_error(503, "Service temporarily unavailable") is True

    def test_429_with_quota_is_not_rate_limit(self):
        """Verify 429 with quota message goes to circuit breaker, not rate limiter."""
        assert is_rate_limit_error(429, "quota exceeded") is False
        assert is_rate_limit_error(429, "billing limit reached") is False

    def test_400_is_not_rate_limit(self):
        """Verify 400 Bad Request is not a rate limit error."""
        assert is_rate_limit_error(400, "Bad request") is False

    def test_500_is_not_rate_limit(self):
        """Verify generic 500 errors are not rate limits."""
        assert is_rate_limit_error(500, "Internal server error") is False

    def test_none_status_code_with_rate_limit_text(self):
        """Verify text-based detection works when status_code is None."""
        assert is_rate_limit_error(None, "rate limit exceeded") is True
        assert is_rate_limit_error(None, "too many requests") is True

    def test_none_status_code_with_quota_text(self):
        """Verify quota errors are not detected as rate limits even from text."""
        assert is_rate_limit_error(None, "rate limit: quota exhausted") is False

    def test_none_status_code_with_unrelated_text(self):
        """Verify unrelated errors are not detected as rate limits."""
        assert is_rate_limit_error(None, "Connection refused") is False
        assert is_rate_limit_error(None, "Invalid API key") is False


class TestAdaptiveRateLimiter:
    """Tests for AdaptiveRateLimiter class."""

    def test_initial_concurrency(self):
        """Verify initial concurrency matches config."""
        config = RateLimiterConfig(initial_concurrent=4)
        limiter = AdaptiveRateLimiter(config)
        assert limiter.get_concurrent() == 4

    def test_on_rate_limit_halves_concurrency(self):
        """Verify on_rate_limit() halves the concurrency."""
        config = RateLimiterConfig(initial_concurrent=8, backoff_factor=0.5)
        limiter = AdaptiveRateLimiter(config)

        assert limiter.get_concurrent() == 8
        limiter.on_rate_limit("test 429")
        assert limiter.get_concurrent() == 4
        limiter.on_rate_limit("test 429")
        assert limiter.get_concurrent() == 2

    def test_concurrency_respects_minimum(self):
        """Verify concurrency never goes below minimum."""
        config = RateLimiterConfig(
            initial_concurrent=2,
            min_concurrent=1,
            backoff_factor=0.5,
        )
        limiter = AdaptiveRateLimiter(config)

        limiter.on_rate_limit("test")
        assert limiter.get_concurrent() == 1
        limiter.on_rate_limit("test")  # Should not go below 1
        assert limiter.get_concurrent() == 1

    def test_acquire_blocks_at_limit(self):
        """Verify acquire() blocks when at current limit."""
        config = RateLimiterConfig(initial_concurrent=2)
        limiter = AdaptiveRateLimiter(config)

        results = []
        acquired_count = [0]
        lock = threading.Lock()

        def acquire_and_hold(duration: float, worker_id: int):
            with limiter.acquire():
                with lock:
                    acquired_count[0] += 1
                    results.append(f"acquired:{worker_id}")
                time.sleep(duration)
                with lock:
                    results.append(f"released:{worker_id}")

        # Start 3 workers, but only 2 should be able to acquire at once
        threads = [
            threading.Thread(target=acquire_and_hold, args=(0.2, i))
            for i in range(3)
        ]
        for t in threads:
            t.start()

        # Wait a bit for first two to acquire
        time.sleep(0.05)
        with lock:
            # Should have exactly 2 acquired (third blocked)
            assert acquired_count[0] == 2

        # Wait for all to complete
        for t in threads:
            t.join(timeout=2.0)

        # All should have completed
        assert acquired_count[0] == 3

    def test_backoff_reduces_effective_concurrency(self):
        """Verify on_rate_limit() reduces what acquire() allows."""
        config = RateLimiterConfig(initial_concurrent=4)
        limiter = AdaptiveRateLimiter(config)

        # Simulate rate limit reducing concurrency to 2
        limiter.on_rate_limit("test 429")
        assert limiter.get_concurrent() == 2

        # Now verify only 2 can acquire at once
        acquired_count = [0]
        lock = threading.Lock()

        def acquire_and_hold(duration: float):
            with limiter.acquire():
                with lock:
                    acquired_count[0] += 1
                time.sleep(duration)

        threads = [
            threading.Thread(target=acquire_and_hold, args=(0.2,))
            for _ in range(4)
        ]
        for t in threads:
            t.start()

        time.sleep(0.05)
        with lock:
            # Should be limited to 2 now (not 4)
            assert acquired_count[0] == 2

        for t in threads:
            t.join(timeout=2.0)

    def test_stats_include_active_count(self):
        """Verify get_stats() includes active request count."""
        config = RateLimiterConfig(initial_concurrent=2)
        limiter = AdaptiveRateLimiter(config)

        stats = limiter.get_stats()
        assert "active_count" in stats
        assert stats["active_count"] == 0

        # Acquire one slot and check
        with limiter.acquire():
            stats = limiter.get_stats()
            assert stats["active_count"] == 1

        stats = limiter.get_stats()
        assert stats["active_count"] == 0

    def test_reset_restores_initial_concurrency(self):
        """Verify reset() restores initial state."""
        config = RateLimiterConfig(initial_concurrent=8)
        limiter = AdaptiveRateLimiter(config)

        limiter.on_rate_limit("test")
        limiter.on_rate_limit("test")
        assert limiter.get_concurrent() == 2

        limiter.reset()
        assert limiter.get_concurrent() == 8


class TestGetRateLimiter:
    """Tests for get_rate_limiter singleton factory."""

    def test_returns_singleton(self):
        """Verify get_rate_limiter returns the same instance."""
        # Reset global state
        import src.figure_pipeline.rate_limiter as rl_module
        rl_module._global_rate_limiter = None

        limiter1 = get_rate_limiter()
        limiter2 = get_rate_limiter()
        assert limiter1 is limiter2

        # Cleanup
        rl_module._global_rate_limiter = None

    def test_respects_figure_concurrent_env_var(self):
        """Verify FIGURE_CONCURRENT env var sets initial concurrency."""
        import src.figure_pipeline.rate_limiter as rl_module
        rl_module._global_rate_limiter = None

        with mock.patch.dict(os.environ, {"FIGURE_CONCURRENT": "3"}):
            limiter = get_rate_limiter()
            assert limiter.get_concurrent() == 3

        # Cleanup
        rl_module._global_rate_limiter = None

    def test_invalid_figure_concurrent_uses_defaults(self):
        """Verify invalid FIGURE_CONCURRENT falls back to defaults."""
        import src.figure_pipeline.rate_limiter as rl_module
        rl_module._global_rate_limiter = None

        with mock.patch.dict(os.environ, {"FIGURE_CONCURRENT": "not_a_number"}):
            limiter = get_rate_limiter()
            # Should use default (8)
            assert limiter.get_concurrent() == 8

        # Cleanup
        rl_module._global_rate_limiter = None
