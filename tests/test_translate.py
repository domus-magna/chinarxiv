"""
Tests for translation functionality.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.translate import translate_field, translate_paragraphs, translate_record
from src.services.translation_service import MathPreservationError
from src.services.translation_service import TranslationService


class TestTranslation:
    """Test translation functionality."""
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_field_success(self, mock_translate):
        """Test successful field translation."""
        mock_translate.return_value = "Translated text"
        
        result = translate_field("Original text", "model", [], dry_run=False)
        
        assert result == "Translated text"
        mock_translate.assert_called_once()
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_field_dry_run(self, mock_translate):
        """Test dry run translation (no API call)."""
        result = translate_field("Original text", "model", [], dry_run=True)
        
        # In dry run, should return original text with math masking
        assert "Original text" in result
        mock_translate.assert_not_called()
    
    def test_translate_field_empty_text(self):
        """Test translation of empty text."""
        result = translate_field("", "model", [], dry_run=False)
        assert result == ""
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_field_with_math(self, mock_translate):
        """Test translation with math expressions."""
        mock_translate.return_value = "The equation ⟪MATH_0001⟫ is simple."
        
        text = "The equation $x = y$ is simple."
        result = translate_field(text, "model", [], dry_run=False)
        
        # Should unmask math expressions
        assert "$x = y$" in result
        mock_translate.assert_called_once()
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_paragraphs(self, mock_translate):
        """Test translation of multiple paragraphs."""
        # Mock needs to handle whole-paper mode with numbered paragraph tags
        def mock_response(text, model, glossary):
            # If input has PARA tags, preserve them in output
            if '<PARA id="0">' in text:
                return '<PARA id="0">Translated first paragraph</PARA>\n<PARA id="1">Translated second paragraph</PARA>'
            # Otherwise individual paragraph translation
            return "Translated paragraph"

        mock_translate.side_effect = mock_response

        paragraphs = ["First paragraph", "Second paragraph"]
        result = translate_paragraphs(paragraphs, "model", [], dry_run=False)

        assert len(result) == 2
        assert "Translated first paragraph" in result[0]
        assert "Translated second paragraph" in result[1]
        # With whole_paper_mode, should be 1 call for all paragraphs
        assert mock_translate.call_count == 1

    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_paragraphs_default_config(self, mock_translate):
        """Whole-paper mode should remain enabled even if config lacks translation block."""

        def mock_response(text, model, glossary):
            if '<PARA id="0">' in text:
                return '<PARA id="0">Default config first</PARA>\n<PARA id="1">Default config second</PARA>'
            return "Translated paragraph"

        mock_translate.side_effect = mock_response

        service = TranslationService(config={})
        paragraphs = ["First paragraph", "Second paragraph"]

        result = service.translate_paragraphs(paragraphs, dry_run=False)

        assert len(result) == 2
        assert "Default config first" in result[0]
        assert "Default config second" in result[1]
        assert mock_translate.call_count == 1
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_record_basic(self, mock_translate):
        """Test basic record translation."""
        mock_translate.return_value = "Translated"
        
        record = {
            "id": "test-1",
            "title": "Test Title",
            "abstract": "Test abstract",
            "creators": ["Author 1"],
            "subjects": ["cs.AI"],
            "date": "2024-01-01",
            "license": {"derivatives_allowed": True}
        }
        
        result = translate_record(record, "model", [], dry_run=False)
        
        assert result["id"] == "test-1"
        assert result["title_en"] == "Translated"
        assert result["abstract_en"] == "Translated"
        assert result["creators"] == ["Author 1"]
        assert result["subjects"] == ["cs.AI"]
        assert result["date"] == "2024-01-01"
    
    @patch('src.services.translation_service.extract_body_paragraphs')
    def test_translate_record_sets_full_body_flags(self, mock_extract):
        """Translation records should carry full-body metadata for publishing filters."""
        mock_extract.side_effect = [
            ["段落一", "段落二"],
            [],
        ]

        record = {
            "id": "test-full",
            "title": "Test Title",
            "abstract": "测试摘要" * 20,
            "creators": ["Author 1"],
            "subjects": ["cs.AI"],
            "date": "2024-01-01",
            "license": {"derivatives_allowed": True},
            "files": {"pdf_path": "/tmp/test.pdf"},
        }
        record_missing = {
            "id": "test-missing",
            "title": "Test Title",
            "abstract": "测试摘要" * 20,
            "creators": ["Author 1"],
            "subjects": ["cs.AI"],
            "date": "2024-01-01",
            "license": {"derivatives_allowed": True},
            "files": {},
        }

        cfg = {"models": {"default_slug": "mock-model"}}

        with patch.object(TranslationService, "translate_field", return_value="Translated field"), patch.object(
            TranslationService, "translate_paragraphs", return_value=["Translated paragraph"]
        ):
            service = TranslationService(config=cfg)
            full = service.translate_record(record, dry_run=False)
            missing = service.translate_record(record_missing, dry_run=False)

        assert full["_has_full_body"] is True
        assert full.get("_full_body_reason") is None
        assert full["_body_paragraphs"] == 1
        assert full.get("_body_source") == "pdf"

        assert missing["_has_full_body"] is False
        assert missing["_full_body_reason"] == "missing_assets"
        assert missing["_body_paragraphs"] == 0
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_record_no_derivatives(self, mock_translate):
        """Test translation when derivatives not allowed."""
        mock_translate.return_value = "Translated"
        
        record = {
            "id": "test-1",
            "title": "Test Title",
            "abstract": "Test abstract",
            "license": {"derivatives_allowed": False}
        }
        
        result = translate_record(record, "model", [], dry_run=False)
        
        assert result["title_en"] == "Translated"
        assert result["abstract_en"] == "Translated"
        assert result["body_en"] is None  # No full text translation
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_record_force_full_text(self, mock_translate):
        """Test translation with force_full_text=True."""
        mock_translate.return_value = "Translated"
        
        record = {
            "id": "test-1",
            "title": "Test Title",
            "abstract": "Test abstract",
            "license": {"derivatives_allowed": False},
            "files": {"pdf_path": "test.pdf"}  # Need PDF path for body extraction
        }
        
        with patch('src.services.translation_service.extract_body_paragraphs') as mock_extract:
            mock_extract.return_value = ["Body paragraph 1", "Body paragraph 2"]
            
            result = translate_record(record, "model", [], dry_run=False, force_full_text=True)
            
            assert result["title_en"] == "Translated"
            assert result["abstract_en"] == "Translated"
            # Should have body_en even though derivatives not allowed
            assert result["body_en"] is not None
            assert len(result["body_en"]) == 2
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_record_with_glossary(self, mock_translate):
        """Test translation with glossary."""
        mock_translate.return_value = "Translated"
        
        record = {
            "id": "test-1",
            "title": "Test Title",
            "abstract": "Test abstract",
            "license": {"derivatives_allowed": True}
        }
        
        glossary = [{"zh": "机器学习", "en": "machine learning"}]
        
        result = translate_record(record, "model", glossary, dry_run=False)
        
        # Check that glossary was passed to translation
        assert mock_translate.call_count >= 2  # At least title and abstract
        mock_translate.assert_called()
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_record_cost_tracking(self, mock_translate):
        """Test that cost tracking is performed."""
        mock_translate.return_value = "Translated"
        
        record = {
            "id": "test-1",
            "title": "Test Title",
            "abstract": "Test abstract",
            "license": {"derivatives_allowed": True}
        }
        
        with patch('src.services.translation_service.append_cost_log') as mock_cost_log:
            result = translate_record(record, "model", [], dry_run=False)
            
            # Should log cost
            mock_cost_log.assert_called_once()
            call_args = mock_cost_log.call_args[0]
            assert call_args[0] == "test-1"  # paper_id
            assert call_args[1] == "deepseek/deepseek-v3.2-exp"    # model
            assert isinstance(call_args[2], int)  # input tokens
            assert isinstance(call_args[3], int)  # output tokens
            assert isinstance(call_args[4], float)  # cost
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_field_math_parity_failure(self, mock_translate):
        """Test handling of math parity check failure."""
        # Mock translation that doesn't preserve math tokens
        mock_translate.return_value = "Translated text without math tokens"
        
        text = "The equation $x = y$ is simple."
        
        with pytest.raises(MathPreservationError, match="Math placeholder parity check failed"):
            translate_field(text, "model", [], dry_run=False)
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translate_field_network_error(self, mock_translate):
        """Test handling of network errors."""
        from src.services.translation_service import OpenRouterError
        mock_translate.side_effect = OpenRouterError("Network error")
        
        with pytest.raises(OpenRouterError):
            translate_field("Test text", "model", [], dry_run=False)
    
    def test_translate_record_missing_fields(self):
        """Test translation with missing optional fields."""
        record = {
            "id": "test-1",
            "title": "Test Title",
            "abstract": "Test abstract",
            "license": {"derivatives_allowed": True}
        }
        
        with patch('src.services.translation_service.TranslationService._call_openrouter') as mock_translate:
            mock_translate.return_value = "Translated"
            
            result = translate_record(record, "model", [], dry_run=False)
            
            # Should handle missing fields gracefully
            assert result["id"] == "test-1"
            assert result["title_en"] == "Translated"
            assert result["abstract_en"] == "Translated"
            assert result["creators"] is None
            assert result["subjects"] is None
            assert result["date"] is None
