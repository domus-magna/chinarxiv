#!/usr/bin/env python3
"""
Comprehensive tests for QA filter to ensure no false positives.
Tests various edge cases and scenarios.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qa_filter import SynthesisQAFilter, ChineseCharacterDetector, QAStatus


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
        # DOI: and CSTR: are in the metadata markers list (not translated text artifacts)
        assert self.detector.has_chinese_metadata("DOI: 10.12074/202503.00001") is True
        assert (
            self.detector.has_chinese_metadata("CSTR: 32003.36.ChinaXiv.202503.00001")
            is True
        )
        assert self.detector.has_chinese_metadata("推荐引用方式：") is True
        assert self.detector.has_chinese_metadata("版本历史") is True
        assert self.detector.has_chinese_metadata("下载全文") is True
        assert self.detector.has_chinese_metadata("来自：ChinaXiv") is True
        assert self.detector.has_chinese_metadata("关键词") is True

        # English text should not be detected
        assert self.detector.has_chinese_metadata("Authors: John Smith") is False
        assert (
            self.detector.has_chinese_metadata("Submission Date: 2025-03-08") is False
        )
        assert (
            self.detector.has_chinese_metadata("Abstract: This is an abstract") is False
        )
        assert self.detector.has_chinese_metadata("Category: Physics") is False
        assert (
            self.detector.has_chinese_metadata("Citation: ChinaXiv:202503.00001")
            is False
        )
        assert (
            self.detector.has_chinese_metadata(
                "Digital Object Identifier: 10.12074/202503.00001"
            )
            is False
        )
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
        assert (
            self.detector.calculate_chinese_ideograph_ratio(chinese_punct_text) == 0.0
        )


class TestSynthesisQAFilter:
    """Test synthesis mode QA filter."""

    def setup_method(self):
        self.qa_filter = SynthesisQAFilter()

    def test_clean_english_translation(self):
        """Test clean English translation passes."""
        # Body needs to be >= 500 chars for MIN_BODY_LENGTH
        body_md = """## Introduction

This is the first paragraph of the paper describing the methodology. This paragraph contains enough content to pass the minimum length requirements for the synthesis QA filter. We present our novel approach to machine learning in this section.

## Methods

We used a convolutional neural network architecture with multiple layers. The training data consisted of beam position measurements collected over several months. Data preprocessing included normalization and outlier removal.

## Results

This is the results section with experimental findings and analysis. The results show significant improvements in beam quality metrics. We observed a 25% reduction in beam loss during injection phases. The neural network achieved 95% accuracy on the validation dataset."""

        clean_translation = {
            "id": "test-clean",
            "title_en": "Machine Learning for Beam Correction Study",
            "abstract_en": "This study utilizes machine learning techniques to analyze beam correction in particle accelerators. The research focuses on improving beam quality and reducing particle loss during injection. We present a novel approach using neural networks to predict beam behavior and optimize correction parameters.",
            "body_md": body_md,
        }

        result = self.qa_filter.check_synthesis_translation(clean_translation)
        assert result.status == QAStatus.PASS
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert self.qa_filter.should_display(result) is True

    def test_chinese_characters_flag(self):
        """Test Chinese characters are flagged."""
        chinese_translation = {
            "id": "test-chinese",
            "title_en": "Machine Learning Study",
            "abstract_en": "This study utilizes machine learning techniques to analyze beam correction. 作者：张三",
            "body_md": "## Introduction\n\nThis is the body text with sufficient length to pass minimum requirements. The content describes the methodology in detail.",
        }

        result = self.qa_filter.check_synthesis_translation(chinese_translation)
        assert result.status == QAStatus.FLAG_CHINESE
        assert result.score < 1.0
        assert result.chinese_ratio > 0.0
        assert len(result.chinese_chars) > 0
        assert len(result.issues) > 0
        assert len(result.flagged_fields) > 0

    def test_short_body_flag(self):
        """Test short body is flagged."""
        short_translation = {
            "id": "test-short",
            "title_en": "Machine Learning Study",
            "abstract_en": "This is a sufficient abstract with enough content.",
            "body_md": "Too short.",
        }

        result = self.qa_filter.check_synthesis_translation(short_translation)
        # Should flag due to body being too short
        assert result.score < 1.0
        assert any("Body too short" in issue for issue in result.issues)

    def test_english_punctuation_passes(self):
        """Test English punctuation passes."""
        body_md = """## Introduction

