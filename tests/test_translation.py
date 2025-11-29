"""
Tests for simplified translation service.
"""

import pytest
from unittest.mock import patch, MagicMock
from src.translation import TranslationService, translate_text, translate_paper


class TestTranslationService:
    """Test simplified translation service."""

    def test_init(self):
        """Test service initialization."""
        service = TranslationService()
        assert service.model == "deepseek/deepseek-v3.2-exp"
        assert len(service.glossary) > 0
        assert "机器学习" in [g["zh"] for g in service.glossary]

    @patch("src.translation.mask_math")
    @patch("src.translation.unmask_math")
    @patch.object(TranslationService, "_call_api")
    def test_translate_text(self, mock_call_api, mock_unmask, mock_mask):
        """Test text translation."""
        # Setup mocks
        mock_mask.return_value = ("masked text", {"MATH_1": "x^2"})
        mock_call_api.return_value = "translated masked text"
        mock_unmask.return_value = "translated text with x^2"

        service = TranslationService()
        result = service.translate_text("Chinese text with x^2")

        assert result == "translated text with x^2"
        mock_mask.assert_called_once_with("Chinese text with x^2")
        mock_call_api.assert_called_once_with("masked text")
        mock_unmask.assert_called_once_with("translated masked text", {"MATH_1": "x^2"})

    def test_translate_empty_text(self):
        """Test translating empty text."""
        service = TranslationService()
        result = service.translate_text("")
        assert result == ""

    def test_translate_none_text(self):
        """Test translating None text."""
        service = TranslationService()
        result = service.translate_text(None)
        assert result == ""

    @patch.object(TranslationService, "_load_paper")
    @patch.object(TranslationService, "_save_translation")
    @patch.object(TranslationService, "translate_text")
    def test_translate_paper(self, mock_translate, mock_save, mock_load):
        """Test paper translation."""
        # Setup mocks
        mock_load.return_value = {
            "id": "test_paper",
            "title": "Test Title",
            "abstract": "Test Abstract",
            "body": ["Paragraph 1", "Paragraph 2"],
        }
        mock_translate.side_effect = [
            "Translated Title",
            "Translated Abstract",
            "Translated Paragraph 1",
            "Translated Paragraph 2",
        ]

        service = TranslationService()
        result = service.translate_paper("test_paper")

        assert result == "data/translated/test_paper.json"
        mock_load.assert_called_once_with("test_paper")
        mock_save.assert_called_once()

        # Check translation calls
        assert mock_translate.call_count == 4

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("src.http_client.get_session")
    def test_call_api(self, mock_get_session):
        """Test API call."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Translated text"}}]
        }

        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        mock_get_session.return_value = mock_session

        service = TranslationService()
        result = service._call_api("Test text")

        assert result == "Translated text"
        mock_session.post.assert_called_once()

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    @patch("src.http_client.get_session")
    def test_call_api_error(self, mock_get_session):
        """Test API call error."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 500

        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        mock_get_session.return_value = mock_session

        service = TranslationService()

        # The retry decorator will raise RetryError after 3 attempts
        with pytest.raises(Exception) as exc_info:
            service._call_api("Test text")

        # Check that the original exception is wrapped in RetryError
        assert "RetryError" in str(exc_info.value)

    def test_build_system_prompt(self):
        """Test system prompt building."""
        service = TranslationService()
        prompt = service._build_system_prompt()

        assert "Translate from Chinese to English" in prompt
        assert "机器学习" in prompt
        assert "machine learning" in prompt

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
    def test_get_headers(self):
        """Test getting API headers."""
        service = TranslationService()
        headers = service._get_headers()

        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"
        assert "HTTP-Referer" in headers
        assert "X-Title" in headers

    @patch.dict("os.environ", {}, clear=True)
    def test_get_headers_no_key(self):
        """Test getting headers without API key."""
        service = TranslationService()

        with pytest.raises(
            ValueError, match="OPENROUTER_API_KEY environment variable not set"
        ):
            service._get_headers()


class TestConvenienceFunctions:
    """Test convenience functions."""

    @patch("src.translation.translation_service")
    def test_translate_text_function(self, mock_service):
        """Test translate_text convenience function."""
        mock_service.translate_text.return_value = "Translated text"

        result = translate_text("Test text")
        assert result == "Translated text"
        mock_service.translate_text.assert_called_once_with("Test text")

    @patch("src.translation.translation_service")
    def test_translate_paper_function(self, mock_service):
        """Test translate_paper convenience function."""
        mock_service.translate_paper.return_value = "data/translated/test.json"

        result = translate_paper("test_paper")
        assert result == "data/translated/test.json"
        mock_service.translate_paper.assert_called_once_with("test_paper")
