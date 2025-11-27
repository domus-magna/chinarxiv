"""
Figure translation using Gemini via OpenRouter API.

Translates Chinese text within figures while preserving layout and design.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

import requests

from .models import PipelineConfig


class FigureTranslator:
    """
    Translate figure text using Gemini via OpenRouter.

    Model: google/gemini-3-pro-image-preview (Nano Banana Pro)
    Key capabilities:
    - Translates Chinese text to English within images
    - Preserves original design, colors, and layout
    - Works with charts, graphs, tables, diagrams
    """

    # OpenRouter API endpoint
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    # Model for figure translation
    MODEL = "google/gemini-3-pro-image-preview"

    # Translation prompt optimized for scientific figures
    TRANSLATION_PROMPT = """Edit this scientific figure: translate all Chinese text to English.

Requirements:
- Keep ALL visual elements exactly the same (colors, shapes, data, layout)
- Only change the text language from Chinese to English
- Preserve font sizes proportionally
- Keep numbers, mathematical symbols, and units unchanged
- If there are axis labels, translate them accurately
- Technical terms should be translated precisely for academic use

Generate a new version of this image with the Chinese text replaced by English translations."""

    def __init__(self, config: Optional[PipelineConfig] = None):
        """Initialize translator."""
        self.config = config or PipelineConfig()
        self._api_key = None

    @property
    def api_key(self) -> str:
        """Get OpenRouter API key."""
        if self._api_key is None:
            self._api_key = os.environ.get("OPENROUTER_API_KEY")
            if not self._api_key:
                raise ValueError("OPENROUTER_API_KEY not set")
        return self._api_key

    def _get_headers(self) -> dict:
        """Get headers for OpenRouter API."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/",
            "X-Title": "chinaxiv-english-figures",
        }

    def _image_to_base64(self, image_path: str) -> tuple[str, str]:
        """
        Read image and convert to base64 data URL.

        Returns:
            Tuple of (data_url, mime_type)
        """
        path = Path(image_path)
        ext = path.suffix.lower()

        # Determine MIME type
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        mime_type = mime_map.get(ext, "image/png")

        # Read and encode
        with open(image_path, "rb") as f:
            image_data = f.read()

        b64 = base64.b64encode(image_data).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64}"

        return data_url, mime_type

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
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        output_dir = output_dir or os.path.join(self.config.temp_dir, paper_id, "translated")
        os.makedirs(output_dir, exist_ok=True)

        # Convert image to base64
        data_url, mime_type = self._image_to_base64(image_path)

        # Build request payload
        payload = {
            "model": self.MODEL,
            "modalities": ["image", "text"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.TRANSLATION_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url}
                        }
                    ]
                }
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
        }

        # Call OpenRouter API
        try:
            response = requests.post(
                self.API_URL,
                headers=self._get_headers(),
                json=payload,
                timeout=(10, 120),  # connect, read
            )

            if not response.ok:
                from ..utils import log
                log(f"OpenRouter API error {response.status_code}: {response.text[:500]}")
                return None

            data = response.json()

            # Extract generated image from response
            choices = data.get("choices", [])
            if not choices:
                from ..utils import log
                log(f"No choices in response for figure {figure_number}")
                return None

            message = choices[0].get("message", {})
            images = message.get("images", [])

            if not images:
                # Model might have returned text instead of image
                text_content = message.get("content", "")
                from ..utils import log
                log(f"No image in response for figure {figure_number}. Got text: {text_content[:200]}")
                return None

            # Get the first generated image
            image_data = images[0]
            if image_data.get("type") == "image_url":
                img_url = image_data.get("image_url", {}).get("url", "")

                # Parse data URL: data:image/png;base64,{data}
                if img_url.startswith("data:"):
                    parts = img_url.split(",", 1)
                    if len(parts) == 2:
                        header, b64_data = parts
                        # Extract format from header
                        if "png" in header:
                            ext = "png"
                        elif "jpeg" in header or "jpg" in header:
                            ext = "jpg"
                        else:
                            ext = "png"

                        # Decode and save
                        output_path = os.path.join(output_dir, f"fig_{figure_number}_en.{ext}")
                        image_bytes = base64.b64decode(b64_data)

                        with open(output_path, "wb") as f:
                            f.write(image_bytes)

                        return output_path

            from ..utils import log
            log(f"Could not extract image from response for figure {figure_number}")
            return None

        except requests.exceptions.Timeout:
            from ..utils import log
            log(f"Timeout translating figure {figure_number}")
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
                # Rate limiting - be gentle with the API
                time.sleep(1.0)

        return results
