"""
Figure translation using Gemini via OpenRouter API.

Translates Chinese text within figures while preserving layout and design.
Uses strong prompting with iterative refinement for robust results.

Includes production-grade error handling with:
- Retry logic with exponential backoff for transient errors (429, 5xx, timeout)
- Proper error classification (retryable vs fatal)
- Comprehensive logging of all API calls and errors
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Optional, Callable, Tuple

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .models import PipelineConfig


class TranslationRetryableError(Exception):
    """Retryable API error (rate limit, server error, timeout)."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class TranslationFatalError(Exception):
    """Non-retryable API error (auth failure, bad request)."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class FigureTranslator:
    """
    Translate figure text using Gemini via OpenRouter.

    Model: google/gemini-3-pro-image-preview (Nano Banana Pro)
    Key capabilities:
    - Translates Chinese text to English within images
    - Preserves original design, colors, and layout
    - Works with charts, graphs, tables, diagrams

    Uses strong prompting (ablation-tested) with optional iteration.
    """

    # OpenRouter API endpoint
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    # Models for figure translation (in priority order)
    # Pro (Gemini 3) produces much higher quality but is less reliable
    # Flash (Gemini 2.5) is reliable fallback
    MODEL_PRO = "google/gemini-3-pro-image-preview"
    MODEL_FLASH = "google/gemini-2.5-flash-image-preview"

    # Default to Pro, fallback to Flash after 3 failed attempts
    MODEL = MODEL_PRO

    # Strong translation prompt (ablation-tested: passes QA in 1 pass)
    TRANSLATION_PROMPT = """CRITICAL TASK: Translate EVERY Chinese character in this scientific figure to English.

You MUST:
1. Find ALL Chinese text - including small labels, axis titles, legends, annotations, watermarks, and captions
2. Translate EACH piece of Chinese text to accurate English
3. Leave ZERO Chinese characters in the output image
4. Preserve the exact visual layout, colors, data values, and design

IMPORTANT: Scan the ENTIRE image systematically:
- Top to bottom, left to right
- Check all corners and edges
- Check inside data points, bars, or other elements
- Check legends and keys
- Check titles and subtitles
- Check axis labels and tick marks
- Check any footnotes or annotations

If you see ANY Chinese character, translate it. The goal is 100% English output.

Generate a new image with ALL Chinese text replaced by English."""

    # Followup prompt for iteration (when Chinese text remains)
    FOLLOWUP_PROMPT = """This image still contains Chinese text that was not translated in the previous attempt.

FIND AND TRANSLATE the remaining Chinese characters. Look carefully at:
- Small labels that may have been missed
- Text inside or near data elements
- Legends, keys, and annotations
- Axis labels and tick marks
- Any text in corners or edges

The output should have ZERO Chinese characters remaining.

