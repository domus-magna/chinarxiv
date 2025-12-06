"""
Tests for circuit breaker functionality in TranslationService.
"""

from unittest.mock import patch
import pytest

from src.services.translation_service import TranslationService, CircuitBreakerOpen
from src.services.circuit_breaker import CircuitBreaker
import contextlib


class TestCircuitBreaker:
    """Test cases for circuit breaker in TranslationService."""

    def setup_method(self):
        """Setup test environment."""
        # Create service with custom circuit breaker config
        config = {
            "models": {"default_slug": "deepseek/deepseek-v3.2-exp"},
            "glossary": [],
            "translation": {
                "circuit_breaker": {
                    "persistent_error_threshold": 2,
                    "transient_error_threshold": 5,
                }
            },
        }
        self.service = TranslationService(config=config)

    def test_persistent_error_threshold(self):
        """Test that 2 consecutive persistent errors trigger circuit breaker."""
        # First persistent error
        self.service._record_failure("payment_required")
        assert self.service._circuit_breaker.consecutive_persistent == 1
        assert self.service._circuit_breaker.consecutive_transient == 0
        assert not self.service._circuit_breaker.is_open

        # Second persistent error should trigger breaker
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            self.service._record_failure("insufficient_quota")

        assert "2 consecutive persistent errors" in str(exc_info.value)
        assert self.service._circuit_breaker.is_open

    def test_transient_error_threshold(self):
        """Test that 5 consecutive transient errors trigger circuit breaker."""
        # First 4 transient errors
        for _i in range(4):
            self.service._record_failure("network_error")
            assert not self.service._circuit_breaker.is_open

        assert self.service._circuit_breaker.consecutive_transient == 4
        assert self.service._circuit_breaker.consecutive_persistent == 0

        # Fifth transient error should trigger breaker
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            self.service._record_failure("http_500")

        assert "5 consecutive transient errors" in str(exc_info.value)
        assert self.service._circuit_breaker.is_open

    def test_error_classification_persistent(self):
        """Test that persistent error codes are correctly classified."""
        persistent_codes = [
            "payment_required",
            "insufficient_quota",
            "invalid_api_key",
            "invalid_credentials",
            "unauthorized",
        ]

        for code in persistent_codes:
            service = TranslationService(config=self.service.config)
            service._record_failure(code)
            assert (
                service._circuit_breaker.consecutive_persistent == 1
            ), f"{code} should be classified as persistent"
            assert service._circuit_breaker.consecutive_transient == 0

    def test_error_classification_transient(self):
        """Test that non-persistent error codes are classified as transient."""
        transient_codes = [
            "network_error",
            "http_500",
            "http_503",
            "rate_limit_exceeded",
            "timeout",
            None,  # Unknown errors are transient
            "some_random_error",
        ]

        for code in transient_codes:
            service = TranslationService(config=self.service.config)
            service._record_failure(code)
            assert (
                service._circuit_breaker.consecutive_transient == 1
            ), f"{code} should be classified as transient"
            assert service._circuit_breaker.consecutive_persistent == 0

    def test_counter_reset_on_success(self):
        """Test that success resets both counters."""
        # Accumulate some failures
        self.service._record_failure("payment_required")
        self.service._record_failure("network_error")

        assert (
            self.service._circuit_breaker.consecutive_persistent == 0
        )  # Reset when switching to transient
        assert self.service._circuit_breaker.consecutive_transient == 1

        # Reset counters
        self.service._on_api_success()

        assert self.service._circuit_breaker.consecutive_persistent == 0
        assert self.service._circuit_breaker.consecutive_transient == 0
        assert not self.service._circuit_breaker.is_open

    def test_counter_reset_when_switching_error_types(self):
        """Test that counters reset when switching between error types."""
        # 1 persistent error
        self.service._record_failure("payment_required")
        assert self.service._circuit_breaker.consecutive_persistent == 1
        assert self.service._circuit_breaker.consecutive_transient == 0

        # Switch to transient error - should reset persistent counter
        self.service._record_failure("network_error")
        assert self.service._circuit_breaker.consecutive_persistent == 0
        assert self.service._circuit_breaker.consecutive_transient == 1

        # Switch back to persistent - should reset transient counter
        self.service._record_failure("insufficient_quota")
        assert self.service._circuit_breaker.consecutive_persistent == 1
        assert self.service._circuit_breaker.consecutive_transient == 0

    def test_alternating_errors_no_trigger(self):
        """Test that alternating error types never trigger breaker."""
        # This pattern should never trigger: P, T, T, T, T, P, T, T
        error_sequence = [
            ("payment_required", "persistent"),
            ("network_error", "transient"),
            ("network_error", "transient"),
            ("network_error", "transient"),
            ("network_error", "transient"),
            ("insufficient_quota", "persistent"),  # Resets transient
            ("network_error", "transient"),
            ("network_error", "transient"),
        ]

        for code, _ in error_sequence:
            self.service._record_failure(code)

        # Should never trigger because we never had 2 consecutive persistent or 5 consecutive transient
        assert not self.service._circuit_breaker.is_open
        assert self.service._circuit_breaker.consecutive_transient == 2
        assert self.service._circuit_breaker.consecutive_persistent == 0

    def test_circuit_breaker_stays_open(self):
        """Test that once circuit breaker opens, it stays open."""
        # Trigger the breaker
        self.service._record_failure("payment_required")
        with pytest.raises(CircuitBreakerOpen):
            self.service._record_failure("payment_required")

        assert self.service._circuit_breaker.is_open

        # Check circuit breaker before attempting operations
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            self.service._check_circuit_breaker()

        assert "Circuit breaker triggered" in str(exc_info.value)

    def test_circuit_breaker_state_persists_across_calls(self):
        """Test that circuit breaker state persists across method calls."""
        # Build up to threshold - 1
        self.service._record_failure("payment_required")

        # Trigger it
        with pytest.raises(CircuitBreakerOpen):
            self.service._record_failure("payment_required")

        # Try to make another call - should fail immediately
        with pytest.raises(CircuitBreakerOpen):
            self.service._check_circuit_breaker()

    @patch("src.services.circuit_breaker.circuit_tripped")
    def test_alert_sent_on_persistent_threshold(self, mock_alert):
        """Test that alert is sent when persistent error threshold is reached."""
        # Trigger persistent error threshold
        self.service._record_failure("payment_required")

        with contextlib.suppress(CircuitBreakerOpen):
            self.service._record_failure("insufficient_quota")

        # Alert should have been called
        mock_alert.assert_called_once()
        call_kwargs = mock_alert.call_args[1]
        assert call_kwargs["consecutive_count"] == 2
        assert "insufficient_quota" in call_kwargs["error_code"]

    @patch("src.services.circuit_breaker.circuit_tripped")
    def test_alert_sent_on_transient_threshold(self, mock_alert):
        """Test that alert is sent when transient error threshold is reached."""
        # Trigger transient error threshold
        for _i in range(4):
            self.service._record_failure("network_error")

        with contextlib.suppress(CircuitBreakerOpen):
            self.service._record_failure("network_error")

        # Alert should have been called
        mock_alert.assert_called_once()
        call_kwargs = mock_alert.call_args[1]
        assert call_kwargs["consecutive_count"] == 5

    @patch("src.services.circuit_breaker.circuit_tripped")
    @patch("src.services.circuit_breaker.log")
    def test_alert_failure_is_logged(self, mock_log, mock_alert):
        """Test that if alert fails, the error is logged."""
        # Make alert raise an exception
        mock_alert.side_effect = Exception("Alert service unavailable")

        # Trigger circuit breaker
        self.service._record_failure("payment_required")

        with contextlib.suppress(CircuitBreakerOpen):
            self.service._record_failure("payment_required")

        # Check that log was called with the alert failure
        log_calls = [str(call) for call in mock_log.call_args_list]
        assert any(
            "Failed to send circuit breaker alert" in str(call) for call in log_calls
        )

    def test_config_defaults(self):
        """Test that circuit breaker works with default config values."""
        # Create service without circuit breaker config
        service = TranslationService(config={"models": {}, "glossary": []})

        # Should use defaults: 2 for persistent, 5 for transient
        assert service._circuit_breaker.persistent_threshold == 2
        assert service._circuit_breaker.transient_threshold == 5

    def test_custom_thresholds(self):
        """Test that custom thresholds from config are respected."""
        config = {
            "models": {},
            "glossary": [],
            "translation": {
                "circuit_breaker": {
                    "persistent_error_threshold": 3,
                    "transient_error_threshold": 10,
                }
            },
        }
        service = TranslationService(config=config)

        assert service._circuit_breaker.persistent_threshold == 3
        assert service._circuit_breaker.transient_threshold == 10

        # Should take 3 persistent errors to trigger
        service._record_failure("payment_required")
        service._record_failure("payment_required")
        # Third should trigger
        with pytest.raises(CircuitBreakerOpen):
            service._record_failure("payment_required")


