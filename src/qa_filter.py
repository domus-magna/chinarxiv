"""
Quality Assurance filter for detecting Chinese characters in translations.
Automatically flags translations containing Chinese characters for manual review.
"""

import re
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum


class QAStatus(Enum):
    """Quality assurance status."""

    PASS = "pass"
    FLAG_CHINESE = "flag_chinese"
    FLAG_FORMATTING = "flag_formatting"
    FLAG_LENGTH = "flag_length"
    FLAG_MATH = "flag_math"
    FLAG_CONTENT = "flag_content"


@dataclass
class QAResult:
    """Result of quality assurance check."""

    status: QAStatus
    score: float  # 0.0 to 1.0, higher is better
    issues: List[str]
    chinese_chars: List[str]
    chinese_ratio: float
    flagged_fields: List[str]


class ChineseCharacterDetector:
    """Detects Chinese characters in text."""

    # Chinese character ranges in Unicode
    CHINESE_RANGES = [
        (0x4E00, 0x9FFF),  # CJK Unified Ideographs
        (0x3400, 0x4DBF),  # CJK Extension A
        (0x20000, 0x2A6DF),  # CJK Extension B
        (0x2A700, 0x2B73F),  # CJK Extension C
        (0x2B740, 0x2B81F),  # CJK Extension D
        (0x2B820, 0x2CEAF),  # CJK Extension E
        (0x2CEB0, 0x2EBEF),  # CJK Extension F
        (0x30000, 0x3134F),  # CJK Extension G
    ]

    # Chinese-specific punctuation (distinct from English)
    CHINESE_PUNCTUATION = [
        "：",
        "；",
        "，",
        "。",
        "！",
        "？",
        "（",
        "）",
        "【",
        "】",
        "《",
        "》",
        "、",
        "…",
        "～",
    ]

    # Chinese metadata markers that should not appear in translations
    CHINESE_METADATA_MARKERS = [
        "作者：",
        "提交时间：",
        "摘要:",
        "分类：",
        "引用：",
        "DOI:",
        "CSTR:",
        "推荐引用方式：",
        "版本历史",
        "下载全文",
        "来自：",
        "关键词",
        "摘要：",
        "标题：",
        "日期：",
        "来源：",
        "期刊：",
        "状态：",
        "接收时间：",
    ]

    def is_chinese_char(self, char: str) -> bool:
        """Check if a character is Chinese."""
        if not char:
            return False

        code_point = ord(char)

        # Check Chinese character ranges (CJK ideographs)
        for start, end in self.CHINESE_RANGES:
            if start <= code_point <= end:
                return True

        # Check Chinese-specific punctuation (distinct from English)
        return char in self.CHINESE_PUNCTUATION

    def is_chinese_ideograph(self, char: str) -> bool:
        """Check if a character is a Chinese ideograph (excluding punctuation)."""
        if not char:
            return False

        code_point = ord(char)

        # Check Chinese character ranges (CJK ideographs only)
        return any(start <= code_point <= end for start, end in self.CHINESE_RANGES)

    def find_chinese_chars(self, text: str) -> List[str]:
        """Find all Chinese characters in text."""
        if not text:
            return []

        chinese_chars = []
        for char in text:
            if self.is_chinese_char(char):
                chinese_chars.append(char)

        return list(set(chinese_chars))  # Remove duplicates

    def calculate_chinese_ratio(self, text: str) -> float:
        """Calculate ratio of Chinese characters to total characters."""
        if not text:
            return 0.0

        chinese_count = sum(1 for char in text if self.is_chinese_char(char))
        total_count = len(text)

        return chinese_count / total_count if total_count > 0 else 0.0

    def calculate_chinese_ideograph_ratio(self, text: str) -> float:
        """Calculate ratio of Chinese ideographs to total characters (excluding punctuation)."""
        if not text:
            return 0.0

        ideograph_count = sum(1 for char in text if self.is_chinese_ideograph(char))
        total_count = len(text)

        return ideograph_count / total_count if total_count > 0 else 0.0

    def has_chinese_metadata(self, text: str) -> bool:
        """Check if text contains Chinese metadata markers."""
        if not text:
            return False

        return any(marker in text for marker in self.CHINESE_METADATA_MARKERS)


