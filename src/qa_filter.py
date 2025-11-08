"""
Quality Assurance filter for detecting Chinese characters in translations.
Automatically flags translations containing Chinese characters for manual review.
"""

from typing import Dict, List, Any, Tuple
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
        if char in self.CHINESE_PUNCTUATION:
            return True

        return False

    def is_chinese_ideograph(self, char: str) -> bool:
        """Check if a character is a Chinese ideograph (excluding punctuation)."""
        if not char:
            return False

        code_point = ord(char)

        # Check Chinese character ranges (CJK ideographs only)
        for start, end in self.CHINESE_RANGES:
            if start <= code_point <= end:
                return True

        return False

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

        for marker in self.CHINESE_METADATA_MARKERS:
            if marker in text:
                return True

        return False


class TranslationQAFilter:
    """Quality assurance filter for translations."""

    def __init__(self):
        self.detector = ChineseCharacterDetector()

        # QA thresholds - Very strict to avoid false positives
        self.MAX_CHINESE_IDEOGRAPH_RATIO = (
            0.001  # 0.1% max Chinese ideographs (any Chinese characters)
        )
        self.MAX_CHINESE_PUNCTUATION_RATIO = 0.01  # 1% max Chinese punctuation
        self.MIN_ABSTRACT_LENGTH = 50
        self.MAX_METADATA_RATIO = 0.3  # 30% max metadata content
        # New content sanity checks
        self.MIN_ENGLISH_LETTERS_START = 3  # require some English letters near the start
        self.TRAILING_WINDOW = 300  # characters to inspect at the end

    def check_translation(self, translation: Dict[str, Any]) -> QAResult:
        """
        Perform comprehensive QA check on a translation.

        Args:
            translation: Translation dictionary with fields like 'abstract_en', 'body_en', etc.

        Returns:
            QAResult with status, score, and issues
        """
        issues = []
        flagged_fields = []
        chinese_chars = []
        total_chinese_ratio = 0.0

        # Check each text field
        text_fields = ["abstract_en", "title_en", "body_en"]

        for field in text_fields:
            if field not in translation:
                continue

            field_value = translation[field]

            # Handle different field types
            if isinstance(field_value, str):
                field_text = field_value
            elif isinstance(field_value, list):
                field_text = " ".join(str(item) for item in field_value)
            else:
                field_text = str(field_value) if field_value else ""

            if not field_text:
                continue

            # Check for Chinese characters
            field_chinese_chars = self.detector.find_chinese_chars(field_text)
            field_chinese_ratio = self.detector.calculate_chinese_ratio(field_text)
            field_ideograph_ratio = self.detector.calculate_chinese_ideograph_ratio(
                field_text
            )

            if field_chinese_chars:
                chinese_chars.extend(field_chinese_chars)

                # Flag if ANY Chinese ideographs found (very strict)
                if field_ideograph_ratio > self.MAX_CHINESE_IDEOGRAPH_RATIO:
                    flagged_fields.append(field)
                    issues.append(
                        f"{field} contains Chinese characters: {field_chinese_chars[:5]}"
                    )

                # Flag if excessive Chinese punctuation
                elif field_chinese_ratio > self.MAX_CHINESE_PUNCTUATION_RATIO:
                    flagged_fields.append(field)
                    issues.append(
                        f"{field} contains excessive Chinese punctuation: {field_chinese_chars[:5]}"
                    )

            # Check for Chinese metadata markers
            if self.detector.has_chinese_metadata(field_text):
                issues.append(f"{field} contains Chinese metadata markers")
                flagged_fields.append(field)

            # Check abstract length
            if field == "abstract_en" and len(field_text) < self.MIN_ABSTRACT_LENGTH:
                issues.append(f"{field} is too short ({len(field_text)} chars)")
                flagged_fields.append(field)

        # Additional content sanity checks:
        # 1) Start with real English content: ensure early part contains letters
        def _has_english_letters(text: str, count: int = 3) -> bool:
            import re
            if count < 1:
                return True
            # Build pattern: [A-Za-z] followed by (count-1) instances of .*[A-Za-z]
            pattern = r"[A-Za-z]" + (r".*[A-Za-z]" * (count - 1))
            return bool(re.search(pattern, (text or "")[:200]))

        title_en = translation.get("title_en") or ""
        abstract_en = translation.get("abstract_en") or ""
        first_body_para = None
        body_en = translation.get("body_en")
        if isinstance(body_en, list) and body_en:
            first_body_para = next((p for p in body_en if isinstance(p, str) and p.strip()), "")
        
        # Check starts
        if title_en and not _has_english_letters(title_en, self.MIN_ENGLISH_LETTERS_START):
            issues.append("title_en does not start with real English content")
            flagged_fields.append("title_en")
        if abstract_en and not _has_english_letters(abstract_en, self.MIN_ENGLISH_LETTERS_START):
            issues.append("abstract_en does not start with real English content")
            flagged_fields.append("abstract_en")
        if first_body_para and not _has_english_letters(first_body_para, self.MIN_ENGLISH_LETTERS_START):
            issues.append("first body_en paragraph lacks real English content")
            flagged_fields.append("body_en")

        # 2) No Chinese at the end: inspect trailing window across all English text
        all_text = " ".join(
            str(translation.get(field, ""))
            for field in text_fields
            if translation.get(field)
        )
        trailing = all_text[-self.TRAILING_WINDOW :]
        trailing_chinese = self.detector.find_chinese_chars(trailing)
        if trailing and trailing_chinese:
            issues.append("trailing Chinese characters detected in translation end")
            flagged_fields.append("body_en")

        # Calculate overall Chinese ratios
        all_text = " ".join(
            str(translation.get(field, ""))
            for field in text_fields
            if translation.get(field)
        )
        total_chinese_ratio = self.detector.calculate_chinese_ratio(all_text)
        total_ideograph_ratio = self.detector.calculate_chinese_ideograph_ratio(
            all_text
        )

        # Determine status
        if (
            total_ideograph_ratio > self.MAX_CHINESE_IDEOGRAPH_RATIO
            or total_chinese_ratio > self.MAX_CHINESE_PUNCTUATION_RATIO
            or self.detector.has_chinese_metadata(all_text)
        ):
            status = QAStatus.FLAG_CHINESE
        elif len(issues) > 0:
            # Prefer formatting for punctuation-only or trailing-window findings
            # to keep FLAG_CONTENT reserved for more severe content issues we may add later.
            if any("real English content" in msg for msg in issues):
                status = QAStatus.FLAG_CONTENT
            else:
                status = QAStatus.FLAG_FORMATTING
        else:
            status = QAStatus.PASS

        # Calculate score (0.0 to 1.0, higher is better)
        score = max(0.0, 1.0 - total_chinese_ratio - (len(issues) * 0.1))

        return QAResult(
            status=status,
            score=score,
            issues=issues,
            chinese_chars=list(set(chinese_chars)),
            chinese_ratio=total_chinese_ratio,
            flagged_fields=list(set(flagged_fields)),
        )

    def should_display(self, qa_result: QAResult) -> bool:
        """Determine if translation should be displayed on the site."""
        return qa_result.status == QAStatus.PASS

    def should_flag_for_review(self, qa_result: QAResult) -> bool:
        """Determine if translation should be flagged for manual review."""
        return qa_result.status in [QAStatus.FLAG_CHINESE, QAStatus.FLAG_FORMATTING]


