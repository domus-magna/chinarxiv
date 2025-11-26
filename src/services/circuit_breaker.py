"""
Circuit breaker pattern for handling consecutive failures.

Prevents cascading failures by "opening" the circuit after a threshold
of consecutive errors, blocking further requests until manually reset
or after successful operations.
"""

from typing import FrozenSet, Optional, Set

from ..logging_utils import log
from ..monitoring import alert_critical


class CircuitBreakerOpen(Exception):
    """Exception raised when circuit breaker is open (tripped)."""

    pass


class CircuitBreaker:
    """
    Circuit breaker for tracking consecutive failures.

    Distinguishes between:
    - Persistent errors (payment/auth): Trip quickly (default: 2 failures)
    - Transient errors (network/5xx): Trip after more failures (default: 5)

    Thread Safety:
        This class is NOT thread-safe. Each instance should only be used
        from a single thread. For multi-threaded contexts, create separate
        instances per thread or use external synchronization.

    Example:
        >>> cb = CircuitBreaker(persistent_threshold=2, transient_threshold=5)
        >>> cb.record_failure("network_error")  # Transient
        >>> cb.record_failure("payment_required")  # Persistent - resets transient
        >>> cb.record_success()  # Resets all counters
    """

    # Default error codes considered "persistent" (payment/auth issues)
    # Using frozenset to prevent accidental mutation of shared default
    DEFAULT_PERSISTENT_CODES: FrozenSet[str] = frozenset({
        "payment_required",
        "insufficient_quota",
        "invalid_api_key",
        "invalid_credentials",
        "unauthorized",
    })

    def __init__(
        self,
        persistent_threshold: int = 2,
        transient_threshold: int = 5,
        persistent_codes: Optional[Set[str]] = None,
        source_name: str = "circuit_breaker",
    ):
        """
        Initialize circuit breaker.

        Args:
            persistent_threshold: Failures before trip on persistent errors
            transient_threshold: Failures before trip on transient errors
            persistent_codes: Error codes to treat as persistent (default: payment/auth)
            source_name: Name for alert source attribution
        """
        self._consecutive_persistent = 0
        self._consecutive_transient = 0
        self._circuit_open = False

        self.persistent_threshold = persistent_threshold
        self.transient_threshold = transient_threshold
        self.persistent_codes = persistent_codes or self.DEFAULT_PERSISTENT_CODES
        self.source_name = source_name

    @property
    def is_open(self) -> bool:
        """Check if circuit is currently open (tripped)."""
        return self._circuit_open

    @property
    def consecutive_persistent(self) -> int:
        """Current count of consecutive persistent errors."""
        return self._consecutive_persistent

    @property
    def consecutive_transient(self) -> int:
        """Current count of consecutive transient errors."""
        return self._consecutive_transient

    def check(self) -> None:
        """
        Check if circuit breaker is open.

        Raises:
            CircuitBreakerOpen: If circuit breaker is triggered
        """
        if self._circuit_open:
            raise CircuitBreakerOpen(
                f"Circuit breaker triggered after consecutive failures: "
                f"{self._consecutive_persistent} persistent, {self._consecutive_transient} transient"
            )

    def record_failure(self, error_code: Optional[str]) -> None:
        """
        Record an API failure and check circuit breaker thresholds.

        Persistent errors trigger after persistent_threshold consecutive failures.
        Transient errors trigger after transient_threshold consecutive failures.

        Args:
            error_code: Error code from the API

        Raises:
            CircuitBreakerOpen: If threshold exceeded
        """
        if error_code in self.persistent_codes:
            # Persistent error - reset transient counter
            self._consecutive_transient = 0
            self._consecutive_persistent += 1

            if self._consecutive_persistent >= self.persistent_threshold:
                self._circuit_open = True
                self._send_alert(
                    "Circuit Breaker: Persistent Error Threshold Reached",
                    f"Stopped after {self._consecutive_persistent} consecutive {error_code} errors",
                    error_code,
                )
                raise CircuitBreakerOpen(
                    f"Circuit breaker triggered: {self._consecutive_persistent} consecutive "
                    f"persistent errors (threshold: {self.persistent_threshold}). "
                    f"Last error: {error_code}"
                )
        else:
            # Transient error - reset persistent counter
            self._consecutive_persistent = 0
            self._consecutive_transient += 1

            if self._consecutive_transient >= self.transient_threshold:
                self._circuit_open = True
                self._send_alert(
                    "Circuit Breaker: Transient Error Threshold Reached",
                    f"Stopped after {self._consecutive_transient} consecutive transient errors",
                    error_code,
                )
                raise CircuitBreakerOpen(
                    f"Circuit breaker triggered: {self._consecutive_transient} consecutive "
                    f"transient errors (threshold: {self.transient_threshold}). "
                    f"Last error: {error_code or 'unknown'}"
                )

    def record_success(self) -> None:
        """Reset circuit breaker counters on successful operation."""
        self._consecutive_persistent = 0
        self._consecutive_transient = 0
        if self._circuit_open:
            log(f"Circuit breaker closed after successful {self.source_name} call")
        self._circuit_open = False

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._consecutive_persistent = 0
        self._consecutive_transient = 0
        self._circuit_open = False

    def _send_alert(self, title: str, message: str, error_code: Optional[str]) -> None:
        """Send critical alert (best effort, won't raise)."""
        try:
            alert_critical(
                title,
                message,
                source=self.source_name,
                metadata={"error_code": error_code or "unknown"},
            )
        except Exception as alert_err:
            log(f"Failed to send circuit breaker alert: {alert_err}")