Generate an updated image with ALL remaining Chinese text translated to English."""

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

    def _classify_error(self, status_code: int, response_text: str) -> Tuple[bool, str]:
        """
        Classify API error as retryable or fatal.

        Args:
            status_code: HTTP status code
            response_text: Response body text

        Returns:
            Tuple of (is_retryable, error_message)
        """
        # Rate limiting - retryable
        if status_code == 429:
            return True, f"Rate limited (429): {response_text[:200]}"

        # Server errors - retryable
        if 500 <= status_code < 600:
            return True, f"Server error ({status_code}): {response_text[:200]}"

        # Auth errors - fatal
        if status_code in (401, 403):
            return False, f"Auth error ({status_code}): {response_text[:200]}"

        # Bad request - fatal (usually means malformed payload)
        if status_code == 400:
            return False, f"Bad request (400): {response_text[:200]}"

        # Payment required - fatal
        if status_code == 402:
            return False, f"Payment required (402): {response_text[:200]}"

        # Other 4xx - non-retryable
        if 400 <= status_code < 500:
            return False, f"Client error ({status_code}): {response_text[:200]}"

        # Unknown - assume retryable
        return True, f"Unknown error ({status_code}): {response_text[:200]}"

    @retry(
        retry=retry_if_exception_type(TranslationRetryableError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _call_api_with_retry(self, image_path: str, prompt: str, output_path: str) -> str:
        """
        Single API call with retry logic for transient errors.

        Args:
            image_path: Path to input image
            prompt: Translation prompt
            output_path: Where to save output

        Returns:
            Path to output image

        Raises:
            TranslationRetryableError: For transient errors (will be retried)
            TranslationFatalError: For non-retryable errors
        """
        data_url, _ = self._image_to_base64(image_path)

        # Use current model (set by _call_api for fallback logic)
        model = getattr(self, '_current_model', self.MODEL_PRO)

        payload = {
            "model": model,
            "modalities": ["image", "text"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}}
                    ]
                }
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
        }

        # Network request with error handling
        try:
            response = requests.post(
                self.API_URL,
                headers=self._get_headers(),
                json=payload,
                timeout=(10, 120),
            )
        except requests.exceptions.Timeout as e:
            print(f"[translator] Timeout error: {e}")
            raise TranslationRetryableError(f"Request timeout: {e}")
        except requests.exceptions.ConnectionError as e:
            print(f"[translator] Connection error: {e}")
            raise TranslationRetryableError(f"Connection error: {e}")
        except requests.exceptions.RequestException as e:
            print(f"[translator] Network error: {e}")
            raise TranslationRetryableError(f"Network error: {e}")

        # Check response status
        if not response.ok:
            is_retryable, error_msg = self._classify_error(
                response.status_code, response.text
            )
            print(f"[translator] API error: {error_msg}")

            if is_retryable:
                raise TranslationRetryableError(error_msg, response.status_code)
            else:
                raise TranslationFatalError(error_msg, response.status_code)

        # Parse response JSON
        try:
            data = response.json()
        except ValueError as e:
            print(f"[translator] Invalid JSON response: {e}")
            raise TranslationRetryableError(f"Invalid JSON response: {e}")

        # Extract image from response
        choices = data.get("choices", [])
        if not choices:
            print(f"[translator] No choices in response: {data.keys()}")
            raise TranslationRetryableError("No choices in API response")

        images = choices[0].get("message", {}).get("images", [])
        if not images:
            # Check if there's text content instead (model may have responded with text)
            content = choices[0].get("message", {}).get("content", "")
            if content:
                print(f"[translator] Model returned text instead of image: {content[:100]}")
            raise TranslationRetryableError("No images in API response (model may be overloaded)")

        img_data = images[0]
        if img_data.get("type") == "image_url":
            img_url = img_data.get("image_url", {}).get("url", "")
            if img_url.startswith("data:"):
                parts = img_url.split(",", 1)
                if len(parts) == 2:
                    try:
                        image_bytes = base64.b64decode(parts[1])
                        with open(output_path, "wb") as f:
                            f.write(image_bytes)
                        return output_path
                    except Exception as e:
                        print(f"[translator] Failed to decode/save image: {e}")
                        raise TranslationRetryableError(f"Failed to decode image: {e}")

        print(f"[translator] Unexpected image format: {img_data.get('type')}")
        raise TranslationRetryableError("Unexpected image format in response")

    def _call_api(self, image_path: str, prompt: str, output_path: str) -> Optional[str]:
        """
        Single API call to translate an image with model fallback.

        Strategy: Try Pro model 3x, then fallback to Flash model 3x.
        Pro produces higher quality but is less reliable.

        Args:
            image_path: Path to input image
            prompt: Translation prompt
            output_path: Where to save output

        Returns:
            Path to output image, or None if failed
        """
        # Try Pro model first (3 attempts via retry decorator)
        try:
            self._current_model = self.MODEL_PRO
            result = self._call_api_with_retry(image_path, prompt, output_path)
            print(f"[translator] Success with Pro model")
            return result
        except TranslationFatalError as e:
            print(f"[translator] Pro fatal error: {e}")
            # Don't fallback on auth/payment errors
            if e.status_code in (401, 402, 403):
                return None
        except TranslationRetryableError as e:
            print(f"[translator] Pro failed after 3 attempts: {e}")
        except Exception as e:
            print(f"[translator] Pro unexpected error: {e}")

        # Fallback to Flash model (3 attempts via retry decorator)
        print(f"[translator] Falling back to Flash model...")
        try:
            self._current_model = self.MODEL_FLASH
            result = self._call_api_with_retry(image_path, prompt, output_path)
            print(f"[translator] Success with Flash fallback")
            return result
        except TranslationFatalError as e:
            print(f"[translator] Flash fatal error: {e}")
            return None
        except TranslationRetryableError as e:
            print(f"[translator] Flash failed after 3 attempts: {e}")
            return None
        except Exception as e:
            print(f"[translator] Flash unexpected error: {e}")
            return None

    def translate(
        self,
        image_path: str,
        figure_number: str,
        paper_id: str,
        output_dir: Optional[str] = None,
        max_passes: int = 3,
        qa_check: Optional[Callable[[str], bool]] = None,
    ) -> Optional[str]:
        """
        Translate Chinese text in an image to English.

        Uses strong prompting with optional iterative refinement.
        If qa_check is provided, will iterate until QA passes or max_passes reached.

        Args:
            image_path: Path to original image
            figure_number: Figure number for naming
            paper_id: Paper ID for organizing output
            output_dir: Directory for output (default: temp_dir)
            max_passes: Maximum translation attempts (default: 3)
            qa_check: Optional function(image_path) -> bool that returns True if Chinese detected

        Returns:
            Path to translated image, or None if translation failed
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        output_dir = output_dir or os.path.join(self.config.temp_dir, paper_id, "translated")
        os.makedirs(output_dir, exist_ok=True)

        current_input = image_path
        final_output = None

        for pass_num in range(1, max_passes + 1):
            # Use main prompt for first pass, followup for subsequent
            prompt = self.TRANSLATION_PROMPT if pass_num == 1 else self.FOLLOWUP_PROMPT

            # Determine output path
            ext = Path(image_path).suffix or ".png"
            if pass_num == 1:
                output_path = os.path.join(output_dir, f"fig_{figure_number}_en{ext}")
            else:
                output_path = os.path.join(output_dir, f"fig_{figure_number}_en_p{pass_num}{ext}")

            # Call API
            result = self._call_api(current_input, prompt, output_path)

            if not result:
                # API failed, return best result so far or None
                print(f"[translator] Translation API failed on pass {pass_num} for figure {figure_number}")
                return final_output

            final_output = result

            # If no QA check provided, return after first pass
            if qa_check is None:
                return final_output

            # Check if Chinese text remains
            has_chinese = qa_check(result)

            if not has_chinese:
                # Success! No Chinese remaining
                print(f"[translator] Figure {figure_number} translated successfully in {pass_num} pass(es)")
                return final_output

            # Chinese still present, iterate if we have passes left
            if pass_num < max_passes:
                print(f"[translator] Figure {figure_number}: Chinese text remaining after pass {pass_num}, retrying...")
                current_input = result
                time.sleep(1)  # Rate limiting between passes

        # Exhausted all passes
        print(f"[translator] Figure {figure_number}: Could not fully translate after {max_passes} passes")
        return final_output

    def batch_translate(
        self,
        image_paths: list,
        paper_id: str,
        max_concurrent: int = 1,  # Reduced from 3 to avoid rate limiting
        max_passes: int = 3,
        qa_check: Optional[Callable[[str], bool]] = None,
        delay_between_requests: float = 3.0,  # Increased from 0.5s
    ) -> dict:
        """
        Translate multiple images with rate limiting.

        Note: Default max_concurrent reduced to 1 and delay increased to 3s
        to avoid OpenRouter rate limiting (429 errors).

        Args:
            image_paths: List of image paths
            paper_id: Paper ID
            max_concurrent: Maximum concurrent requests (default: 1)
            max_passes: Maximum passes per image
            qa_check: Optional QA function to enable iteration
            delay_between_requests: Seconds to wait between requests (default: 3.0)

        Returns:
            Dict mapping input path to output path (or None if failed)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}
        total = len(image_paths)
        successful = 0
        failed = 0

        def translate_one(path: str, idx: int) -> tuple:
            try:
                output = self.translate(
                    path,
                    str(idx + 1),
                    paper_id,
                    max_passes=max_passes,
                    qa_check=qa_check,
                )
                return (path, output)
            except Exception as e:
                print(f"[translator] Failed to translate {path}: {e}")
                return (path, None)

        print(f"[translator] Starting batch translation of {total} images (concurrency: {max_concurrent}, delay: {delay_between_requests}s)")

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {
                executor.submit(translate_one, path, idx): path
                for idx, path in enumerate(image_paths)
            }

            for future in as_completed(futures):
                path, output = future.result()
                results[path] = output

                # Track success/failure
                if output:
                    successful += 1
                else:
                    failed += 1

                # Log progress
                done = successful + failed
                print(f"[translator] Progress: {done}/{total} ({successful} success, {failed} failed)")

                # Rate limiting between requests
                time.sleep(delay_between_requests)

        # Summary log
        success_rate = (successful / total * 100) if total > 0 else 0
        print(f"[translator] Batch complete: {successful}/{total} ({success_rate:.1f}% success rate)")

        return results