class SynthesisQAFilter:
    """
    Quality filter for synthesis mode translations.

    This filter checks for:
    - Content completeness (reasonable length)
    - Section presence (academic structure)
    - Readability (no excessive fragmentation)
    - No Chinese leakage
    - No watermark artifacts
    """

    def __init__(self):
        self.detector = ChineseCharacterDetector()

        # Synthesis-specific thresholds
        self.MIN_BODY_LENGTH = 500  # Minimum characters for body
        self.MIN_SECTIONS = 0  # Allow papers with no detected sections (preamble only)
        self.MAX_CHINESE_RATIO = 0.005  # 0.5% tolerance (some papers have Chinese refs)
        self.MIN_AVG_SENTENCE_LENGTH = 10  # Detect fragmentation
        self.MAX_WATERMARK_PATTERN_COUNT = 0  # No watermarks allowed
        self.MAX_TITLE_LENGTH = 300  # Titles should never be body-sized

    # Known watermark patterns to check in output
    WATERMARK_OUTPUT_PATTERNS = [
        re.compile(r"[A-Za-z]\s+[A-Za-z]\s+[A-Za-z]\s+[A-Za-z]"),  # Spaced letters
        re.compile(r"X\s*a\s*n\s*i\s*h\s*C", re.IGNORECASE),
        re.compile(r"\d\s+v\s+\d"),
    ]

    def check_synthesis_translation(
        self,
        translation: Dict[str, Any],
        source_stats: Dict[str, Any] = None,
    ) -> QAResult:
        """
        Check synthesis translation for quality.

        Args:
            translation: Translation dict with 'body_md', 'title_en', 'abstract_en'
            source_stats: Optional extraction statistics for comparison

        Returns:
            QAResult with status and issues
        """
        issues: List[str] = []
        flagged_fields: List[str] = []

        body_md = translation.get("body_md", "") or ""
        title_en = translation.get("title_en", "") or ""
        abstract_en = translation.get("abstract_en", "") or ""

        # Combine all text for overall checks
        all_text = f"{title_en}\n{abstract_en}\n{body_md}"

        # 1. Content length check
        if len(body_md) < self.MIN_BODY_LENGTH:
            issues.append(
                f"Body too short: {len(body_md)} chars (min: {self.MIN_BODY_LENGTH})"
            )
            flagged_fields.append("body_md")

        # 2. Section presence check (informational only)
        section_count = len(re.findall(r"^##\s+", body_md, re.MULTILINE))
        if section_count < self.MIN_SECTIONS:
            issues.append(f"Few sections detected: {section_count}")

        # 3. Chinese character check
        chinese_chars = self.detector.find_chinese_chars(all_text)
        chinese_ratio = self.detector.calculate_chinese_ratio(all_text)
        if chinese_ratio > self.MAX_CHINESE_RATIO:
            issues.append(f"Chinese content detected: {chinese_ratio:.3%}")
            if self.detector.find_chinese_chars(body_md):
                flagged_fields.append("body_md")
            if self.detector.find_chinese_chars(title_en):
                flagged_fields.append("title_en")
            if self.detector.find_chinese_chars(abstract_en):
                flagged_fields.append("abstract_en")

        # 4. Fragmentation check (average sentence length)
        sentences = re.split(r"[.!?。！？]\s+", body_md)
        sentences = [s for s in sentences if s.strip() and len(s.strip()) > 3]
        if sentences:
            avg_sentence_len = sum(len(s) for s in sentences) / len(sentences)
            if avg_sentence_len < self.MIN_AVG_SENTENCE_LENGTH:
                issues.append(
                    f"High fragmentation: avg sentence {avg_sentence_len:.1f} chars"
                )

        # 5. Watermark artifact check
        watermark_count = 0
        for pattern in self.WATERMARK_OUTPUT_PATTERNS:
            matches = pattern.findall(body_md)
            watermark_count += len(matches)
        if watermark_count > self.MAX_WATERMARK_PATTERN_COUNT:
            issues.append(f"Watermark artifacts detected: {watermark_count} patterns")
            flagged_fields.append("body_md")

        # 6. Unreplaced math placeholder check
        if "⟪MATH_" in body_md:
            issues.append("Unreplaced math placeholders found")
            flagged_fields.append("body_md")

        # 7. Title/abstract validation
        if len(title_en) < 5:
            issues.append("Title too short or missing")
            flagged_fields.append("title_en")

        # Catch cases where a whole-paper blob (often wrapped in <PARA>) gets written into title_en.
        # This should be treated as a QA failure so it never lands in validated artifacts.
        if "<para" in title_en.lower():
            issues.append("Title contains PARA wrapper tags")
            flagged_fields.append("title_en")

        if len(title_en) > self.MAX_TITLE_LENGTH:
            issues.append(f"Title too long: {len(title_en)} chars (max: {self.MAX_TITLE_LENGTH})")
            flagged_fields.append("title_en")

        if len(abstract_en) < 20:
            issues.append("Abstract too short or missing")
            flagged_fields.append("abstract_en")

        # Determine status
        if chinese_ratio > self.MAX_CHINESE_RATIO:
            status = QAStatus.FLAG_CHINESE
        elif watermark_count > 0 or len(issues) >= 3:
            status = QAStatus.FLAG_CONTENT
        elif issues:
            status = QAStatus.FLAG_FORMATTING
        else:
            status = QAStatus.PASS

        # Calculate score
        score = max(0.0, 1.0 - chinese_ratio - (len(issues) * 0.1))

        return QAResult(
            status=status,
            score=score,
            issues=issues,
            chinese_chars=chinese_chars[:20],  # Limit to first 20
            chinese_ratio=chinese_ratio,
            flagged_fields=list(set(flagged_fields)),
        )

    def should_display(self, result: QAResult) -> bool:
        """
        Determine if a synthesis translation should be displayed.

        More lenient threshold - we want readable content to show.
        """
        # Only block if Chinese ratio is very high
        if result.chinese_ratio > 0.01:  # 1%
            return False
        # Block if watermarks detected
        return not (result.status == QAStatus.FLAG_CONTENT and "Watermark" in str(result.issues))


if __name__ == "__main__":
    # Example usage
    import sys
    import json

    if len(sys.argv) > 1:
        translation_path = sys.argv[1]
        with open(translation_path, "r", encoding="utf-8") as f:
            translation = json.load(f)

        qa_filter = SynthesisQAFilter()
        result = qa_filter.check_synthesis_translation(translation)

        print(f"QA Status: {result.status.value}")
        print(f"Score: {result.score:.2f}")
        print(f"Chinese Ratio: {result.chinese_ratio:.2%}")
        print(f"Chinese Characters: {result.chinese_chars}")
        print(f"Issues: {result.issues}")
        print(f"Flagged Fields: {result.flagged_fields}")
        print(f"Should Display: {qa_filter.should_display(result)}")
    else:
        print("Usage: python qa_filter.py <translation_file.json>")
