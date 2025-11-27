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
        Compare original and translated figures.

        Uses Moondream 3's multi-image reasoning to compare:
        - Are figures identical except for text translation?
        - Any differences in layout, colors, data, structure?

        Args:
            original_path: Path to original Chinese figure
            translated_path: Path to translated English figure

        Returns:
            Dict with:
                - passed: bool - Did QA pass?
                - differences: str - Description of any differences
        """
        from PIL import Image

        if not os.path.exists(original_path) or not os.path.exists(translated_path):
            return {
                "passed": False,
                "differences": "One or both image files not found",
            }

        img_original = Image.open(original_path)
        img_translated = Image.open(translated_path)

        try:
            # Multi-image comparison - THE CORE QA QUESTION
            result = self.model.query(
                [img_original, img_translated],
                "Are these two figures identical except that the text has been "
                "translated from Chinese to English? Answer yes or no, then list "
                "any differences in layout, colors, data, or structure."
            )

            answer = result.get("answer", "")
            passed = "yes" in answer.lower().split(".")[0]  # Check first sentence

            return {
                "passed": passed,
                "differences": answer,
            }
        except Exception as e:
            return {
                "passed": False,
                "differences": f"QA comparison failed: {e}",
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