This is the body text with normal English punctuation: colons, semicolons; commas, and periods. It also has quotes "like this" and parentheses (like this). The paragraph has sufficient length to pass the minimum requirements for the synthesis QA filter.

## Methodology

We implemented our approach using standard software engineering practices. The codebase includes comprehensive unit tests and integration tests. Documentation covers all major functions and classes.

## Results

Our experimental results show significant improvements over baseline methods. We achieved a 30% reduction in processing time while maintaining accuracy. The system handles edge cases gracefully and produces consistent output."""

        english_punct_translation = {
            "id": "test-english-punct",
            "title_en": "Machine Learning Study",
            "abstract_en": 'This study utilizes machine learning techniques: colons, semicolons; commas, and periods. It also has quotes "like this" and parentheses (like this).',
            "body_md": body_md,
        }

        result = self.qa_filter.check_synthesis_translation(english_punct_translation)
        assert result.status == QAStatus.PASS
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert self.qa_filter.should_display(result) is True

    def test_chinese_punctuation_flag(self):
        """Test Chinese punctuation is flagged."""
        chinese_punct_translation = {
            "id": "test-chinese-punct",
            "title_en": "Machine Learning Study",
            "abstract_en": "This study utilizes machine learning techniques：colons，semicolons；commas，and periods。",
            "body_md": "## Introduction\n\nThis is the body text with Chinese punctuation。The paragraph has sufficient length to pass minimum requirements.",
        }

        result = self.qa_filter.check_synthesis_translation(chinese_punct_translation)
        assert result.status == QAStatus.FLAG_CHINESE
        assert result.score < 1.0
        assert result.chinese_ratio > 0.0
        assert len(result.chinese_chars) > 0

    def test_edge_cases(self):
        """Test various edge cases."""
        # Empty body but valid title/abstract
        empty_body = {
            "id": "test-empty-body",
            "title_en": "Valid Title",
            "abstract_en": "This is a valid abstract with sufficient content.",
            "body_md": "",
        }
        result = self.qa_filter.check_synthesis_translation(empty_body)
        # Should flag due to empty body
        assert any("Body too short" in issue for issue in result.issues)

        # None values
        none_translation = {
            "id": "test-none",
            "title_en": None,
            "abstract_en": None,
            "body_md": None,
        }
        result = self.qa_filter.check_synthesis_translation(none_translation)
        # Should handle gracefully
        assert result is not None

    def test_technical_symbols_pass(self):
        """Test technical symbols and special characters pass."""
        body_md = """## Introduction

This is the body text with technical symbols: ±, ×, ÷, ≠, ≤, ≥, ∞, ∑, ∏, ∫, ∂, ∇. The paragraph has sufficient length to pass the minimum requirements for synthesis QA filter validation.

## Mathematical Framework

We define our model using standard mathematical notation. The objective function includes regularization terms. Convergence analysis shows the algorithm terminates in polynomial time.

## Experimental Setup

The experiments were conducted on a cluster with multiple GPU nodes. Training time averaged 12 hours per model. We used cross-validation to tune hyperparameters systematically."""

        technical_translation = {
            "id": "test-technical",
            "title_en": "Machine Learning Study",
            "abstract_en": "This study uses mathematical symbols: α, β, γ, δ, ε, ζ, η, θ, λ, μ, π, ρ, σ, τ, φ, χ, ψ, ω.",
            "body_md": body_md,
        }

        result = self.qa_filter.check_synthesis_translation(technical_translation)
        assert result.status == QAStatus.PASS
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []

    def test_unicode_edge_cases(self):
        """Test Unicode edge cases."""
        body_md = """## Introduction

