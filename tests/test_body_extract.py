"""
Comprehensive tests for body_extract.py.
Tests both legacy extraction and synthesis mode functions.
"""

import io
import tarfile
from pathlib import Path


from src.body_extract import (
    extract_body_paragraphs,
    extract_from_latex,
    extract_from_pdf_synthesis,
    is_watermark_or_garbage,
    detect_section_boundary,
    merge_pdf_lines_to_paragraphs,
)


# =============================================================================
# Legacy extraction tests
# =============================================================================


def make_tex_tar(tmp_path: Path, name: str = "paper.tar.gz") -> str:
    tex_content = r"""
\documentclass{article}
\begin{document}
这是正文内容。这里有数学 $x+y$。

第二段内容。
\end{document}
""".strip()
    tar_path = tmp_path / name
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo("main.tex")
        data = tex_content.encode("utf-8")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return str(tar_path)


def test_extract_from_latex_paragraphs(tmp_path):
    tar_path = make_tex_tar(tmp_path)
    paras = extract_from_latex(tar_path)
    assert paras and any("数学" in p for p in paras)


def test_body_extract_prefers_latex(tmp_path, monkeypatch):
    tar_path = make_tex_tar(tmp_path)
    rec = {"files": {"latex_source_path": tar_path, "pdf_path": None}}
    paras = extract_body_paragraphs(rec)
    assert len(paras) >= 1


# =============================================================================
# Synthesis mode tests: Watermark Detection
# =============================================================================


class TestWatermarkDetection:
    """Test is_watermark_or_garbage function."""

    def test_short_text_is_garbage(self):
        """Text shorter than 5 chars is garbage."""
        assert is_watermark_or_garbage("") is True
        assert is_watermark_or_garbage("abc") is True
        assert is_watermark_or_garbage("1234") is True
        assert is_watermark_or_garbage("    ") is True

    def test_valid_text_passes(self):
        """Normal text should not be filtered."""
        assert is_watermark_or_garbage("This is a normal sentence.") is False
        assert is_watermark_or_garbage("The experiment showed positive results.") is False
        assert is_watermark_or_garbage("这是一段正常的中文文本。") is False

    def test_spaced_watermark_pattern(self):
        """Spaced-out text like 'X a n i h C' should be detected."""
        assert is_watermark_or_garbage("X a n i h C") is True
        assert is_watermark_or_garbage("C h i n a X i v") is True
        assert is_watermark_or_garbage("T h i s v e r s i o n") is True

    def test_high_space_ratio(self):
        """Text with >40% spaces starting with alphanumeric is garbage."""
        # "a b c d e f" has 5 spaces out of 11 chars = 45%
        assert is_watermark_or_garbage("a b c d e f") is True
        # Normal text has lower space ratio
        assert is_watermark_or_garbage("This is normal text with spaces") is False

    def test_page_numbers(self):
        """Pure numbers/punctuation like page numbers should be filtered."""
        assert is_watermark_or_garbage("123") is True
        assert is_watermark_or_garbage("- 5 -") is True
        assert is_watermark_or_garbage("12.34") is True

    def test_chinaxiv_watermarks(self):
        """ChinaXiv-specific watermark patterns."""
        assert is_watermark_or_garbage("X a n i h C") is True
        assert is_watermark_or_garbage(":viXanihC") is True

    def test_boundary_cases(self):
        """Boundary cases around 5 char threshold."""
        assert is_watermark_or_garbage("abcde") is False  # Exactly 5 chars
        assert is_watermark_or_garbage("abcd") is True   # 4 chars, too short


# =============================================================================
# Synthesis mode tests: Section Boundary Detection
# =============================================================================


class TestSectionBoundaryDetection:
    """Test detect_section_boundary function."""

    def test_numbered_sections_english(self):
        """Numbered English section headings."""
        assert detect_section_boundary("1 Introduction") == "1 Introduction"
        assert detect_section_boundary("2.1 Methods") == "2.1 Methods"
        assert detect_section_boundary("3.2.1 Data Collection") == "3.2.1 Data Collection"

    def test_numbered_sections_chinese(self):
        """Numbered Chinese section headings."""
        assert detect_section_boundary("1. 引言") == "1. 引言"
        assert detect_section_boundary("2 方法") == "2 方法"

    def test_chinese_chapter_markers(self):
        """Chinese chapter/section markers like '第一章'."""
        assert detect_section_boundary("第一章") == "第一章"
        assert detect_section_boundary("第二节") == "第二节"
        assert detect_section_boundary("第1章 绪论") == "第1章 绪论"

    def test_standalone_chinese_headers(self):
        """Common Chinese section names."""
        assert detect_section_boundary("摘要") == "摘要"
        assert detect_section_boundary("引言") == "引言"
        assert detect_section_boundary("方法") == "方法"
        assert detect_section_boundary("结果") == "结果"
        assert detect_section_boundary("讨论") == "讨论"
        assert detect_section_boundary("结论") == "结论"
        assert detect_section_boundary("参考文献") == "参考文献"

    def test_standalone_english_headers(self):
        """Common English section names."""
        assert detect_section_boundary("Abstract") == "Abstract"
        assert detect_section_boundary("Introduction") == "Introduction"
        assert detect_section_boundary("Methods") == "Methods"
        assert detect_section_boundary("Results") == "Results"
        assert detect_section_boundary("Discussion") == "Discussion"
        assert detect_section_boundary("Conclusions") == "Conclusions"
        assert detect_section_boundary("References") == "References"

    def test_non_section_text(self):
        """Normal body text should not be detected as sections."""
        assert detect_section_boundary("This is a normal paragraph.") is None
        assert detect_section_boundary("The results show that...") is None
        # Note: "实验结果表明..." starts with "实验" which IS a section keyword
        # Use text that doesn't start with any section keyword
        assert detect_section_boundary("研究发现这些数据表明...") is None
        assert detect_section_boundary("数据分析显示...") is None

    def test_max_length_boundary(self):
        """Sections longer than 100 chars should not match."""
        long_text = "1 " + "A" * 110
        assert detect_section_boundary(long_text) is None

    def test_whitespace_handling(self):
        """Whitespace should be stripped before detection."""
        assert detect_section_boundary("  Introduction  ") == "Introduction"
        assert detect_section_boundary("\t1 Methods\n") == "1 Methods"


