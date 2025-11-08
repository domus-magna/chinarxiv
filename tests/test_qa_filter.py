#!/usr/bin/env python3
"""
Comprehensive tests for QA filter to ensure no false positives.
Tests various edge cases and scenarios.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from qa_filter import TranslationQAFilter, ChineseCharacterDetector, QAStatus


class TestChineseCharacterDetector:
    """Test Chinese character detection."""
    
    def setup_method(self):
        self.detector = ChineseCharacterDetector()
    
    def test_chinese_ideographs(self):
        """Test Chinese ideograph detection."""
        assert self.detector.is_chinese_ideograph("中") is True
        assert self.detector.is_chinese_ideograph("文") is True
        assert self.detector.is_chinese_ideograph("学") is True
        assert self.detector.is_chinese_ideograph("机") is True
        assert self.detector.is_chinese_ideograph("器") is True
        assert self.detector.is_chinese_ideograph("学") is True
        assert self.detector.is_chinese_ideograph("习") is True
        
        # English characters should not be detected
        assert self.detector.is_chinese_ideograph("a") is False
        assert self.detector.is_chinese_ideograph("A") is False
        assert self.detector.is_chinese_ideograph("1") is False
        assert self.detector.is_chinese_ideograph(" ") is False
        assert self.detector.is_chinese_ideograph(".") is False
    
    def test_chinese_punctuation(self):
        """Test Chinese punctuation detection."""
        # Chinese punctuation
        assert self.detector.is_chinese_char("：") is True
        assert self.detector.is_chinese_char("；") is True
        assert self.detector.is_chinese_char("，") is True
        assert self.detector.is_chinese_char("。") is True
        assert self.detector.is_chinese_char("！") is True
        assert self.detector.is_chinese_char("？") is True
        assert self.detector.is_chinese_char("（") is True
        assert self.detector.is_chinese_char("）") is True
        assert self.detector.is_chinese_char("【") is True
        assert self.detector.is_chinese_char("】") is True
        assert self.detector.is_chinese_char("《") is True
        assert self.detector.is_chinese_char("》") is True
        assert self.detector.is_chinese_char("、") is True
        assert self.detector.is_chinese_char("…") is True
        assert self.detector.is_chinese_char("～") is True
        
        # English punctuation should not be detected
        assert self.detector.is_chinese_char(":") is False
        assert self.detector.is_chinese_char(";") is False
        assert self.detector.is_chinese_char(",") is False
        assert self.detector.is_chinese_char(".") is False
        assert self.detector.is_chinese_char("!") is False
        assert self.detector.is_chinese_char("?") is False
        assert self.detector.is_chinese_char("(") is False
        assert self.detector.is_chinese_char(")") is False
        assert self.detector.is_chinese_char("[") is False
        assert self.detector.is_chinese_char("]") is False
        assert self.detector.is_chinese_char("<") is False
        assert self.detector.is_chinese_char(">") is False
        # Note: "..." is 3 characters, not a single character
        assert self.detector.is_chinese_char("~") is False
    
    def test_chinese_metadata_markers(self):
        """Test Chinese metadata marker detection."""
        assert self.detector.has_chinese_metadata("作者：张三") is True
        assert self.detector.has_chinese_metadata("提交时间：2025-03-08") is True
        assert self.detector.has_chinese_metadata("摘要: This is an abstract") is True
        assert self.detector.has_chinese_metadata("分类：Physics") is True
        assert self.detector.has_chinese_metadata("引用：ChinaXiv:202503.00001") is True
        assert self.detector.has_chinese_metadata("DOI: 10.12074/202503.00001") is True
        assert self.detector.has_chinese_metadata("CSTR: 32003.36.ChinaXiv.202503.00001") is True
        assert self.detector.has_chinese_metadata("推荐引用方式：") is True
        assert self.detector.has_chinese_metadata("版本历史") is True
        assert self.detector.has_chinese_metadata("下载全文") is True
        assert self.detector.has_chinese_metadata("来自：ChinaXiv") is True
        assert self.detector.has_chinese_metadata("关键词") is True
        
        # English text should not be detected
        assert self.detector.has_chinese_metadata("Authors: John Smith") is False
        assert self.detector.has_chinese_metadata("Submission Date: 2025-03-08") is False
        assert self.detector.has_chinese_metadata("Abstract: This is an abstract") is False
        assert self.detector.has_chinese_metadata("Category: Physics") is False
        assert self.detector.has_chinese_metadata("Citation: ChinaXiv:202503.00001") is False
        # Note: "DOI:" is in Chinese metadata markers, so this will be True
        assert self.detector.has_chinese_metadata("Digital Object Identifier: 10.12074/202503.00001") is False
        assert self.detector.has_chinese_metadata("Recommended Citation:") is False
        assert self.detector.has_chinese_metadata("Version History") is False
        assert self.detector.has_chinese_metadata("Download Full Text") is False
        assert self.detector.has_chinese_metadata("From: ChinaXiv") is False
        assert self.detector.has_chinese_metadata("Keywords") is False
    
    def test_ratio_calculations(self):
        """Test ratio calculations."""
        # Pure English text
        english_text = "This is a test abstract with no Chinese characters."
        assert self.detector.calculate_chinese_ratio(english_text) == 0.0
        assert self.detector.calculate_chinese_ideograph_ratio(english_text) == 0.0
        
        # Mixed text
        mixed_text = "Hello 世界：this is a test."
        assert self.detector.calculate_chinese_ratio(mixed_text) > 0.0
        assert self.detector.calculate_chinese_ideograph_ratio(mixed_text) > 0.0
        
        # Chinese punctuation only
        chinese_punct_text = "Hello world：this is a test，with Chinese punctuation."
        assert self.detector.calculate_chinese_ratio(chinese_punct_text) > 0.0
        assert self.detector.calculate_chinese_ideograph_ratio(chinese_punct_text) == 0.0


class TestTranslationQAFilter:
    """Test translation QA filter."""
    
    def setup_method(self):
        self.qa_filter = TranslationQAFilter()
    
    def test_clean_english_translation(self):
        """Test clean English translation passes."""
        clean_translation = {
            'id': 'test-clean',
            'title_en': 'Machine Learning for Beam Correction Study',
            'abstract_en': 'This study utilizes machine learning techniques to analyze beam correction in particle accelerators. The research focuses on improving beam quality and reducing particle loss during injection. We present a novel approach using neural networks to predict beam behavior and optimize correction parameters.',
            'body_en': ['This is the first paragraph of the paper describing the methodology.', 'This is the second paragraph with experimental results and analysis.'],
            'creators': ['John Smith', 'Jane Doe'],
            'subjects': ['Physics', 'Machine Learning']
        }
        
        result = self.qa_filter.check_translation(clean_translation)
        assert result.status == QAStatus.PASS
        assert result.score == 1.0
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert result.issues == []
        assert result.flagged_fields == []
        assert self.qa_filter.should_display(result) is True
    
    def test_chinese_characters_flag(self):
        """Test Chinese characters are flagged."""
        chinese_translation = {
            'id': 'test-chinese',
            'title_en': 'Machine Learning Study',
            'abstract_en': 'This study utilizes machine learning techniques to analyze beam correction. 作者：张三',
            'body_en': ['This is the body text.'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        
        result = self.qa_filter.check_translation(chinese_translation)
        assert result.status == QAStatus.FLAG_CHINESE
        assert result.score < 1.0
        assert result.chinese_ratio > 0.0
        assert len(result.chinese_chars) > 0
        assert len(result.issues) > 0
        assert len(result.flagged_fields) > 0
        assert self.qa_filter.should_display(result) is False
    
    def test_chinese_metadata_flag(self):
        """Test Chinese metadata markers are flagged."""
        metadata_translation = {
            'id': 'test-metadata',
            'title_en': 'Machine Learning Study',
            'abstract_en': 'This study utilizes machine learning techniques. 提交时间：2025-03-08',
            'body_en': ['This is the body text.'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        
        result = self.qa_filter.check_translation(metadata_translation)
        assert result.status == QAStatus.FLAG_CHINESE
        assert result.score < 1.0
        assert len(result.issues) > 0
        assert len(result.flagged_fields) > 0
        assert self.qa_filter.should_display(result) is False
    
    def test_short_abstract_flag(self):
        """Test short abstract is flagged."""
        short_translation = {
            'id': 'test-short',
            'title_en': 'Machine Learning Study',
            'abstract_en': 'Short abstract.',
            'body_en': ['This is the body text.'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        
        result = self.qa_filter.check_translation(short_translation)
        assert result.status == QAStatus.FLAG_FORMATTING
        assert result.score < 1.0
        assert len(result.issues) > 0
        assert len(result.flagged_fields) > 0
        assert self.qa_filter.should_display(result) is False
    
    def test_english_punctuation_passes(self):
        """Test English punctuation passes."""
        english_punct_translation = {
            'id': 'test-english-punct',
            'title_en': 'Machine Learning Study',
            'abstract_en': 'This study utilizes machine learning techniques: colons, semicolons; commas, and periods. It also has quotes "like this" and parentheses (like this).',
            'body_en': ['This is the body text with normal English punctuation.'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        
        result = self.qa_filter.check_translation(english_punct_translation)
        assert result.status == QAStatus.PASS
        assert result.score == 1.0
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert result.issues == []
        assert result.flagged_fields == []
        assert self.qa_filter.should_display(result) is True
    
    def test_chinese_punctuation_flag(self):
        """Test Chinese punctuation is flagged."""
        chinese_punct_translation = {
            'id': 'test-chinese-punct',
            'title_en': 'Machine Learning Study',
            'abstract_en': 'This study utilizes machine learning techniques：colons，semicolons；commas，and periods。It also has quotes "like this" and parentheses (like this).',
            'body_en': ['This is the body text with Chinese punctuation。'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        
        result = self.qa_filter.check_translation(chinese_punct_translation)
        assert result.status == QAStatus.FLAG_CHINESE
        assert result.score < 1.0
        assert result.chinese_ratio > 0.0
        assert len(result.chinese_chars) > 0
        assert len(result.issues) > 0
        assert len(result.flagged_fields) > 0
        assert self.qa_filter.should_display(result) is False
    
    def test_edge_cases(self):
        """Test various edge cases."""
        # Empty translation
        empty_translation = {'id': 'test-empty'}
        result = self.qa_filter.check_translation(empty_translation)
        assert result.status == QAStatus.PASS
        
        # None values
        none_translation = {
            'id': 'test-none',
            'title_en': None,
            'abstract_en': None,
            'body_en': None
        }
        result = self.qa_filter.check_translation(none_translation)
        assert result.status == QAStatus.PASS
        
        # List body
        list_translation = {
            'id': 'test-list',
            'title_en': 'Test',
            'abstract_en': 'This is a longer test abstract that meets the minimum length requirement.',
            'body_en': ['Paragraph 1', 'Paragraph 2']
        }
        result = self.qa_filter.check_translation(list_translation)
        assert result.status == QAStatus.PASS
        
        # Mixed case
        mixed_case_translation = {
            'id': 'test-mixed',
            'title_en': 'Test',
            'abstract_en': 'This is a longer test abstract that meets the minimum length requirement.',
            'body_en': ['Paragraph 1', 'Paragraph 2'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        result = self.qa_filter.check_translation(mixed_case_translation)
        assert result.status == QAStatus.PASS
    
    def test_technical_symbols_pass(self):
        """Test technical symbols and special characters pass."""
        technical_translation = {
            'id': 'test-technical',
            'title_en': 'Machine Learning Study',
            'abstract_en': 'This study uses mathematical symbols: α, β, γ, δ, ε, ζ, η, θ, λ, μ, π, ρ, σ, τ, φ, χ, ψ, ω. It also includes special characters: ±, ×, ÷, ≠, ≤, ≥, ∞, ∑, ∏, ∫, ∂, ∇, ∆, √, ∛, ∜, ∠, ∟, ⊥, ∥, ∦, ∝, ∼, ≈, ≅, ≡, ≢, ≣, ≦, ≧, ≨, ≩, ≪, ≫, ≬, ≭, ≮, ≯, ≰, ≱, ≲, ≳, ≴, ≵, ≶, ≷, ≸, ≹, ≺, ≻, ≼, ≽, ≾, ≿, ⊀, ⊁, ⊂, ⊃, ⊄, ⊅, ⊆, ⊇, ⊈, ⊉, ⊊, ⊋, ⊌, ⊍, ⊎, ⊏, ⊐, ⊑, ⊒, ⊓, ⊔, ⊕, ⊖, ⊗, ⊘, ⊙, ⊚, ⊛, ⊜, ⊝, ⊞, ⊟, ⊠, ⊡, ⊢, ⊣, ⊤, ⊥, ⊦, ⊧, ⊨, ⊩, ⊪, ⊫, ⊬, ⊭, ⊮, ⊯, ⊰, ⊱, ⊲, ⊳, ⊴, ⊵, ⊶, ⊷, ⊸, ⊹, ⊺, ⊻, ⊼, ⊽, ⊾, ⊿, ⋀, ⋁, ⋂, ⋃, ⋄, ⋅, ⋆, ⋇, ⋈, ⋉, ⋊, ⋋, ⋌, ⋍, ⋎, ⋏, ⋐, ⋑, ⋒, ⋓, ⋔, ⋕, ⋖, ⋗, ⋘, ⋙, ⋚, ⋛, ⋜, ⋝, ⋞, ⋟, ⋠, ⋡, ⋢, ⋣, ⋤, ⋥, ⋦, ⋧, ⋨, ⋩, ⋪, ⋫, ⋬, ⋭, ⋮, ⋯, ⋰, ⋱, ⋲, ⋳, ⋴, ⋵, ⋶, ⋷, ⋸, ⋹, ⋺, ⋻, ⋼, ⋽, ⋾, ⋿.',
            'body_en': ['This is the body text with technical symbols.'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        
        result = self.qa_filter.check_translation(technical_translation)
        assert result.status == QAStatus.PASS
        assert result.score == 1.0
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert result.issues == []
        assert result.flagged_fields == []

    def test_trailing_chinese_is_flagged(self):
        translation = {
            'id': 'test-trailing',
            'title_en': 'Valid English Title',
            'abstract_en': 'This abstract is valid English with no Chinese content and sufficient length to pass the minimum requirement of the QA filter.',
            'body_en': [
                'This is a valid English paragraph to start the body.',
                'Another valid paragraph to ensure enough content.',
                'Ending with Chinese 中文',
            ],
        }
        result = self.qa_filter.check_translation(translation)
        assert result.status in (QAStatus.FLAG_CHINESE, QAStatus.FLAG_CONTENT)
        assert any('trailing Chinese' in msg for msg in result.issues)

    def test_start_without_real_english_is_flagged(self):
        translation = {
            'id': 'test-start-content',
            'title_en': '... ( )',
            'abstract_en': '   ',
            'body_en': [',,, -- !!']
        }
        result = self.qa_filter.check_translation(translation)
        assert result.status in (QAStatus.FLAG_FORMATTING, QAStatus.FLAG_CONTENT)
        assert 'title_en does not start with real English content' in ' '.join(result.issues)
        assert self.qa_filter.should_display(result) is False
    
    def test_unicode_edge_cases(self):
        """Test Unicode edge cases."""
        unicode_translation = {
            'id': 'test-unicode',
            'title_en': 'Unicode Test Study',
            'abstract_en': 'This study includes various Unicode characters: é, ñ, ü, ö, ä, ß, ç, ğ, ş, ı, ö, ü, ç, ğ, ş, ı, å, ø, æ, ñ, á, é, í, ó, ú, à, è, ì, ò, ù, â, ê, î, ô, û, ã, õ, ă, ĕ, ĭ, ŏ, ŭ, ā, ē, ī, ō, ū, ą, ę, į, ǫ, ų, ǎ, ě, ǐ, ǒ, ǔ, ǎ, ě, ǐ, ǒ, ǔ, ǎ, ě, ǐ, ǒ, ǔ.',
            'body_en': ['This is the body text with Unicode characters.'],
            'creators': ['José María', 'François'],
            'subjects': ['Physics', 'Mathematics']
        }
        
        result = self.qa_filter.check_translation(unicode_translation)
        assert result.status == QAStatus.PASS
        assert result.score == 1.0
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert result.issues == []
        assert result.flagged_fields == []
        assert self.qa_filter.should_display(result) is True
    
    def test_numbers_and_symbols(self):
        """Test numbers and symbols pass."""
        numbers_translation = {
            'id': 'test-numbers',
            'title_en': 'Numerical Analysis Study',
            'abstract_en': 'This study analyzes numerical data: 123, 456.789, 1e-10, 2.5×10³, 3.14×10⁻⁶, 1.618, 2.718, 3.14159, 0.5772, 1.4142, 1.7320, 2.2360, 2.4494, 2.6457, 2.8284, 3.0000, 3.1622, 3.3166, 3.4641, 3.6055, 3.7416, 3.8729, 4.0000.',
            'body_en': ['This is the body text with numbers.'],
            'creators': ['John Smith'],
            'subjects': ['Mathematics']
        }
        
        result = self.qa_filter.check_translation(numbers_translation)
        assert result.status == QAStatus.PASS
        assert result.score == 1.0
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert result.issues == []
        assert result.flagged_fields == []
        assert self.qa_filter.should_display(result) is True
    
    def test_single_chinese_character_flags(self):
        """Test that even a single Chinese character flags the translation."""
        single_char_translation = {
            'id': 'test-single-char',
            'title_en': 'Machine Learning Study',
            'abstract_en': 'This study utilizes machine learning techniques to analyze beam correction in particle accelerators. The research focuses on improving beam quality and reducing particle loss during injection. We present a novel approach using neural networks to predict beam behavior and optimize correction parameters. 中',
            'body_en': ['This is the body text.'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        
        result = self.qa_filter.check_translation(single_char_translation)
        assert result.status == QAStatus.FLAG_CHINESE
        assert result.score < 1.0
        assert result.chinese_ratio > 0.0
        assert len(result.chinese_chars) > 0
        assert len(result.issues) > 0
        assert len(result.flagged_fields) > 0
        assert self.qa_filter.should_display(result) is False
    
    def test_chinese_punctuation_only_flags(self):
        """Test that Chinese punctuation only flags the translation."""
        chinese_punct_only_translation = {
            'id': 'test-chinese-punct-only',
            'title_en': 'Machine Learning Study',
            'abstract_en': 'This study utilizes machine learning techniques to analyze beam correction in particle accelerators. The research focuses on improving beam quality and reducing particle loss during injection. We present a novel approach using neural networks to predict beam behavior and optimize correction parameters。',
            'body_en': ['This is the body text with Chinese punctuation。'],
            'creators': ['John Smith'],
            'subjects': ['Physics']
        }
        
        result = self.qa_filter.check_translation(chinese_punct_only_translation)
        assert result.status == QAStatus.FLAG_FORMATTING
        assert result.score < 1.0
        assert result.chinese_ratio > 0.0
        assert len(result.chinese_chars) > 0
        assert len(result.issues) > 0
        assert len(result.flagged_fields) > 0
        assert self.qa_filter.should_display(result) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
