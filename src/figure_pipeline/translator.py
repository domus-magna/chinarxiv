"""
Figure translation using Gemini via direct Google AI Studio API.

Primary: Direct Google AI Studio API (gemini-3-pro-image-preview)
Fallback: OpenRouter API (if Google API unavailable)

Model comparison (2024-11-29):
- Gemini 3 Pro: 100% QA pass rate, single pass, ~20s avg
- Gemini 2.5 Flash: 20% QA pass rate, 3 passes needed, ~30s avg
- Decision: Use Gemini 3 Pro via direct Google API as primary

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
# Note: Retry logic is now implemented manually in _call_api_with_retry()
# to avoid race conditions with tenacity's decorator approach

from .models import PipelineConfig
from .gemini_client import GeminiClient, GeminiRetryableError, GeminiFatalError


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


class InsufficientCreditsError(Exception):
    """Out of OpenRouter credits (402 Payment Required)."""
    def __init__(self, message: str, balance: Optional[float] = None):
        super().__init__(message)
        self.balance = balance


class FigureTranslator:
    """
    Translate figure text using Gemini via direct Google AI Studio API.

    Primary: gemini-3-pro-image-preview via Google AI Studio (GEMINI_API_KEY)
    Fallback: OpenRouter API (OPENROUTER_API_KEY) if Google API unavailable

    Model comparison (2024-11-29):
    - Gemini 3 Pro: 100% QA pass rate, single pass, ~20s avg
    - Gemini 2.5 Flash: 20% QA pass rate, 3 passes needed, ~30s avg
    - Conclusion: Use Gemini 3 Pro exclusively

    Key capabilities:
    - Translates Chinese text to English within images
    - Preserves original design, colors, and layout
    - Works with charts, graphs, tables, diagrams

    Uses strong prompting (ablation-tested) with optional iteration.
    """

    # OpenRouter API endpoint (fallback only)
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    # Models for figure translation
    # Gemini 3 Pro is dramatically better (100% vs 20% QA pass rate)
    MODEL_PRO = "google/gemini-3-pro-image-preview"
    MODEL_FLASH = "google/gemini-2.5-flash-image-preview"

    # Default to Pro - do NOT use Flash (20% pass rate)
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
        """Initialize translator with Google AI Studio as primary backend."""
        self.config = config or PipelineConfig()
        self._api_key = None
        self._gemini_client = None

    @property
    def gemini_client(self) -> Optional[GeminiClient]:
        """Get GeminiClient for direct Google API access (lazy init)."""
        if self._gemini_client is None:
            # Only initialize if GEMINI_API_KEY is available
            if os.environ.get("GEMINI_API_KEY"):
                self._gemini_client = GeminiClient()
        return self._gemini_client

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

    def check_balance(self) -> float:
        """
        Check OpenRouter credit balance.

        Returns:
            Balance in dollars

        Raises:
            RuntimeError if balance check fails
        """
        try:
            response = requests.get(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=(5, 10),
            )
            if response.ok:
                data = response.json()
                # Balance is returned in cents
                balance_cents = data.get("data", {}).get("total_credits", 0)
                return balance_cents / 100.0
            else:
                print(f"[translator] Failed to check balance: {response.status_code}")
                return -1.0
        except Exception as e:
            print(f"[translator] Balance check error: {e}")
            return -1.0

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

    def _call_api_with_retry(
        self, image_path: str, prompt: str, output_path: str, model: str
    ) -> str:
        """
        Single API call with retry logic for transient errors.

        Args:
            image_path: Path to input image
            prompt: Translation prompt
            output_path: Where to save output
            model: Model to use (passed explicitly to avoid race conditions)

        Returns:
            Path to output image

        Raises:
            TranslationRetryableError: For transient errors (will be retried)
            TranslationFatalError: For non-retryable errors
        """
        # Retry logic implemented via wrapper to avoid decorator complexity
        last_error = None
        for attempt in range(3):
            try:
                return self._call_api_single(image_path, prompt, output_path, model)
            except TranslationRetryableError as e:
                last_error = e
                wait_time = min(30, 2 * (2 ** attempt))  # Exponential backoff: 2, 4, 8... max 30
                print(f"[translator] Attempt {attempt + 1}/3 failed: {e}, waiting {wait_time}s...")
                time.sleep(wait_time)
            except TranslationFatalError:
                raise  # Don't retry fatal errors

        raise last_error or TranslationRetryableError("All retries exhausted")

    def _call_api_single(
        self, image_path: str, prompt: str, output_path: str, model: str
    ) -> str:
        """Single API call without retry (used by _call_api_with_retry)."""
        data_url, _ = self._image_to_base64(image_path)

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
        Translate an image using Google AI Studio API (primary) with OpenRouter fallback.

        Strategy:
        1. Try Google AI Studio API first (gemini-3-pro-image-preview)
        2. Fall back to OpenRouter if Google API fails or unavailable

        Model comparison (2024-11-29):
        - Gemini 3 Pro: 100% QA pass rate, single pass, ~20s avg
        - Direct Google API preferred over OpenRouter for reliability

        Args:
            image_path: Path to input image
            prompt: Translation prompt
            output_path: Where to save output

        Returns:
            Path to output image, or None if failed

        Raises:
            InsufficientCreditsError: If OpenRouter account is out of credits (402)
        """
        # Strategy 1: Try Google AI Studio API (primary)
        if self.gemini_client is not None:
            try:
                print("[translator] Trying Google AI Studio API (gemini-3-pro-image-preview)...")
                result = self.gemini_client.translate_image(
                    image_path, output_path, prompt=prompt, max_retries=3
                )
                if result:
                    print("[translator] Success with Google AI Studio API")
                    return result
                print("[translator] Google API returned no result, trying OpenRouter fallback...")
            except GeminiRetryableError as e:
                print(f"[translator] Google API retryable error: {e}, trying OpenRouter fallback...")
            except GeminiFatalError as e:
                print(f"[translator] Google API fatal error: {e}, trying OpenRouter fallback...")
            except Exception as e:
                print(f"[translator] Google API unexpected error: {e}, trying OpenRouter fallback...")
        else:
            print("[translator] Google API not available (no GEMINI_API_KEY), using OpenRouter...")

        # Strategy 2: Fall back to OpenRouter (only if API key is available)
        if not os.environ.get("OPENROUTER_API_KEY"):
            print("[translator] No OpenRouter API key - skipping fallback")
            return None

        models = [
            (self.MODEL_PRO, "Pro"),
            (self.MODEL_FLASH, "Flash"),
        ]

        for model, name in models:
            try:
                result = self._call_api_with_retry(image_path, prompt, output_path, model)
                print(f"[translator] Success with OpenRouter {name} model")
                return result
            except TranslationFatalError as e:
                print(f"[translator] OpenRouter {name} fatal error: {e}")
                # Payment error - raise special exception to stop batch processing
                if e.status_code == 402:
                    balance = self.check_balance()
                    raise InsufficientCreditsError(
                        f"Out of OpenRouter credits. Balance: ${balance:.2f}",
                        balance=balance
                    )
                # Don't fallback on auth errors
                if e.status_code in (401, 403):
                    return None
                # Continue to next model for other fatal errors
            except TranslationRetryableError as e:
                print(f"[translator] OpenRouter {name} failed after 3 attempts: {e}")
                # Continue to next model
            except Exception as e:
                print(f"[translator] OpenRouter {name} unexpected error: {e}")
                # Continue to next model

            # Add small delay between model fallback
            if model != models[-1][0]:
                print("[translator] Falling back to next OpenRouter model...")
                time.sleep(2)

        print("[translator] All APIs exhausted")
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
        min_balance: float = 1.0,  # Minimum OpenRouter balance for fallback
    ) -> dict:
        """
        Translate multiple images using Google AI Studio API (primary) with OpenRouter fallback.

        Features:
        - Uses Google AI Studio API (gemini-3-pro-image-preview) as primary
        - Falls back to OpenRouter if Google API fails
        - Checks OpenRouter balance for fallback capacity
        - Tracks failure reasons (success, API error, payment failure)
        - Reports detailed summary

        Args:
            image_paths: List of image paths
            paper_id: Paper ID
            max_concurrent: Maximum concurrent requests (default: 1)
            max_passes: Maximum passes per image
            qa_check: Optional QA function to enable iteration
            delay_between_requests: Seconds to wait between requests (default: 3.0)
            min_balance: Minimum balance to continue (default: $1.00)

        Returns:
            Dict with keys:
            - 'results': mapping of input path to output path (or None)
            - 'stats': dict with success/failure/skipped counts
            - 'stopped_early': bool if stopped due to payment
            - 'balance_before': float
            - 'balance_after': float
        """
        # Check balance before starting
        balance_before = self.check_balance()
        print(f"[translator] OpenRouter balance: ${balance_before:.2f}")

        if balance_before >= 0 and balance_before < min_balance:
            print(f"[translator] WARNING: Low balance (${balance_before:.2f} < ${min_balance:.2f})")

        # Note: balance_before == 0 is OK if Google API is available (primary)
        # Only warn, don't abort - Google API doesn't use OpenRouter credits
        if balance_before == 0 and self.gemini_client is None:
            print("[translator] ERROR: No credits and no Google API. Aborting batch.")
            return {
                'results': {p: None for p in image_paths},
                'stats': {'successful': 0, 'api_failed': 0, 'payment_failed': len(image_paths), 'skipped': 0},
                'stopped_early': True,
                'stop_reason': 'no_credits',
                'balance_before': balance_before,
                'balance_after': balance_before,
            }
        elif balance_before == 0:
            print("[translator] WARNING: OpenRouter balance is $0 (fallback unavailable, using Google API)")

        results = {}
        total = len(image_paths)
        successful = 0
        api_failed = 0
        payment_failed = 0
        skipped = 0
        stopped_early = False
        stop_reason = None

        print(f"[translator] Starting batch translation of {total} images")
        print(f"[translator] Settings: concurrency={max_concurrent}, delay={delay_between_requests}s, min_balance=${min_balance:.2f}")

        for idx, path in enumerate(image_paths):
            # Check if we should stop
            if stopped_early:
                results[path] = None
                skipped += 1
                continue

            try:
                output = self.translate(
                    path,
                    str(idx + 1),
                    paper_id,
                    max_passes=max_passes,
                    qa_check=qa_check,
                )
                results[path] = output

                if output:
                    successful += 1
                else:
                    api_failed += 1

            except InsufficientCreditsError as e:
                print(f"[translator] PAYMENT FAILURE: {e}")
                results[path] = None
                payment_failed += 1
                stopped_early = True
                stop_reason = 'insufficient_credits'
                # Mark remaining as skipped
                continue

            except Exception as e:
                print(f"[translator] API ERROR for {path}: {e}")
                results[path] = None
                api_failed += 1

            # Log progress
            done = successful + api_failed + payment_failed
            print(f"[translator] Progress: {done}/{total} (success={successful}, api_fail={api_failed}, payment_fail={payment_failed})")

            # Rate limiting between requests
            if idx < total - 1 and not stopped_early:
                time.sleep(delay_between_requests)

        # Check balance after
        balance_after = self.check_balance()

        # Summary
        print()
        print("[translator] === BATCH SUMMARY ===")
        print(f"[translator] Total images: {total}")
        print(f"[translator] Successful: {successful}")
        print(f"[translator] API failures: {api_failed}")
        print(f"[translator] Payment failures: {payment_failed}")
        print(f"[translator] Skipped: {skipped}")

        # Note: OpenRouter balance only tracks fallback usage.
        # Google AI Studio API costs are NOT tracked here (no balance API available).
        openrouter_spent = balance_before - balance_after
        if openrouter_spent > 0:
            print(f"[translator] OpenRouter fallback: ${balance_before:.2f} -> ${balance_after:.2f} (spent: ${openrouter_spent:.2f})")
            if successful > 0:
                cost_per_image = openrouter_spent / successful
                print(f"[translator] OpenRouter cost per image: ${cost_per_image:.4f}")
        else:
            print("[translator] OpenRouter balance unchanged (Google API was primary)")
            print("[translator] Note: Google AI Studio API costs are billed separately")

        if stopped_early:
            print(f"[translator] STOPPED EARLY: {stop_reason}")

        return {
            'results': results,
            'stats': {
                'successful': successful,
                'api_failed': api_failed,
                'payment_failed': payment_failed,
                'skipped': skipped,
            },
            'stopped_early': stopped_early,
            'stop_reason': stop_reason,
            'balance_before': balance_before,
            'balance_after': balance_after,
        }
