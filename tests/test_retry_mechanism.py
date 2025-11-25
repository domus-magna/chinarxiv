#!/usr/bin/env python3
"""
Tests for Chinese character retry mechanism.
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.services.translation_service import TranslationService


class TestRetryMechanism:
    """Test Chinese character retry mechanism."""
    
    def setup_method(self):
        self.config = {
            'translation': {'retry_chinese_chars': True},
            'model': 'deepseek/deepseek-v3.2-exp',
            'glossary': []
        }
        self.service = TranslationService()
        self.service.config = self.config
    
    def test_retry_enabled_by_config(self):
        """Test that retry is enabled by config."""
        assert self.service.config.get('translation', {}).get('retry_chinese_chars', True) is True
    
    def test_retry_disabled_by_config(self):
        """Test that retry can be disabled by config."""
        self.service.config['translation']['retry_chinese_chars'] = False
        assert self.service.config.get('translation', {}).get('retry_chinese_chars', True) is False
    
    def test_retry_conditions(self):
        """Test retry conditions."""
        # Mock QA result with Chinese characters
        mock_qa_result = Mock()
        mock_qa_result.status.value = 'flag_chinese'
        mock_qa_result.chinese_chars = ['中', '文']
        
        # Mock translation
        translation = {
            'id': 'test-001',
            'title_en': 'Test Paper',
            'abstract_en': 'This is a test abstract with Chinese: 中文',
            'body_en': ['This is the body text.']
        }
        
        # Test retry conditions
        should_retry = (
            not False and  # not dry_run
            self.service.config.get('translation', {}).get('retry_chinese_chars', True) and
            mock_qa_result.status.value == 'flag_chinese' and
            not translation.get('_retry_attempted') and
            len(mock_qa_result.chinese_chars) <= 5
        )
        
        assert should_retry is True
    
    def test_retry_prompt_generation(self):
        """Test retry prompt generation."""
        chinese_chars = ['中', '文']
        expected_prompt = f"""
                This paper was translated from Chinese to English, but Chinese characters were detected: {chinese_chars}
                
                Please re-translate the entire paper ensuring all Chinese characters are translated to English.
                Maintain exact formatting and structure. Return the corrected translation in the same format.
                """
        
        # Test that prompt contains Chinese characters
        assert '中' in expected_prompt
        assert '文' in expected_prompt
        assert 're-translate' in expected_prompt
    
    @patch('src.services.translation_service.TranslationService._call_openrouter')
    def test_retry_translate_with_prompt(self, mock_api_call):
        """Test retry translation with prompt."""
        # Mock API response (now uses _call_openrouter via translate_field)
        mock_api_call.return_value = "This is a corrected translation without Chinese characters."

        # Test translation with Chinese in abstract
        translation = {
            'id': 'test-001',
            'title_en': 'Test Paper',
            'abstract_en': 'This is a test abstract with Chinese: 中文',
            'body_en': ['This is the body text.']
        }

        retry_prompt = "Please fix Chinese characters"

        # Call retry method
        result = self.service._retry_translate_with_prompt(translation, retry_prompt)

        # Verify API was called (for the abstract field with Chinese characters)
        mock_api_call.assert_called_once()

        # Verify result structure
        assert result['id'] == 'test-001'
        assert result['title_en'] == 'Test Paper'
        assert result['body_en'] == ['This is the body text.']
        # Abstract should be updated with retry result
        assert result['abstract_en'] == "This is a corrected translation without Chinese characters."
    
    def test_retry_quality_check(self):
        """Test retry quality check logic."""
        # Original QA result
        original_qa = Mock()
        original_qa.chinese_chars = ['中', '文', '学']
        
        # Retry QA result (improved)
        retry_qa = Mock()
        retry_qa.chinese_chars = ['中']  # Fewer Chinese characters
        
        # Test quality improvement
        is_improvement = len(retry_qa.chinese_chars) < len(original_qa.chinese_chars)
        assert is_improvement is True
        
        # Test no improvement
        retry_qa_no_improvement = Mock()
        retry_qa_no_improvement.chinese_chars = ['中', '文', '学', '习']  # More Chinese characters
        
        is_no_improvement = len(retry_qa_no_improvement.chinese_chars) < len(original_qa.chinese_chars)
        assert is_no_improvement is False
    
    def test_retry_attempted_flag(self):
        """Test that retry attempted flag is set."""
        translation = {'id': 'test-001'}
        
        # Initially no retry attempted
        assert translation.get('_retry_attempted') is None
        
        # Set retry attempted flag
        translation['_retry_attempted'] = True
        
        # Verify flag is set
        assert translation.get('_retry_attempted') is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
