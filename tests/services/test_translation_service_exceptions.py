"""
Tests for exception handling in translation_service.py.

Covers:
- Circuit breaker behavior (persistent vs transient errors)
- OpenRouter API error handling hierarchy
- Translation validation errors
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.services.translation_service import (
    CircuitBreakerOpen,
    OpenRouterError,
    OpenRouterFatalError,
    OpenRouterRetryableError,
    TranslationService,
    TranslationValidationError,
)


class TestCircuitBreaker:
    """Tests for circuit breaker behavior."""

    def test_check_circuit_breaker_when_open(self):
        """Circuit breaker raises when already open."""
        service = TranslationService()
        service._circuit_breaker._circuit_open = True

        with pytest.raises(CircuitBreakerOpen) as exc_info:
            service._check_circuit_breaker()

        assert "Circuit breaker triggered" in str(exc_info.value)

    def test_check_circuit_breaker_when_closed(self):
        """Circuit breaker does not raise when closed."""
        service = TranslationService()
        service._circuit_breaker._circuit_open = False

        # Should not raise
        service._check_circuit_breaker()

    def test_persistent_error_trips_after_threshold(self):
        """Persistent errors trip circuit after 2 consecutive failures."""
        service = TranslationService()
        service._circuit_breaker.persistent_threshold = 2

        # First persistent error - should not trip
        service._record_failure("payment_required")
        assert service._circuit_breaker.consecutive_persistent == 1
        assert not service._circuit_breaker.is_open

        # Second persistent error - should trip
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            service._record_failure("payment_required")

        assert "persistent errors" in str(exc_info.value)
        assert service._circuit_breaker.is_open

    def test_transient_error_trips_after_threshold(self):
        """Transient errors trip circuit after 5 consecutive failures."""
        service = TranslationService()
        service._circuit_breaker.transient_threshold = 5

        # First 4 transient errors - should not trip
        for i in range(4):
            service._record_failure("rate_limit")
            assert service._circuit_breaker.consecutive_transient == i + 1
            assert not service._circuit_breaker.is_open

        # 5th transient error - should trip
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            service._record_failure("rate_limit")

        assert "transient errors" in str(exc_info.value)
        assert service._circuit_breaker.is_open

    def test_persistent_error_resets_transient_counter(self):
        """Persistent error resets transient counter."""
        service = TranslationService()

        # Build up transient errors
        service._record_failure("network_error")
        service._record_failure("network_error")
        assert service._circuit_breaker.consecutive_transient == 2

        # Persistent error should reset transient counter
        service._record_failure("payment_required")
        assert service._circuit_breaker.consecutive_transient == 0
        assert service._circuit_breaker.consecutive_persistent == 1

    def test_transient_error_resets_persistent_counter(self):
        """Transient error resets persistent counter."""
        service = TranslationService()

        # Build up persistent error
        service._record_failure("payment_required")
        assert service._circuit_breaker.consecutive_persistent == 1

        # Transient error should reset persistent counter
        service._record_failure("network_error")
        assert service._circuit_breaker.consecutive_persistent == 0
        assert service._circuit_breaker.consecutive_transient == 1

    def test_on_api_success_clears_all(self):
        """API success clears all counters and closes circuit."""
        service = TranslationService()
        service._circuit_breaker._consecutive_persistent = 1
        service._circuit_breaker._consecutive_transient = 3
        service._circuit_breaker._circuit_open = True

        service._on_api_success()

        assert service._circuit_breaker.consecutive_persistent == 0
        assert service._circuit_breaker.consecutive_transient == 0
        assert not service._circuit_breaker.is_open

    def test_persistent_error_codes_recognized(self):
        """All persistent error codes are recognized."""
        service = TranslationService()

        persistent_codes = [
            "payment_required",
            "insufficient_quota",
            "invalid_api_key",
            "invalid_credentials",
            "unauthorized",
        ]

        for code in persistent_codes:
            service._circuit_breaker.reset()
            service._record_failure(code)
            assert (
                service._circuit_breaker.consecutive_persistent == 1
            ), f"Code {code} should be persistent"
            assert service._circuit_breaker.consecutive_transient == 0


class TestOpenRouterRequest:
    """Tests for OpenRouter API request error handling."""

    @patch("src.services.translation_service.requests.post")
    @patch("src.services.translation_service.openrouter_headers")
    @patch("src.services.translation_service.get_proxies")
    @patch("src.services.translation_service.monitoring_service")
    def test_network_error_raises_retryable(
        self, mock_monitoring, mock_proxies, mock_headers, mock_post
    ):
        """Network errors raise OpenRouterRetryableError."""
        mock_proxies.return_value = ({}, "none")
        mock_headers.return_value = {"Authorization": "Bearer test"}
        mock_post.side_effect = requests.RequestException("Connection failed")

        service = TranslationService()
        payload = {"model": "test", "messages": []}

        with pytest.raises(OpenRouterRetryableError) as exc_info:
            service._execute_openrouter_request(payload, "test-model")

        assert "Network error" in str(exc_info.value)
        assert exc_info.value.code == "network_error"

    @patch("src.services.translation_service.requests.post")
    @patch("src.services.translation_service.openrouter_headers")
    @patch("src.services.translation_service.get_proxies")
    @patch("src.services.translation_service.parse_openrouter_error")
    @patch("src.services.translation_service.monitoring_service")
    def test_retryable_api_error_raises_retryable(
        self, mock_monitoring, mock_parse, mock_proxies, mock_headers, mock_post
    ):
        """Retryable API errors raise OpenRouterRetryableError."""
        mock_proxies.return_value = ({}, "none")
        mock_headers.return_value = {"Authorization": "Bearer test"}

        mock_response = MagicMock()
        mock_response.ok = False
        mock_post.return_value = mock_response

        mock_parse.return_value = {
            "status": 429,
            "code": "rate_limit",
            "message": "Rate limit exceeded",
            "retryable": True,
            "fallback_ok": True,
        }

        service = TranslationService()
        payload = {"model": "test", "messages": []}

        with pytest.raises(OpenRouterRetryableError) as exc_info:
            service._execute_openrouter_request(payload, "test-model")

        assert exc_info.value.code == "rate_limit"

    @patch("src.services.translation_service.requests.post")
    @patch("src.services.translation_service.openrouter_headers")
    @patch("src.services.translation_service.get_proxies")
    @patch("src.services.translation_service.parse_openrouter_error")
    @patch("src.services.translation_service.monitoring_service")
    @patch("src.services.translation_service.api_error")
    def test_fatal_api_error_raises_fatal(
        self,
        mock_api_error,
        mock_monitoring,
        mock_parse,
        mock_proxies,
        mock_headers,
        mock_post,
    ):
        """Fatal API errors (no fallback) raise OpenRouterFatalError."""
        mock_proxies.return_value = ({}, "none")
        mock_headers.return_value = {"Authorization": "Bearer test"}

        mock_response = MagicMock()
        mock_response.ok = False
        mock_post.return_value = mock_response

        mock_parse.return_value = {
            "status": 401,
            "code": "invalid_api_key",
            "message": "Invalid API key",
            "retryable": False,
            "fallback_ok": False,
        }

        service = TranslationService()
        payload = {"model": "test", "messages": []}

        with pytest.raises(OpenRouterFatalError) as exc_info:
            service._execute_openrouter_request(payload, "test-model")

        assert exc_info.value.code == "invalid_api_key"
        assert not exc_info.value.fallback_ok

    @patch("src.services.translation_service.requests.post")
    @patch("src.services.translation_service.openrouter_headers")
    @patch("src.services.translation_service.get_proxies")
    @patch("src.services.translation_service.monitoring_service")
    def test_invalid_json_raises_retryable(
        self, mock_monitoring, mock_proxies, mock_headers, mock_post
    ):
        """Invalid JSON response raises OpenRouterRetryableError."""
        mock_proxies.return_value = ({}, "none")
        mock_headers.return_value = {"Authorization": "Bearer test"}

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "not json"
        mock_post.return_value = mock_response

        service = TranslationService()
        service.failure_log_dir = MagicMock()
        service.failure_log_dir.mkdir = MagicMock()
        payload = {"model": "test", "messages": []}

        with pytest.raises(OpenRouterRetryableError) as exc_info:
            service._execute_openrouter_request(payload, "test-model")

        assert exc_info.value.code == "invalid_json"

    @patch("src.services.translation_service.requests.post")
    @patch("src.services.translation_service.openrouter_headers")
    @patch("src.services.translation_service.get_proxies")
    @patch("src.services.translation_service.monitoring_service")
    def test_missing_content_raises_retryable(
        self, mock_monitoring, mock_proxies, mock_headers, mock_post
    ):
        """Missing content in response raises OpenRouterRetryableError."""
        mock_proxies.return_value = ({}, "none")
        mock_headers.return_value = {"Authorization": "Bearer test"}

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"choices": []}  # No message content
        mock_response.text = '{"choices": []}'
        mock_post.return_value = mock_response

        service = TranslationService()
        service.failure_log_dir = MagicMock()
        service.failure_log_dir.mkdir = MagicMock()
        payload = {"model": "test", "messages": []}

        with pytest.raises(OpenRouterRetryableError) as exc_info:
            service._execute_openrouter_request(payload, "test-model")

        assert exc_info.value.code == "invalid_payload"

    @patch("src.services.translation_service.requests.post")
    @patch("src.services.translation_service.openrouter_headers")
    @patch("src.services.translation_service.get_proxies")
    @patch("src.services.translation_service.monitoring_service")
    def test_empty_content_raises_retryable(
        self, mock_monitoring, mock_proxies, mock_headers, mock_post
    ):
        """Empty content in response raises OpenRouterRetryableError."""
        mock_proxies.return_value = ({}, "none")
        mock_headers.return_value = {"Authorization": "Bearer test"}

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"choices": [{"message": {"content": ""}}]}
        mock_response.text = '{"choices": [{"message": {"content": ""}}]}'
        mock_post.return_value = mock_response

        service = TranslationService()
        service.failure_log_dir = MagicMock()
        service.failure_log_dir.mkdir = MagicMock()
        payload = {"model": "test", "messages": []}

        with pytest.raises(OpenRouterRetryableError) as exc_info:
            service._execute_openrouter_request(payload, "test-model")

        assert exc_info.value.code == "empty_content"

    @patch("src.services.translation_service.requests.post")
    @patch("src.services.translation_service.openrouter_headers")
    @patch("src.services.translation_service.get_proxies")
    @patch("src.services.translation_service.monitoring_service")
    def test_successful_response_returns_content(
        self, mock_monitoring, mock_proxies, mock_headers, mock_post
    ):
        """Successful response returns content string."""
        mock_proxies.return_value = ({}, "none")
        mock_headers.return_value = {"Authorization": "Bearer test"}

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Translated text"}}]
        }
        mock_post.return_value = mock_response

        service = TranslationService()
        payload = {"model": "test", "messages": []}

        result = service._execute_openrouter_request(payload, "test-model")

        assert result == "Translated text"


class TestTranslationValidation:
    """Tests for translation validation."""

    def test_empty_translation_raises_error(self):
        """Empty translation raises TranslationValidationError."""
        service = TranslationService()

        with pytest.raises(TranslationValidationError) as exc_info:
            service._validate_translation("Original text", "")

        assert "empty" in str(exc_info.value).lower()

    def test_whitespace_only_translation_raises_error(self):
        """Whitespace-only translation raises TranslationValidationError."""
        service = TranslationService()

        with pytest.raises(TranslationValidationError) as exc_info:
            service._validate_translation("Original text", "   \n\t   ")

        assert "empty" in str(exc_info.value).lower()

    def test_math_placeholders_in_output_raises_error(self):
        """Math placeholders in final output raise TranslationValidationError."""
        service = TranslationService()

        with pytest.raises(TranslationValidationError) as exc_info:
            service._validate_translation(
                "Original text", "Translated text with ⟪MATH_1⟫ leftover"
            )

        assert "Math placeholder" in str(exc_info.value)

    def test_valid_translation_passes(self):
        """Valid translation passes validation without error."""
        service = TranslationService()

        # Should not raise
        service._validate_translation(
            "Original Chinese text", "Translated English text"
        )

    def test_citation_mismatch_logs_warning(self):
        """Citation mismatch logs warning but doesn't raise."""
        service = TranslationService()

        # Should not raise, just log warning
        service._validate_translation(
            "Original with \\cite{ref1} and \\cite{ref2}",
            "Translated with only \\cite{ref1}",
        )

    def test_latex_command_mismatch_logs_warning(self):
        """LaTeX command mismatch logs warning but doesn't raise."""
        service = TranslationService()

        # Should not raise, just log warning
        service._validate_translation(
            "Original with \\textbf{bold} and \\emph{italic}",
            "Translated with only \\textbf{bold}",
        )


