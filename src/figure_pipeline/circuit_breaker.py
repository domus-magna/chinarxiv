"""
Circuit breaker for fail-fast error handling in figure pipeline.

Detects billing/quota errors and trips to prevent infinite retries on:
- Google API (Gemini) - figure translation
- Moondream - figure QA validation

When tripped, the pipeline stops immediately instead of wasting resources.
"""
from __future__ import annotations

from datetime import datetime
from typing import FrozenSet, Optional

from ..alerts import circuit_tripped as send_circuit_alert


# Error codes that indicate billing/quota issues (non-recoverable)
BILLING_ERROR_CODES: FrozenSet[str] = frozenset({
    "payment_required",
    "insufficient_quota",
    "invalid_api_key",
    "quota_exceeded",
    "billing_required",
    "unauthorized",
    "invalid_credentials",
})


def classify_api_error(
    status_code: Optional[int],
    error_text: str,
) -> Optional[str]:
    """
    Classify API error and return error code if billing-related.

    Works for both HTTP status codes and text-based errors.

    Args:
        status_code: HTTP status code (or None for SDK errors)
        error_text: Error message text

    Returns:
        Error code string if billing-related, None for transient errors
    """
    error_lower = error_text.lower()

    # HTTP status code classification
    if status_code:
        # Payment required
        if status_code == 402:
            return "payment_required"

        # Auth errors
        if status_code == 401:
            return "invalid_api_key"

        # Rate limiting - check if quota-related
        if status_code == 429:
            if "quota" in error_lower or "billing" in error_lower:
                return "quota_exceeded"
            return None  # Regular rate limit is transient

        # Forbidden - check if quota-related
        if status_code == 403:
            if "quota" in error_lower or "billing" in error_lower:
                return "quota_exceeded"
            return "unauthorized"

    # Text-based classification (for SDK errors without status codes)
    if "unauthorized" in error_lower or "401" in error_lower:
        return "invalid_api_key"
    if "invalid" in error_lower and "key" in error_lower:
        return "invalid_api_key"
    if "quota" in error_lower or "limit exceeded" in error_lower:
        return "quota_exceeded"
    if "payment" in error_lower or "402" in error_lower:
        return "payment_required"
    if "billing" in error_lower:
        return "billing_required"
    if "403" in error_lower or "forbidden" in error_lower:
        return "quota_exceeded"

    return None  # Transient error


class FigureCircuitBreaker:
    """
    Circuit breaker for figure pipeline API calls.

    Trips after consecutive billing/quota errors to fail fast.
    Supports Google API (Gemini) and Moondream.
    """

    def __init__(self, threshold: int = 2):
        """
        Initialize circuit breaker.

        Args:
            threshold: Number of consecutive billing errors before tripping
        """
        self.threshold = threshold
        self.is_open = False
        self.consecutive_failures = 0
        self.failure_reason: Optional[str] = None
        self.failed_api: Optional[str] = None
        self.tripped_at: Optional[datetime] = None

    def record_failure(self, error_code: str, api_name: str, details: str = "") -> bool:
        """
        Record a failed API call.

        Args:
            error_code: Error code from the API
            api_name: Name of the API (e.g., "gemini", "moondream")
            details: Additional error details

        Returns:
            True if the circuit breaker tripped as a result
        """
        if error_code.lower() not in BILLING_ERROR_CODES:
            # Transient error - don't count towards threshold
            return False

        self.consecutive_failures += 1
        print(f"[circuit_breaker] Billing error from {api_name}: {error_code} "
              f"({self.consecutive_failures}/{self.threshold})")

        if self.consecutive_failures >= self.threshold:
            self._trip(api_name, error_code, details)
            return True

        return False

    def _trip(self, api_name: str, error_code: str, details: str) -> None:
        """Trip the circuit breaker."""
        self.is_open = True
        self.failed_api = api_name
        self.failure_reason = f"{api_name}: {error_code}"
        self.tripped_at = datetime.now()

        print(f"[circuit_breaker] TRIPPED! {self.failure_reason}")
        if details:
            print(f"[circuit_breaker] Details: {details[:200]}")

        # Send Discord alert if configured
        self._send_alert(api_name, error_code, details)

    def _send_alert(self, api_name: str, error_code: str, details: str) -> None:
        """Send Discord alert about circuit breaker trip using unified alerts."""
        try:
            # Format API name nicely (e.g., "gemini" -> "Gemini")
            formatted_api = api_name.title()
            send_circuit_alert(
                api=f"Figure Pipeline ({formatted_api})",
                error_code=error_code,
                consecutive_count=self.consecutive_failures,
                details=details,
            )
        except Exception as e:
            print(f"[circuit_breaker] Failed to send Discord alert: {e}")

    def check(self) -> None:
        """
        Check circuit breaker state.

        Raises:
            RuntimeError: If circuit breaker is open
        """
        if self.is_open:
            raise RuntimeError(
                f"Circuit breaker open: {self.failure_reason}. "
                f"Pipeline stopped to prevent further API costs."
            )

    def reset(self) -> None:
        """Reset the circuit breaker to closed state."""
        self.is_open = False
        self.consecutive_failures = 0
        self.failure_reason = None
        self.failed_api = None
        self.tripped_at = None
        print("[circuit_breaker] Reset to closed state")


# Singleton instance for shared state across pipeline
_global_circuit_breaker: Optional[FigureCircuitBreaker] = None


def get_circuit_breaker(threshold: int = 2) -> FigureCircuitBreaker:
    """
    Get the global circuit breaker instance.

    Args:
        threshold: Number of consecutive errors before tripping

    Returns:
        Shared FigureCircuitBreaker instance
    """
    global _global_circuit_breaker
    if _global_circuit_breaker is None:
        _global_circuit_breaker = FigureCircuitBreaker(threshold=threshold)
    return _global_circuit_breaker