def filter_translations(
    translations: List[Dict[str, Any]], flag_for_review: bool = True
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Filter translations based on QA criteria.

    Args:
        translations: List of translation dictionaries
        flag_for_review: If True, save flagged translations for review

    Returns:
        Tuple of (passed_translations, flagged_translations)
    """
    qa_filter = TranslationQAFilter()
    passed = []
    flagged = []

    for translation in translations:
        qa_result = qa_filter.check_translation(translation)

        if qa_filter.should_display(qa_result):
            passed.append(translation)
        else:
            flagged.append(translation)

            # Add QA metadata to flagged translation
            translation["_qa_status"] = qa_result.status.value
            translation["_qa_score"] = qa_result.score
            translation["_qa_issues"] = qa_result.issues
            translation["_qa_chinese_chars"] = qa_result.chinese_chars
            translation["_qa_chinese_ratio"] = qa_result.chinese_ratio
            translation["_qa_flagged_fields"] = qa_result.flagged_fields

    return passed, flagged


def analyze_translation_quality(translation_path: str) -> QAResult:
    """
    Analyze a single translation file for quality issues.

    Args:
        translation_path: Path to translation JSON file

    Returns:
        QAResult with analysis
    """
    import json

    with open(translation_path, "r", encoding="utf-8") as f:
        translation = json.load(f)

    qa_filter = TranslationQAFilter()
    return qa_filter.check_translation(translation)


def filter_translation_file(
    translation: Dict[str, Any],
    save_passed: bool = True,
    save_flagged: bool = True,
) -> Tuple[bool, QAResult]:
    """
    Filter a single translation and optionally save to appropriate directory.

    Args:
        translation: Translation dictionary
        save_passed: If True, save passed translations to data/translated/
        save_flagged: If True, save flagged translations to data/flagged/

    Returns:
        Tuple of (should_publish: bool, qa_result: QAResult)
    """
    import json
    from pathlib import Path

    qa_filter = TranslationQAFilter()
    qa_result = qa_filter.check_translation(translation)

    paper_id = translation.get("id", "unknown")
    should_publish = qa_filter.should_display(qa_result)

    if should_publish and save_passed:
        # Save to data/translated/
        output_dir = Path("data/translated")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{paper_id}.json"

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(translation, f, indent=2, ensure_ascii=False)

    elif not should_publish and save_flagged:
        # Save to data/flagged/ with QA metadata
        output_dir = Path("data/flagged")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{paper_id}_flagged.json"

        # Add QA metadata
        translation["_qa_status"] = qa_result.status.value
        translation["_qa_score"] = qa_result.score
        translation["_qa_issues"] = qa_result.issues
        translation["_qa_chinese_chars"] = qa_result.chinese_chars
        translation["_qa_chinese_ratio"] = qa_result.chinese_ratio
        translation["_qa_flagged_fields"] = qa_result.flagged_fields

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(translation, f, indent=2, ensure_ascii=False)

    return should_publish, qa_result


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) > 1:
        translation_path = sys.argv[1]
        result = analyze_translation_quality(translation_path)

        print(f"QA Status: {result.status.value}")
        print(f"Score: {result.score:.2f}")
        print(f"Chinese Ratio: {result.chinese_ratio:.2%}")
        print(f"Chinese Characters: {result.chinese_chars}")
        print(f"Issues: {result.issues}")
        print(f"Flagged Fields: {result.flagged_fields}")
        print(f"Should Display: {TranslationQAFilter().should_display(result)}")
    else:
        print("Usage: python qa_filter.py <translation_file.json>")
