"""
Figure translation using Gemini 3 Pro Image API.

Translates Chinese text within figures while preserving layout and design.
"""
from __future__ import annotations

import os
from typing import Optional

from .models import PipelineConfig


class FigureTranslator:
    """
    Translate figure text using Gemini 3 Pro Image (Nano Banana Pro).

    Key capabilities:
    - Translates Chinese text to English within images
    - Preserves original design, colors, and layout
    - Works with charts, graphs, tables, diagrams
    """

    # Translation prompt optimized for scientific figures
    TRANSLATION_PROMPT = """
Translate all Chinese text in this scientific figure to English.

Requirements:
- Keep ALL visual elements exactly the same (colors, shapes, data, layout)
- Only change the text language from Chinese to English
- Preserve font sizes proportionally
- Keep numbers, mathematical symbols, and units unchanged
- If there are axis labels, translate them accurately
- Technical terms should be translated precisely

Output a new image with the translated text.
"""

    def __init__(self, config: Optional[PipelineConfig] = None):
        """Initialize translator."""
        self.config = config or PipelineConfig()
        self._model = None

    @property
    def model(self):
        """Lazy-load Gemini model."""
        if self._model is None:
            try:
                import google.generativeai as genai

                api_key = self.config.gemini_api_key or os.environ.get("GEMINI_API_KEY")
                if not api_key:
                    raise ValueError("GEMINI_API_KEY not set")

                genai.configure(api_key=api_key)
                # Use Gemini 3 Pro Image for translation
                self._model = genai.GenerativeModel("gemini-2.0-flash-exp")
            except ImportError:
                raise ImportError(
                    "google-generativeai not installed. Install with: pip install google-generativeai"
                )
        return self._model

    def translate(
        self,
        image_path: str,
        figure_number: str,
        paper_id: str,
        output_dir: Optional[str] = None,
    ) -> Optional[str]:
        """
        Translate Chinese text in an image to English.

        Args:
            image_path: Path to original image
            figure_number: Figure number for naming
            paper_id: Paper ID for organizing output
            output_dir: Directory for output (default: temp_dir)

        Returns:
            Path to translated image, or None if translation failed
        """
        import google.generativeai as genai
        from PIL import Image

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        output_dir = output_dir or os.path.join(self.config.temp_dir, paper_id, "translated")
        os.makedirs(output_dir, exist_ok=True)

        # Load image
        img = Image.open(image_path)

        # Call Gemini API
        try:
            response = self.model.generate_content(
                [self.TRANSLATION_PROMPT, img],
                generation_config={
                    "temperature": 0.2,
                    "max_output_tokens": 4096,
                },
            )

            # Check if response contains an image
            if hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                for part in candidate.content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        # Save the generated image
                        ext = image_path.rsplit(".", 1)[-1] if "." in image_path else "png"
                        output_path = os.path.join(
                            output_dir, f"fig_{figure_number}_en.{ext}"
                        )

                        # Decode and save image
                        import base64
                        image_data = base64.b64decode(part.inline_data.data)
                        with open(output_path, "wb") as f:
                            f.write(image_data)

                        return output_path

            # If no image in response, Gemini might not support image generation yet
            # Fall back to returning None (figure won't be translated)
            from ..utils import log
            log(f"Gemini did not return an image for figure {figure_number}")
            return None

        except Exception as e:
            from ..utils import log
            log(f"Translation failed for figure {figure_number}: {e}")
            raise

    def batch_translate(
        self,
        image_paths: list,
        paper_id: str,
        max_concurrent: int = 3,
    ) -> dict:
        """
        Translate multiple images with rate limiting.

        Args:
            image_paths: List of image paths
            paper_id: Paper ID
            max_concurrent: Maximum concurrent requests

        Returns:
            Dict mapping input path to output path (or None if failed)
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}

        def translate_one(path: str, idx: int) -> tuple:
            try:
                output = self.translate(path, str(idx + 1), paper_id)
                return (path, output)
            except Exception as e:
                from ..utils import log
                log(f"Failed to translate {path}: {e}")
                return (path, None)

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(translate_one, path, idx): path
                for idx, path in enumerate(image_paths)
            }

            for future in as_completed(futures):
                path, output = future.result()
                results[path] = output
                # Rate limiting
                time.sleep(0.5)

        return results