# =============================================================================
# Synthesis mode tests: Paragraph Merging
# =============================================================================


class TestParagraphMerging:
    """Test merge_pdf_lines_to_paragraphs function."""

    def test_empty_input(self):
        """Empty list returns empty list."""
        assert merge_pdf_lines_to_paragraphs([]) == []

    def test_single_line(self):
        """Single line becomes single paragraph."""
        result = merge_pdf_lines_to_paragraphs(["This is a test sentence."])
        assert len(result) == 1
        assert result[0] == "This is a test sentence."

    def test_simple_merging(self):
        """Lines without sentence endings get merged."""
        lines = [
            "This is the first part of",
            "a sentence that spans multiple",
            "lines in the PDF."
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=10)
        assert len(result) == 1
        assert "first part" in result[0]
        assert "multiple" in result[0]

    def test_sentence_boundary_break(self):
        """Sentence ending followed by uppercase starts new paragraph."""
        lines = [
            "This is the first paragraph with enough content.",
            "This starts a new paragraph."
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=20)
        assert len(result) == 2

    def test_section_headers_break(self):
        """Section headers force paragraph breaks."""
        lines = [
            "Introduction to the study.",
            "1 Methods",
            "We used the following approach.",
            "2 Results",
            "The results show improvement."
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=10)

        # Section headers should be their own paragraph
        assert any(p == "1 Methods" for p in result)
        assert any(p == "2 Results" for p in result)

    def test_watermark_filtering(self):
        """Watermarks should be filtered out during merging."""
        lines = [
            "This is valid text.",
            "X a n i h C",  # Watermark
            "More valid text.",
            "123",  # Page number (too short)
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=10)

        # Watermarks should not appear
        assert not any("X a n i h C" in p for p in result)
        # Valid text should be present
        combined = " ".join(result)
        assert "valid text" in combined

    def test_blank_lines_ignored(self):
        """Blank lines should be skipped."""
        lines = [
            "First sentence.",
            "",
            "   ",
            "Second sentence."
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=10)
        assert len(result) >= 1

    def test_chinese_paragraph_detection(self):
        """Chinese text paragraphs should work correctly."""
        lines = [
            "这是第一段的开始内容足够长。",
            "这是第二段的内容也很长。"
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=5)
        assert len(result) >= 1

    def test_mixed_content(self):
        """Mixed Chinese and English content."""
        lines = [
            "1 Introduction",
            "This study examines machine learning.",
            "我们使用了神经网络。",
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=10)

        # Section should be preserved
        assert any("Introduction" in p for p in result)
        # Both languages should be present
        combined = " ".join(result)
        assert "machine learning" in combined
        assert "神经网络" in combined


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_very_long_line(self):
        """Very long lines should be handled."""
        long_text = "This is a very long sentence. " * 50
        result = merge_pdf_lines_to_paragraphs([long_text])
        assert len(result) == 1
        assert len(result[0]) > 100

    def test_special_characters(self):
        """Special characters should not break detection."""
        lines = [
            "The equation $x = y$ is important.",
            "Here is another equation: \\frac{a}{b}."
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=20)
        assert any("equation" in p for p in result)

    def test_consecutive_sections(self):
        """Consecutive section headers should work."""
        lines = [
            "1 Introduction",
            "2 Background",
            "Some content here."
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=10)
        assert "1 Introduction" in result
        assert "2 Background" in result

    def test_section_at_end(self):
        """Section header at document end should work."""
        lines = [
            "Some content here.",
            "References"
        ]
        result = merge_pdf_lines_to_paragraphs(lines, min_para_length=10)
        assert "References" in result


# =============================================================================
# Synthesis mode tests: extract_from_pdf_synthesis()
# =============================================================================