class TestErrorClassHierarchy:
    """Tests for error class hierarchy and attributes."""

    def test_openrouter_error_defaults(self):
        """OpenRouterError has correct default attributes."""
        error = OpenRouterError("Test error")

        assert error.code is None
        assert error.retryable is False
        assert error.fallback_ok is True

    def test_openrouter_error_with_attributes(self):
        """OpenRouterError accepts custom attributes."""
        error = OpenRouterError(
            "Test error", code="test_code", retryable=True, fallback_ok=False
        )

        assert error.code == "test_code"
        assert error.retryable is True
        assert error.fallback_ok is False

    def test_retryable_error_inherits_from_base(self):
        """OpenRouterRetryableError inherits from OpenRouterError."""
        error = OpenRouterRetryableError("Test error", code="retry_code")

        assert isinstance(error, OpenRouterError)
        assert error.retryable is True
        assert error.code == "retry_code"

    def test_fatal_error_inherits_from_base(self):
        """OpenRouterFatalError inherits from OpenRouterError."""
        error = OpenRouterFatalError("Test error", code="fatal_code")

        assert isinstance(error, OpenRouterError)
        assert error.retryable is False
        assert error.fallback_ok is False
        assert error.code == "fatal_code"

    def test_circuit_breaker_is_exception(self):
        """CircuitBreakerOpen is a standard Exception."""
        error = CircuitBreakerOpen("Circuit open")

        assert isinstance(error, Exception)
        assert str(error) == "Circuit open"

    def test_validation_error_is_exception(self):
        """TranslationValidationError is a standard Exception."""
        error = TranslationValidationError("Validation failed")

        assert isinstance(error, Exception)
        assert str(error) == "Validation failed"
