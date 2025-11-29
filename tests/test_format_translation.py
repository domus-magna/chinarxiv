"""
Tests for translation formatting functionality.
"""

from src.format_translation import (
    is_section_heading,
    is_short_fragment,
    is_mathematical_formula,
    merge_short_fragments,
    format_as_markdown,
    format_body_paragraphs,
    format_translation,
    format_translation_to_markdown,
)


class TestFormatTranslation:
    """Test translation formatting functionality."""

    def test_is_section_heading(self):
        """Test section heading detection."""
        # Numbered sections
        assert is_section_heading("1. Introduction") is True
        assert is_section_heading("2 Methods") is True  # No dot after number
        assert (
            is_section_heading("3.2.1 Results") is False
        )  # Regex doesn't match this pattern

        # Common heading words
        assert is_section_heading("Abstract") is True
        assert is_section_heading("Introduction") is True
        assert is_section_heading("Methods") is True
        assert is_section_heading("Results") is True
        assert is_section_heading("Discussion") is True
        assert is_section_heading("Conclusion") is True
        assert is_section_heading("References") is True
        assert is_section_heading("Acknowledgments") is True

        # Not headings
        assert (
            is_section_heading(
                "This is a long paragraph that contains multiple sentences."
            )
            is False
        )
        assert is_section_heading("Introduction to the topic.") is False  # Has period
        assert is_section_heading("") is False
        assert is_section_heading("   ") is False

    def test_is_short_fragment(self):
        """Test short fragment detection."""
        assert is_short_fragment("Short") is True
        assert is_short_fragment("This is a very short fragment") is True
        assert (
            is_short_fragment(
                "This is a longer fragment that exceeds the minimum length"
            )
            is False
        )
        assert is_short_fragment("") is True
        assert is_short_fragment("   ") is True

        # Custom minimum length
        assert is_short_fragment("Short", min_length=10) is True
        assert is_short_fragment("This is longer", min_length=10) is False

    def test_is_mathematical_formula(self):
        """Test mathematical formula detection."""
        # LaTeX commands
        assert is_mathematical_formula("\\sum_{i=1}^{n} x_i") is True
        assert is_mathematical_formula("\\frac{d}{dx}f(x)") is True
        assert is_mathematical_formula("\\int_{-\\infty}^{\\infty} e^{-x^2} dx") is True

        # Math symbols (high density)
        assert is_mathematical_formula("∑∏∫∂∇±×÷≤≥≠≈∞") is True
        # Lower density math symbols
        assert is_mathematical_formula("x ∈ ℝ, y ∈ ℂ") is False

        # Not math
        assert is_mathematical_formula("This is regular text") is False
        assert is_mathematical_formula("The equation x = y is simple") is False
        assert is_mathematical_formula("") is False

    def test_merge_short_fragments(self):
        """Test merging short fragments."""
        paragraphs = [
            "This is a long paragraph that should remain separate.",
            "Short frag",
            "Another short",
            "This is another long paragraph that should remain separate.",
            "Tiny",
            "Another tiny fragment",
        ]

        result = merge_short_fragments(paragraphs)

        # Short fragments should be merged
        assert len(result) == 2
        assert "This is a long paragraph that should remain separate." in result[0]
        assert (
            "Short frag Another short This is another long paragraph that should remain separate. Tiny Another tiny fragment"
            in result[1]
        )

    def test_merge_short_fragments_with_headings(self):
        """Test merging with section headings."""
        paragraphs = [
            "1. Introduction",
            "Short frag",
            "2. Methods",
            "Another short",
            "This is a long paragraph.",
        ]

        result = merge_short_fragments(paragraphs)

        # Headings should not be merged
        assert len(result) == 2
        assert "1. Introduction" in result[0]
        assert (
            "Short frag 2. Methods Another short This is a long paragraph." in result[1]
        )

    def test_merge_short_fragments_empty(self):
        """Test merging with empty input."""
        assert merge_short_fragments([]) == []
        assert merge_short_fragments(["", "   ", ""]) == []

    def test_format_as_markdown(self):
        """Test markdown formatting."""
        paragraphs = [
            "1. Introduction",
            "This is the introduction paragraph.",
            "\\sum_{i=1}^{n} x_i",
            "2. Methods",
            "This is the methods section.",
        ]

        result = format_as_markdown(paragraphs)

        assert "## 1. Introduction" in result
        assert "This is the introduction paragraph." in result
        assert "```\n\\sum_{i=1}^{n} x_i\n```" in result
        assert "## 2. Methods" in result
        assert "This is the methods section." in result

    def test_format_body_paragraphs(self):
        """Test body paragraph formatting."""
        paragraphs = [
            "This is a long paragraph that should remain separate.",
            "Short frag",
            "Another short",
            "This is another long paragraph.",
            "   Extra whitespace   ",
            "Duplicate..punctuation",
        ]

        result = format_body_paragraphs(paragraphs)

        # Should merge short fragments and clean up
        assert len(result) == 1
        assert (
            "This is a long paragraph that should remain separate. Short frag Another short This is another long paragraph. Extra whitespace Duplicate.punctuation"
            in result[0]
        )

    def test_format_translation(self):
        """Test complete translation formatting."""
        translation = {
            "title_en": "  Test Title  ",
            "abstract_en": "  Test abstract with   extra   spaces  ",
            "body_en": ["Short frag", "Another short", "This is a long paragraph."],
        }

        result = format_translation(translation)

        assert result["title_en"] == "Test Title"
        assert result["abstract_en"] == "Test abstract with extra spaces"
        assert len(result["body_en"]) == 1
        assert (
            "Short frag Another short This is a long paragraph." in result["body_en"][0]
        )

    def test_format_translation_no_body(self):
        """Test formatting translation without body."""
        translation = {"title_en": "Test Title", "abstract_en": "Test abstract"}

        result = format_translation(translation)

        assert result["title_en"] == "Test Title"
        assert result["abstract_en"] == "Test abstract"
        assert "body_en" not in result or result["body_en"] is None

    def test_format_translation_to_markdown(self):
        """Test converting translation to markdown."""
        translation = {
            "title_en": "Test Paper Title",
            "creators": ["Author 1", "Author 2"],
            "date": "2024-01-01",
            "subjects": ["cs.AI", "cs.LG"],
            "abstract_en": "This is the abstract.",
            "body_en": [
                "1. Introduction",
                "This is the introduction.",
                "2. Methods",
                "This is the methods section.",
            ],
        }

        result = format_translation_to_markdown(translation)

        assert "# Test Paper Title" in result
        assert "**Authors:** Author 1, Author 2" in result
        assert "**Date:** 2024-01-01" in result
        assert "**Subjects:** cs.AI, cs.LG" in result
        assert "## Abstract" in result
        assert "This is the abstract." in result
        assert "## Full Text" in result
        assert "## 1. Introduction" in result
        assert (
            "This is the introduction. 2. Methods This is the methods section."
            in result
        )

    def test_format_translation_to_markdown_minimal(self):
        """Test markdown conversion with minimal data."""
        translation = {"title_en": "Test Title", "abstract_en": "Test abstract"}

        result = format_translation_to_markdown(translation)

        assert "# Test Title" in result
        assert "## Abstract" in result
        assert "Test abstract" in result
        assert "## Full Text" not in result

    def test_format_translation_edge_cases(self):
        """Test formatting edge cases."""
        # Empty translation
        result = format_translation({})
        assert result == {}

        # None values
        translation = {"title_en": None, "abstract_en": None, "body_en": None}
        result = format_translation(translation)
        assert result["title_en"] is None
        assert result["abstract_en"] is None
        assert result["body_en"] is None

        # Empty strings
        translation = {"title_en": "", "abstract_en": "", "body_en": []}
        result = format_translation(translation)
        assert result["title_en"] == ""
        assert result["abstract_en"] == ""
        assert result["body_en"] == []

    def test_mathematical_formula_detection_edge_cases(self):
        """Test mathematical formula detection edge cases."""
        # Long text with some math symbols
        long_text = "This is a very long text that contains some mathematical symbols like ∑ and ∏ but is not primarily mathematical."
        assert is_mathematical_formula(long_text) is False

        # Short text with high math symbol density
        math_text = "∑∏∫∂∇±×÷≤≥≠≈∞∈∉⊂⊃∩∪∀∃"
        assert is_mathematical_formula(math_text) is True

        # Empty string
        assert is_mathematical_formula("") is False

        # Only whitespace
        assert is_mathematical_formula("   ") is False