This is the body text with Unicode characters like José and François. The paragraph has sufficient length to pass minimum requirements for the synthesis QA filter.

## International Collaboration

Our research team includes members from multiple countries. Contributors include researchers named Müller, Søren, and Çelik. The diversity of perspectives strengthened our methodology.

## Acknowledgments

We thank our international collaborators for their valuable contributions. Special thanks to Dr. José García for early feedback on the manuscript."""

        unicode_translation = {
            "id": "test-unicode",
            "title_en": "Unicode Test Study",
            "abstract_en": "This study includes various Unicode characters: é, ñ, ü, ö, ä, ß, ç, ğ, ş, å, ø, æ.",
            "body_md": body_md,
        }

        result = self.qa_filter.check_synthesis_translation(unicode_translation)
        assert result.status == QAStatus.PASS
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert self.qa_filter.should_display(result) is True

    def test_numbers_and_symbols(self):
        """Test numbers and symbols pass."""
        body_md = """## Introduction

This is the body text with numbers like 1.618, 2.718, 3.14159. The paragraph has sufficient length to pass minimum requirements for synthesis QA.

## Numerical Results

We computed values ranging from 1e-10 to 2.5×10³. The golden ratio φ ≈ 1.618 appears in natural phenomena. Euler's number e ≈ 2.718 is fundamental to calculus.

## Statistical Analysis

Our statistical tests showed p-values below 0.05 threshold. The 95% confidence intervals were narrow. Sample sizes ranged from 100 to 10000."""

        numbers_translation = {
            "id": "test-numbers",
            "title_en": "Numerical Analysis Study",
            "abstract_en": "This study analyzes numerical data: 123, 456.789, 1e-10, 2.5×10³, 3.14×10⁻⁶.",
            "body_md": body_md,
        }

        result = self.qa_filter.check_synthesis_translation(numbers_translation)
        assert result.status == QAStatus.PASS
        assert result.chinese_ratio == 0.0
        assert result.chinese_chars == []
        assert self.qa_filter.should_display(result) is True

    def test_chinese_content_flags(self):
        """Test that Chinese content above threshold flags the translation."""
        # Chinese ratio threshold is 0.5% - we need enough Chinese chars to exceed this
        body_md = """## Introduction

This is the body text. 中文测试 - 这是一段中文内容 - Chinese characters in body.

## Methods

Our methodology is described here briefly.

## Results

Results are presented here."""

        chinese_translation = {
            "id": "test-chinese-content",
            "title_en": "Machine Learning Study",
            "abstract_en": "This study utilizes machine learning. 这里有中文内容",
            "body_md": body_md,
        }

        result = self.qa_filter.check_synthesis_translation(chinese_translation)
        assert result.status == QAStatus.FLAG_CHINESE
        assert result.score < 1.0
        assert result.chinese_ratio > 0.005  # Above 0.5% threshold
        assert len(result.chinese_chars) > 0

    def test_watermark_artifacts_flag(self):
        """Test that watermark artifacts are flagged."""
        watermark_translation = {
            "id": "test-watermark",
            "title_en": "Valid Title",
            "abstract_en": "This is a valid abstract with sufficient content.",
            "body_md": "## Introduction\n\nThis is the body with X a n i h C watermark pattern. The paragraph has sufficient length.",
        }

        result = self.qa_filter.check_synthesis_translation(watermark_translation)
        # Should flag watermark artifacts
        assert any("Watermark" in issue for issue in result.issues)

    def test_math_placeholder_flag(self):
        """Test that unreplaced math placeholders are flagged."""
        math_placeholder_translation = {
            "id": "test-math",
            "title_en": "Valid Title",
            "abstract_en": "This is a valid abstract with sufficient content.",
            "body_md": "## Introduction\n\nThis is the body with ⟪MATH_0001⟫ unreplaced placeholder. The paragraph has sufficient length.",
        }

        result = self.qa_filter.check_synthesis_translation(
            math_placeholder_translation
        )
        # Should flag unreplaced math placeholders
        assert any("math placeholder" in issue.lower() for issue in result.issues)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
