"""
Adaptive rate limiter for figure translation API calls.

Handles 429 rate limit errors by dynamically adjusting concurrency:
- Backs off (reduces concurrency) on 429 errors
- Speeds up (increases concurrency) on sustained success
- Works with ThreadPoolExecutor-based parallel translation

This is separate from the circuit breaker, which handles billing/quota errors.
The rate limiter handles transient rate limits that can be recovered from.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RateLimiterConfig:
    """Configuration for adaptive rate limiter."""

    # Concurrency bounds
    initial_concurrent: int = 8
    min_concurrent: int = 1
    max_concurrent: int = 16

    # Adjustment factors
    backoff_factor: float = 0.5  # Halve on rate limit
    speedup_increment: float = 0.5  # Add 0.5 per success window

    # Success tracking
    success_window: int = 10  # Successes before considering speedup

    # Cooldown after rate limit
    cooldown_seconds: float = 5.0


class AdaptiveRateLimiter:
    """
    Adaptive rate limiter that adjusts concurrency based on API responses.

    Thread-safe for use with ThreadPoolExecutor.

    Usage:
        limiter = AdaptiveRateLimiter()

        # Get current concurrency for ThreadPoolExecutor
        max_workers = limiter.get_concurrent()

        # On successful API call
        limiter.on_success()

        # On 429 rate limit error
        delay = limiter.on_rate_limit()
        time.sleep(delay)
    """

    def __init__(self, config: Optional[RateLimiterConfig] = None):
        """Initialize rate limiter with configuration."""
        self.config = config or RateLimiterConfig()

        # Current concurrency level (float for gradual adjustment)
        self._current: float = float(self.config.initial_concurrent)

        # Success counter for speedup
        self._success_count: int = 0

        # Last rate limit time for cooldown
        self._last_rate_limit: float = 0.0

        # Thread safety
        self._lock = threading.Lock()

        # Stats for monitoring
        self._total_successes: int = 0
        self._total_rate_limits: int = 0

    def get_concurrent(self) -> int:
        """
        Get current concurrency level for ThreadPoolExecutor.

        Returns:
            Integer concurrency level (at least 1)
        """
        with self._lock:
            return max(1, int(self._current))

    def on_success(self) -> None:
        """
        Record a successful API call.

        Gradually increases concurrency if sustained success.
        """
        with self._lock:
            self._total_successes += 1
            self._success_count += 1

            # Check if we've had enough successes to speed up
            if self._success_count >= self.config.success_window:
                # Only speed up if we're below max and past cooldown
                time_since_limit = time.time() - self._last_rate_limit
                if (self._current < self.config.max_concurrent and
                    time_since_limit > self.config.cooldown_seconds * 2):

                    old_concurrent = self._current
                    self._current = min(
                        self._current + self.config.speedup_increment,
                        self.config.max_concurrent,
                    )

                    if self._current != old_concurrent:
                        print(f"[rate_limiter] Speeding up: {old_concurrent:.1f} -> {self._current:.1f} concurrent")

                # Reset success counter
                self._success_count = 0

    def on_rate_limit(self, error_message: str = "") -> float:
        """
        Record a 429 rate limit error.

        Immediately reduces concurrency and returns recommended delay.

        Args:
            error_message: Error message from API (for logging)

        Returns:
            Recommended delay in seconds before retrying
        """
        with self._lock:
            self._total_rate_limits += 1
            self._last_rate_limit = time.time()
            self._success_count = 0  # Reset success counter

            old_concurrent = self._current
            self._current = max(
                self._current * self.config.backoff_factor,
                self.config.min_concurrent,
            )

            print(f"[rate_limiter] Rate limit hit! Backing off: {old_concurrent:.1f} -> {self._current:.1f} concurrent")
            if error_message:
                print(f"[rate_limiter] Error: {error_message[:100]}")

            return self.config.cooldown_seconds

    def get_stats(self) -> dict:
        """
        Get rate limiter statistics.

        Returns:
            Dictionary with current state and totals
        """
        with self._lock:
            return {
                "current_concurrent": int(self._current),
                "current_concurrent_exact": self._current,
                "total_successes": self._total_successes,
                "total_rate_limits": self._total_rate_limits,
                "success_streak": self._success_count,
                "time_since_last_limit": time.time() - self._last_rate_limit if self._last_rate_limit else None,
            }

    def reset(self) -> None:
        """Reset rate limiter to initial state."""
        with self._lock:
            self._current = float(self.config.initial_concurrent)
            self._success_count = 0
            self._last_rate_limit = 0.0
            self._total_successes = 0
            self._total_rate_limits = 0
            print(f"[rate_limiter] Reset to {self._current:.0f} concurrent")


# Global instance for shared state across pipeline
_global_rate_limiter: Optional[AdaptiveRateLimiter] = None


def get_rate_limiter(config: Optional[RateLimiterConfig] = None) -> AdaptiveRateLimiter:
    """
    Get the global rate limiter instance.

    Args:
        config: Optional configuration (only used on first call)

    Returns:
        Shared AdaptiveRateLimiter instance
    """
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = AdaptiveRateLimiter(config)
    return _global_rate_limiter


def is_rate_limit_error(status_code: Optional[int], error_text: str) -> bool:
    """
    Check if an error is a transient rate limit (not quota exhaustion).

    Args:
        status_code: HTTP status code
        error_text: Error message

    Returns:
        True if this is a transient 429 that should trigger backoff
    """
    error_lower = error_text.lower()

    # 429 is rate limit, but check if it's quota-related
    if status_code == 429:
        # Quota exhaustion should go to circuit breaker, not rate limiter
        if "quota" in error_lower or "billing" in error_lower:
            return False
        return True

    # Some APIs return 503 for temporary overload
    if status_code == 503:
        return True

    # Text-based detection
    if "rate limit" in error_lower or "too many requests" in error_lower:
        if "quota" not in error_lower and "billing" not in error_lower:
            return True

    return False