class TestCircuitBreakerClass:
    """Tests for the standalone CircuitBreaker class."""

    def test_init_defaults(self):
        """CircuitBreaker initializes with correct defaults."""
        cb = CircuitBreaker()

        assert cb.persistent_threshold == 2
        assert cb.transient_threshold == 5
        assert cb.consecutive_persistent == 0
        assert cb.consecutive_transient == 0
        assert not cb.is_open

    def test_init_custom_thresholds(self):
        """CircuitBreaker accepts custom thresholds."""
        cb = CircuitBreaker(persistent_threshold=3, transient_threshold=10)

        assert cb.persistent_threshold == 3
        assert cb.transient_threshold == 10

    def test_init_custom_persistent_codes(self):
        """CircuitBreaker accepts custom persistent codes."""
        custom_codes = {"custom_error", "another_error"}
        cb = CircuitBreaker(persistent_codes=custom_codes)

        assert cb.persistent_codes == custom_codes

    def test_check_when_closed(self):
        """Check does not raise when circuit is closed."""
        cb = CircuitBreaker()
        cb.check()  # Should not raise

    def test_check_when_open(self):
        """Check raises CircuitBreakerOpen when circuit is open."""
        cb = CircuitBreaker()
        cb._circuit_open = True

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_record_failure_persistent(self):
        """Persistent errors increment persistent counter."""
        cb = CircuitBreaker(persistent_threshold=3)

        cb.record_failure("payment_required")
        assert cb.consecutive_persistent == 1
        assert cb.consecutive_transient == 0
        assert not cb.is_open

    def test_record_failure_transient(self):
        """Transient errors increment transient counter."""
        cb = CircuitBreaker(transient_threshold=10)

        cb.record_failure("network_error")
        assert cb.consecutive_transient == 1
        assert cb.consecutive_persistent == 0
        assert not cb.is_open

    def test_persistent_trips_at_threshold(self):
        """Persistent errors trip circuit at threshold."""
        cb = CircuitBreaker(persistent_threshold=2)

        cb.record_failure("payment_required")
        with pytest.raises(CircuitBreakerOpen):
            cb.record_failure("payment_required")

        assert cb.is_open

    def test_transient_trips_at_threshold(self):
        """Transient errors trip circuit at threshold."""
        cb = CircuitBreaker(transient_threshold=3)

        cb.record_failure("rate_limit")
        cb.record_failure("rate_limit")
        with pytest.raises(CircuitBreakerOpen):
            cb.record_failure("rate_limit")

        assert cb.is_open

    def test_record_success_resets_counters(self):
        """record_success resets all counters."""
        cb = CircuitBreaker()
        cb._consecutive_persistent = 1
        cb._consecutive_transient = 3
        cb._circuit_open = True

        cb.record_success()

        assert cb.consecutive_persistent == 0
        assert cb.consecutive_transient == 0
        assert not cb.is_open

    def test_reset_clears_state(self):
        """reset() clears all state."""
        cb = CircuitBreaker()
        cb._consecutive_persistent = 1
        cb._consecutive_transient = 3
        cb._circuit_open = True

        cb.reset()

        assert cb.consecutive_persistent == 0
        assert cb.consecutive_transient == 0
        assert not cb.is_open

    def test_all_default_persistent_codes(self):
        """All default persistent codes are recognized."""
        cb = CircuitBreaker(persistent_threshold=10)

        for code in CircuitBreaker.DEFAULT_PERSISTENT_CODES:
            cb.reset()
            cb.record_failure(code)
            assert cb.consecutive_persistent == 1, f"Code {code} should be persistent"
            assert cb.consecutive_transient == 0

    def test_zero_persistent_threshold_rejected(self):
        """Zero persistent threshold raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CircuitBreaker(persistent_threshold=0)

        assert "persistent_threshold must be >= 1" in str(exc_info.value)

    def test_negative_persistent_threshold_rejected(self):
        """Negative persistent threshold raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CircuitBreaker(persistent_threshold=-1)

        assert "persistent_threshold must be >= 1" in str(exc_info.value)

    def test_zero_transient_threshold_rejected(self):
        """Zero transient threshold raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CircuitBreaker(transient_threshold=0)

        assert "transient_threshold must be >= 1" in str(exc_info.value)

    def test_negative_transient_threshold_rejected(self):
        """Negative transient threshold raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            CircuitBreaker(transient_threshold=-5)

        assert "transient_threshold must be >= 1" in str(exc_info.value)
