"""
Simplified end-to-end test for the ChinaXiv English translation pipeline.

This test validates the core workflow components individually and together.
Tests synthesis mode translation which produces readable academic prose.
"""
import os
import tempfile
import shutil
from unittest.mock import patch

import pytest

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

    @patch('src.services.translation_service.TranslationService._call_openrouter_synthesis')
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_synthesis_mode_translation(self, mock_translate, mock_synthesis):
        """Test synthesis mode translation for records."""
        mock_translate.return_value = "Translated field"
        mock_synthesis.return_value = "## Introduction\n\nThis is the translated body content."

        service = TranslationService()
        record = {
            "id": "test-synthesis",
            "title": "测试标题",
            "abstract": "测试摘要",
            "creators": ["作者一"],
            "subjects": ["物理学"],
        }

        # Mock the extraction to return a simple result
        with patch('src.body_extract.extract_from_pdf_synthesis') as mock_extract:
            mock_extract.return_value = {
                "sections": [{"name": "Introduction", "paragraphs": ["段落一"]}],
                "stats": {"original_lines": 10, "merged_paragraphs": 5}
            }

            result = service.translate_record_synthesis(record, dry_run=False)

            assert result["id"] == "test-synthesis"
            assert result["title_en"] == "Translated field"
            assert result["abstract_en"] == "Translated field"
            assert result["_synthesis_mode"] is True

    @patch('src.services.translation_service.TranslationService._call_openrouter_synthesis')
    def test_synthesis_chunk_by_sections(self, mock_synthesis):
        """Test section-based chunking in synthesis mode."""
        mock_synthesis.return_value = "## Introduction\n\nTranslated content."

        service = TranslationService()

        extraction_result = {
            "sections": [
                {"name": "Introduction", "paragraphs": ["Para 1", "Para 2"]},
                {"name": "Methods", "paragraphs": ["Para 3"]},
            ],
            "stats": {}
        }

        result = service.translate_synthesis_mode(extraction_result, dry_run=False)

        assert "body_md" in result
        assert result["chunks_used"] >= 1
        mock_synthesis.assert_called()