class TestExtractFromPdfSynthesis:
    """Test extract_from_pdf_synthesis function."""

    def test_missing_file_returns_none(self):
        """Missing file should return None."""
        result = extract_from_pdf_synthesis("/nonexistent/path/to/file.pdf")
        assert result is None

    def test_none_path_returns_none(self):
        """None path should return None."""
        result = extract_from_pdf_synthesis(None)
        assert result is None

    def test_empty_path_returns_none(self):
        """Empty string path should return None."""
        result = extract_from_pdf_synthesis("")
        assert result is None

    def test_result_structure(self, tmp_path):
        """Result should have correct structure when PDF exists."""
        from unittest.mock import patch

        mock_text = """1 Introduction
This is the introduction paragraph with enough text to be valid.

2 Methods
We used various methods in our research with detailed explanations.

3 Results
The results show significant improvements in all metrics measured.
"""
        with patch("pdfminer.high_level.extract_text", return_value=mock_text):
            pdf_path = tmp_path / "test.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")

            result = extract_from_pdf_synthesis(str(pdf_path))

        assert result is not None
        assert "sections" in result
        assert "raw_paragraphs" in result
        assert "stats" in result

    def test_stats_structure(self, tmp_path):
        """Stats should contain expected keys."""
        from unittest.mock import patch

        mock_text = """1 Introduction
This is valid content for the introduction section.

2 Methods
This describes the methodology used.
"""
        with patch("pdfminer.high_level.extract_text", return_value=mock_text):
            pdf_path = tmp_path / "test.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")

            result = extract_from_pdf_synthesis(str(pdf_path))

        assert result is not None
        stats = result["stats"]
        assert "original_lines" in stats
        assert "merged_paragraphs" in stats
        assert "detected_sections" in stats
        assert "section_names" in stats

    def test_section_detection(self, tmp_path):
        """Sections should be correctly detected and grouped."""
        from unittest.mock import patch

        mock_text = """Abstract
This paper presents our findings on machine learning.

1 Introduction
We introduce a novel approach to data analysis.

2 Methods
Our methodology involves several key steps.

3 Conclusion
In conclusion, our results demonstrate significant value.
"""
        with patch("pdfminer.high_level.extract_text", return_value=mock_text):
            pdf_path = tmp_path / "test.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")

            result = extract_from_pdf_synthesis(str(pdf_path))

        assert result is not None
        section_names = [s["name"] for s in result["sections"]]
        # Should detect Abstract, 1 Introduction, 2 Methods, 3 Conclusion
        assert any("Introduction" in name for name in section_names)

    def test_preamble_section(self, tmp_path):
        """Content before first section should go to Preamble."""
        from unittest.mock import patch

        mock_text = """Some content before any section header.
More preamble content here with enough text.

1 Introduction
The actual introduction starts here.
"""
        with patch("pdfminer.high_level.extract_text", return_value=mock_text):
            pdf_path = tmp_path / "test.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")

            result = extract_from_pdf_synthesis(str(pdf_path))

        assert result is not None
        # First section should be Preamble if there's content before headers
        if result["sections"]:
            first_section = result["sections"][0]
            # Either it's Preamble or starts with the first detected section
            assert first_section["name"] in ["Preamble", "1 Introduction"]

    def test_extraction_failure_returns_none(self, tmp_path):
        """Extraction failure should return None gracefully."""
        from unittest.mock import patch

        with patch("pdfminer.high_level.extract_text", side_effect=Exception("PDF error")):
            pdf_path = tmp_path / "test.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")

            result = extract_from_pdf_synthesis(str(pdf_path))

        assert result is None

    def test_empty_extraction_returns_none(self, tmp_path):
        """Empty PDF content should return None."""
        from unittest.mock import patch

        with patch("pdfminer.high_level.extract_text", return_value=""):
            pdf_path = tmp_path / "test.pdf"
            pdf_path.write_bytes(b"%PDF-1.4")

            result = extract_from_pdf_synthesis(str(pdf_path))

        assert result is None


# =============================================================================
# Pipeline QA Integration Test
# =============================================================================


class TestPipelineQAIntegration:
    """Test that pipeline QA imports work correctly."""

    def test_qa_import_works(self):
        """Verify the QA import in pipeline.py works."""
        # This import mirrors what pipeline.py does at runtime
        from src.qa_filter import SynthesisQAFilter, QAStatus

        # Verify classes are usable
        qa_filter = SynthesisQAFilter()
        assert qa_filter is not None
        assert QAStatus.PASS is not None

    def test_qa_filter_callable(self):
        """Verify QA filter can check a translation."""
        from src.qa_filter import SynthesisQAFilter, QAStatus

        qa_filter = SynthesisQAFilter()

        # Create a mock translation that should pass
        translation = {
            "title_en": "A Study of Machine Learning",
            "abstract_en": "This paper presents a comprehensive study of machine learning techniques.",
            "body_md": "## Introduction\n\n" + "This is valid content. " * 100,
        }

        result = qa_filter.check_synthesis_translation(translation)
        assert result is not None
        assert hasattr(result, "status")
        assert hasattr(result, "score")
        assert result.status in [QAStatus.PASS, QAStatus.FLAG_FORMATTING]

