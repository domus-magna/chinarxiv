"""
Simplified end-to-end test for the ChinaXiv English translation pipeline.

This test validates the core workflow components individually and together.
"""
import os
import tempfile
import shutil
from unittest.mock import patch

import pytest

from src.translate import translate_field, translate_paragraphs, translate_record
from src.services.translation_service import TranslationService
from src.services.license_service import LicenseService


class TestE2ESimple:
    """Simplified end-to-end pipeline test."""
    
    def setup_method(self):
        """Set up test environment."""
        # Create temporary directory for test data
        self.test_dir = tempfile.mkdtemp(prefix="chinaxiv_e2e_simple_")
        self.original_cwd = os.getcwd()
        
        # Create test data structure
        self.data_dir = os.path.join(self.test_dir, "data")
        self.translated_dir = os.path.join(self.data_dir, "translated")
        
        os.makedirs(self.translated_dir, exist_ok=True)
        
        # Change to test directory
        os.chdir(self.test_dir)
    
    def teardown_method(self):
        """Clean up test environment."""
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translation_service_field(self, mock_translate):
        """Test translation service field translation."""
        mock_translate.return_value = "Translated text"
        
        service = TranslationService()
        result = service.translate_field("Original text", dry_run=False)
        
        assert result == "Translated text"
        mock_translate.assert_called_once()
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translation_service_paragraphs(self, mock_translate):
        """Test translation service paragraph translation."""
        mock_translate.return_value = "Translated paragraph"
        
        service = TranslationService()
        paragraphs = ["First paragraph", "Second paragraph"]
        result = service.translate_paragraphs(paragraphs, dry_run=False)
        
        assert len(result) == 2
        assert all("Translated paragraph" in p for p in result)
        assert mock_translate.call_count >= len(paragraphs)
        # The final calls should correspond to per-paragraph fallbacks
        tail_calls = mock_translate.call_args_list[-len(paragraphs):]
        for call, para in zip(tail_calls, paragraphs):
            assert para in call.args[0]

    @patch('src.services.translation_service.TranslationService.translate_field')
    def test_translation_service_paragraphs_full_fallback(self, mock_translate_field):
        """Ensure whole-paper mode falls back through all stages when validation fails."""
        paragraphs = ["第一段内容", "第二段内容"]
        mock_translate_field.side_effect = [
            "bad output",        # Whole-paper attempt (missing tags)
            "still bad output",  # Strict retry (still missing tags)
            "bad chunk output",  # Chunked translation result (missing sentinel split)
            "Paragraph 1 translated",
            "Paragraph 2 translated",
        ]

        service = TranslationService()
        result = service.translate_paragraphs(paragraphs, dry_run=False)

        assert result == ["Paragraph 1 translated", "Paragraph 2 translated"]
        assert mock_translate_field.call_count == 5
        # The last two calls should translate each paragraph individually
        assert mock_translate_field.call_args_list[-2][0][0] == paragraphs[0]
        assert mock_translate_field.call_args_list[-1][0][0] == paragraphs[1]
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_translation_service_record(self, mock_translate):
        """Test translation service record translation."""
        mock_translate.side_effect = [
            "Translated Title",
            "Translated Abstract"
        ]
        
        service = TranslationService()
        record = {
            "id": "test-1",
            "title": "Original Title",
            "abstract": "Original Abstract",
            "license": {"derivatives_allowed": True}
        }
        
        result = service.translate_record(record, dry_run=False)
        
        assert result["id"] == "test-1"
        assert result["title_en"] == "Translated Title"
        assert result["abstract_en"] == "Translated Abstract"
        assert result["body_en"] is None  # No PDF processing in this test
        assert mock_translate.call_count == 2
    
    def test_license_service(self):
        """Test license service."""
        # Create test config with license mapping
        test_config = {
            "license_mappings": {
                "CC BY": {"derivatives_allowed": True},
                "CC BY-SA": {"derivatives_allowed": True},
                "CC BY-NC": {"derivatives_allowed": False}
            }
        }
        
        service = LicenseService(test_config)
        
        # Test license decision
        record = {
            "id": "test-1",
            "license": {"type": "CC BY", "raw": "CC BY"}
        }
        
        result = service.decide_derivatives_allowed(record)
        # The license service should set derivatives_allowed based on the license type
        assert "license" in result
        assert "derivatives_allowed" in result["license"]
        assert result["license"]["derivatives_allowed"] is True
        
        # Test license summary
        summary = service.get_license_summary(result)
        assert "CC BY" in summary or "Unknown" in summary  # License type might be preserved as label
        assert "Allowed" in summary
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_backward_compatibility(self, mock_translate):
        """Test backward compatibility of translate module functions."""
        mock_translate.return_value = "Translated text"
        
        # Test translate_field
        result = translate_field("Original text", "model", [], dry_run=False)
        assert result == "Translated text"
        
        # Test translate_paragraphs
        paragraphs = ["First paragraph", "Second paragraph"]
        result = translate_paragraphs(paragraphs, "model", [], dry_run=False)
        assert len(result) == 2
        
        # Test translate_record
        record = {
            "id": "test-1",
            "title": "Original Title",
            "abstract": "Original Abstract",
            "license": {"derivatives_allowed": True}
        }
        result = translate_record(record, "model", [], dry_run=False)
        assert result["title_en"] == "Translated text"
        assert result["abstract_en"] == "Translated text"
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_math_preservation(self, mock_translate):
        """Test math expression preservation during translation."""
        mock_translate.return_value = "The equation ⟪MATH_0001⟫ is simple."
        
        service = TranslationService()
        text = "The equation $x = y$ is simple."
        result = service.translate_field(text, dry_run=False)
        
        # Should unmask math expressions
        assert "$x = y$" in result
        mock_translate.assert_called_once()
    
    def test_dry_run_mode(self):
        """Test dry run mode."""
        service = TranslationService()
        
        # Test field translation in dry run
        result = service.translate_field("Original text", dry_run=True)
        assert "Original text" in result
        
        # Test record translation in dry run
        record = {
            "id": "test-1",
            "title": "Original Title",
            "abstract": "Original Abstract",
            "license": {"derivatives_allowed": True}
        }
        result = service.translate_record(record, dry_run=True)
        assert result["title_en"] is not None
        assert result["abstract_en"] is not None
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_glossary_usage(self, mock_translate):
        """Test glossary usage in translation."""
        mock_translate.return_value = "This is about machine learning."
        
        service = TranslationService()
        text = "这是关于机器学习的内容。"
        service.translate_field(text, dry_run=False)
        
        # Verify glossary was included in the call
        call_args = mock_translate.call_args
        assert call_args is not None
        # The glossary should be passed to the translation call
        assert len(call_args[0]) >= 2  # text, model, glossary
    
    def test_error_handling(self):
        """Test error handling in services."""
        from src.services.translation_service import OpenRouterError
        
        service = TranslationService()
        
        # Test network error handling
        with patch('src.services.translation_service.TranslationService._call_openrouter') as mock_translate:
            mock_translate.side_effect = OpenRouterError("Network error")
            
            with pytest.raises(OpenRouterError):
                service.translate_field("Test text", dry_run=False)
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_cost_tracking(self, mock_translate):
        """Test cost tracking functionality."""
        mock_translate.return_value = "Translated text"
        
        with patch('src.services.translation_service.append_cost_log') as mock_cost_log:
            service = TranslationService()
            record = {
                "id": "test-1",
                "title": "Original Title",
                "abstract": "Original Abstract",
                "license": {"derivatives_allowed": True}
            }
            
            service.translate_record(record, dry_run=False)
            
            # Verify cost was logged
            mock_cost_log.assert_called_once()
            call_args = mock_cost_log.call_args[0]
            assert call_args[0] == "test-1"  # paper_id
            assert call_args[1] == "deepseek/deepseek-v3.2-exp"  # model
            assert isinstance(call_args[2], int)  # input tokens
            assert isinstance(call_args[3], int)  # output tokens
            assert isinstance(call_args[4], float)  # cost
