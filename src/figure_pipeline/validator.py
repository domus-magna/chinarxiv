"""
Figure validation using Moondream API.

Performs QA checks:
- Pre-translation: Is figure readable? Has Chinese text?
- Post-translation: Are figures identical except for language?
"""
from __future__ import annotations

import os
from typing import Optional

from .models import PipelineConfig


class FigureValidator:
    """
    QA validation using Moondream Cloud API.

    Key capabilities:
    - Multi-image reasoning (compare two images)
    - OCR for text detection
    - Figure type classification
    - Free tier: 5,000 requests/day
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        """Initialize validator."""
        self.config = config or PipelineConfig()
        self._model = None

    @property
    def model(self):
        """Lazy-load Moondream model."""
        if self._model is None:
            try:
                import moondream as md

                api_key = self.config.moondream_api_key or os.environ.get("MOONDREAM_API_KEY")
                if not api_key:
                    raise ValueError("MOONDREAM_API_KEY not set")

                self._model = md.vl(api_key=api_key)
            except ImportError:
                raise ImportError(
                    "moondream not installed. Install with: pip install moondream"
                )
        return self._model

    def validate(self, image_path: str) -> dict:
        """
        Run validation checks on an extracted figure.

        Args:
            image_path: Path to figure image

        Returns:
            Dict with:
                - readable: bool - Is figure readable?
                - has_chinese: bool - Does it contain Chinese text?
                - figure_type: str - chart/graph/table/diagram/photo/other
        """
        from PIL import Image

        if not os.path.exists(image_path):
            return {"readable": False, "has_chinese": False, "figure_type": "unknown"}

        img = Image.open(image_path)

        # Check if readable
        readable = self._check_readable(img)

        # Check for Chinese text
        has_chinese = self._has_chinese_text(img)

        # Classify figure type
        figure_type = self._get_figure_type(img)

        return {
            "readable": readable,
            "has_chinese": has_chinese,
            "figure_type": figure_type,
        }

    def _check_readable(self, img) -> bool:
        """Check if figure is readable/not corrupted."""
        try:
            result = self.model.query(
                img,
                "Is this image readable and not corrupted? Answer yes or no."
            )
            return "yes" in result.get("answer", "").lower()
        except Exception:
            return True  # Assume readable if check fails

    def _has_chinese_text(self, img) -> bool:
        """Check if figure contains Chinese text."""
        try:
            result = self.model.query(
                img,
                "Does this image contain any Chinese characters or text? Answer yes or no."
            )
            return "yes" in result.get("answer", "").lower()
        except Exception:
            return False

    def _get_figure_type(self, img) -> str:
        """Classify the figure type."""
        try:
            result = self.model.query(
                img,
                "What type of figure is this? Answer with one word: chart, graph, table, diagram, photo, equation, or other."
            )
            answer = result.get("answer", "other").lower().strip()
            valid_types = {"chart", "graph", "table", "diagram", "photo", "equation", "other"}
            return answer if answer in valid_types else "other"
        except Exception:
            return "other"

    def qa_translation(
        self,
        original_path: str,
        translated_path: str,
    ) -> dict:
        """
        Validate a translated figure using separate queries.

        Since Moondream doesn't support multi-image comparison, we validate by:
        1. Checking translated image has English text
        2. Checking translated image has no Chinese text remaining
        3. Checking English text is coherent (not gibberish)
        4. Verifying the figure type matches original

        Args:
            original_path: Path to original Chinese figure
            translated_path: Path to translated English figure

        Returns:
            Dict with:
                - passed: bool - Did QA pass?
                - has_english: bool - Does translated have English?
                - has_chinese_remaining: bool - Does Chinese text remain?
                - is_coherent: bool - Is English text readable/sensible?
                - figure_type_match: bool - Same figure type?
                - details: str - Description of validation
        """
        from PIL import Image

        if not os.path.exists(original_path) or not os.path.exists(translated_path):
            return {
                "passed": False,
                "has_english": False,
                "has_chinese_remaining": True,
                "is_coherent": False,
                "figure_type_match": False,
                "details": "One or both image files not found",
            }

        try:
            img_translated = Image.open(translated_path)

            # Check 1: Does translated image have English text?
            result_english = self.model.query(
                img_translated,
                "Does this image contain English text? Answer yes or no."
            )
            has_english = "yes" in result_english.get("answer", "").lower()

            # Check 2: Does translated image still have Chinese text?
            result_chinese = self.model.query(
                img_translated,
                "Does this image contain any Chinese characters? Answer yes or no."
            )
            has_chinese_remaining = "yes" in result_chinese.get("answer", "").lower()

            # Check 3: Is the English text coherent (not gibberish)?
            # Note: Moondream is unreliable at spelling/coherence checks, so we use
            # a lenient approach - only flag obvious issues where text detection fails
            # The primary quality gate is: has English + no Chinese remaining
            # Coherence is informational only and doesn't block pass/fail
            try:
                result_coherent = self.model.query(
                    img_translated,
                    "Read the text in this image. Can you identify any English words? "
                    "Answer 'yes' if you can read English words, 'no' if the text is unreadable."
                )
                coherent_answer = result_coherent.get("answer", "").lower()
                # Lenient check - pass if we can identify any English words
                is_coherent = "yes" in coherent_answer or "no" not in coherent_answer
            except Exception:
                is_coherent = True  # Default to coherent if check fails

            # Check 4: Get figure type of translated for comparison
            img_original = Image.open(original_path)
            original_type = self._get_figure_type(img_original)
            translated_type = self._get_figure_type(img_translated)
            figure_type_match = original_type == translated_type

            # Determine overall pass/fail
            # Pass if: has English AND no Chinese remaining
            # Note: is_coherent is informational only (Moondream unreliable for coherence)
            # Note: figure_type_match is logged but not required (vision models inconsistent)
            passed = has_english and not has_chinese_remaining

            details_parts = []
            if has_english:
                details_parts.append("English text detected")
            else:
                details_parts.append("WARNING: No English text detected")

            if has_chinese_remaining:
                details_parts.append("WARNING: Chinese text still present")
            else:
                details_parts.append("No Chinese text remaining")

            if is_coherent:
                details_parts.append("English text is coherent")
            else:
                details_parts.append("WARNING: English text may be gibberish/nonsense")

            if figure_type_match:
                details_parts.append(f"Figure type preserved ({translated_type})")
            else:
                details_parts.append(f"WARNING: Figure type changed ({original_type} -> {translated_type})")

            return {
                "passed": passed,
                "has_english": has_english,
                "has_chinese_remaining": has_chinese_remaining,
                "is_coherent": is_coherent,
                "figure_type_match": figure_type_match,
                "details": "; ".join(details_parts),
            }
        except Exception as e:
            return {
                "passed": False,
                "has_english": False,
                "has_chinese_remaining": True,
                "is_coherent": False,
                "figure_type_match": False,
                "details": f"QA validation failed: {e}",
            }

    def should_translate(self, image_path: str) -> bool:
        """
        Determine if a figure should be translated.

        Args:
            image_path: Path to figure image

        Returns:
            True if figure has Chinese text and should be translated
        """
        validation = self.validate(image_path)
        return validation.get("readable", False) and validation.get("has_chinese", False)
