"""
Direct Google AI Studio API client for Gemini image generation.

Bypasses OpenRouter for more reliable access to Gemini image models.
Uses REST API directly (no SDK dependency).

Models:
- gemini-2.5-flash-image (Nano Banana) - faster, cheaper
- gemini-3-pro-image-preview (Nano Banana Pro) - higher quality
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Optional, Tuple

from .circuit_breaker import classify_api_error, get_circuit_breaker

import requests


class GeminiAPIError(Exception):
    """Base error for Gemini API calls."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GeminiRetryableError(GeminiAPIError):
    """Retryable error (rate limit, server error, timeout)."""
    pass


class GeminiFatalError(GeminiAPIError):
    """Non-retryable error (auth failure, bad request)."""
    pass


class GeminiClient:
    """
    Direct Google AI Studio API client for image generation/editing.

    Uses REST API instead of SDK for simpler dependency management.
    """

    # API endpoint template
    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    # Available models (from ListModels API)
    MODEL_FLASH = "gemini-2.5-flash-image"  # Nano Banana (fast, cheaper)
    MODEL_PRO = "gemini-3-pro-image-preview"  # Nano Banana Pro (higher quality)

    # Note: TRANSLATION_PROMPT removed - use prompt from translator.py as source of truth

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Gemini client.

        Args:
            api_key: Google AI Studio API key. If None, reads from GEMINI_API_KEY env var.
        """
        self._api_key = api_key

    @property
    def api_key(self) -> str:
        """Get API key, loading from env if needed."""
        if self._api_key is None:
            self._api_key = os.environ.get("GEMINI_API_KEY")
            if not self._api_key:
                raise ValueError("GEMINI_API_KEY not set")
        return self._api_key

    def _get_endpoint(self, model: str) -> str:
        """Get API endpoint for a model."""
        return f"{self.API_BASE}/{model}:generateContent"

    def _image_to_base64(self, image_path: str) -> Tuple[str, str]:
        """
        Read image and encode as base64.

        Returns:
            Tuple of (base64_data, mime_type)
        """
        path = Path(image_path)
        ext = path.suffix.lower()

        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        mime_type = mime_map.get(ext, "image/png")

        with open(image_path, "rb") as f:
            image_data = f.read()

        b64 = base64.b64encode(image_data).decode("utf-8")
        return b64, mime_type

    def _classify_error(
        self, status_code: int, response_text: str
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Classify API error as retryable or fatal.

        Returns:
            Tuple of (is_retryable, error_message, error_code)
            error_code is set for billing/quota errors that should trip circuit breaker
        """
        # Use shared error classification
        error_code = classify_api_error(status_code, response_text)
        error_msg = f"HTTP {status_code}: {response_text[:200]}"

        # Determine if retryable
        # Server errors (5xx) are retryable
        if 500 <= status_code < 600:
            return True, error_msg, error_code

        # Rate limits without quota issues are retryable
        if status_code == 429 and error_code is None:
            return True, error_msg, None

        # Bad request is fatal but doesn't trip circuit breaker
        if status_code == 400:
            return False, error_msg, None

        # Other 4xx with billing error codes are fatal
        if 400 <= status_code < 500:
            is_retryable = error_code is None
            return is_retryable, error_msg, error_code

        # Unknown - assume retryable
        return True, error_msg, error_code

    def _call_api(
        self,
        image_path: str,
        prompt: str,
        model: str,
        timeout: Tuple[int, int] = (10, 120),
    ) -> Optional[bytes]:
        """
        Make a single API call.

        Args:
            image_path: Path to input image
            prompt: Text prompt for image editing
            model: Model to use
            timeout: (connect_timeout, read_timeout) in seconds

        Returns:
            Image bytes if successful, None otherwise

        Raises:
            GeminiRetryableError: For transient errors
            GeminiFatalError: For non-retryable errors
        """
        # Encode image
        b64_data, mime_type = self._image_to_base64(image_path)

        # Build request
        endpoint = self._get_endpoint(model)
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_data
                        }
                    }
                ]
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            }
        }

        # Make request
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.exceptions.Timeout as e:
            raise GeminiRetryableError(f"Request timeout: {e}")
        except requests.exceptions.ConnectionError as e:
            raise GeminiRetryableError(f"Connection error: {e}")
        except requests.exceptions.RequestException as e:
            raise GeminiRetryableError(f"Network error: {e}")

        # Check response
        if not response.ok:
            is_retryable, error_msg, error_code = self._classify_error(
                response.status_code, response.text
            )

            # Record billing errors to circuit breaker
            if error_code:
                circuit_breaker = get_circuit_breaker()
                circuit_breaker.record_failure(error_code, "gemini", error_msg)

            if is_retryable:
                raise GeminiRetryableError(error_msg, response.status_code)
            else:
                raise GeminiFatalError(error_msg, response.status_code)

        # Parse response
        try:
            data = response.json()
        except ValueError as e:
            raise GeminiRetryableError(f"Invalid JSON response: {e}")

        # Extract image from response
        candidates = data.get("candidates", [])
        if not candidates:
            # Check for safety block
            if "promptFeedback" in data:
                feedback = data.get("promptFeedback", {})
                block_reason = feedback.get("blockReason", "unknown")
                raise GeminiFatalError(f"Request blocked: {block_reason}")
            raise GeminiRetryableError("No candidates in response")

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])

        for part in parts:
            if "inlineData" in part:
                inline_data = part["inlineData"]
                b64_image = inline_data.get("data", "")
                if b64_image:
                    return base64.b64decode(b64_image)

        # No image found - check if text response
        for part in parts:
            if "text" in part:
                text = part["text"]
                print(f"[gemini] Model returned text instead of image: {text[:200]}")

        raise GeminiRetryableError("No image in response")

    def translate_image(
        self,
        image_path: str,
        output_path: str,
        model: Optional[str] = None,
        prompt: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> Optional[str]:
        """
        Translate Chinese text in an image to English.

        Args:
            image_path: Path to input image with Chinese text
            output_path: Where to save translated image
            model: Model to use (default: MODEL_PRO)
            prompt: Custom prompt (default: TRANSLATION_PROMPT)
            max_retries: Maximum retry attempts
            retry_delay: Base delay between retries (exponential backoff)

        Returns:
            Path to output image, or None if failed
        """
        model = model or self.MODEL_PRO
        if not prompt:
            raise ValueError("prompt is required - use FigureTranslator.TRANSLATION_PROMPT")

        if not os.path.exists(image_path):
            print(f"[gemini] Image not found: {image_path}")
            return None

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        last_error = None
        for attempt in range(max_retries):
            try:
                image_bytes = self._call_api(image_path, prompt, model)
                if image_bytes:
                    with open(output_path, "wb") as f:
                        f.write(image_bytes)
                    print(f"[gemini] Translated image saved: {output_path}")
                    return output_path
            except GeminiRetryableError as e:
                last_error = e
                wait_time = retry_delay * (2 ** attempt)
                print(f"[gemini] Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    print(f"[gemini] Waiting {wait_time:.1f}s before retry...")
                    time.sleep(wait_time)
            except GeminiFatalError as e:
                print(f"[gemini] Fatal error: {e}")
                return None

        print(f"[gemini] All retries exhausted: {last_error}")
        return None


def test_api_key() -> bool:
    """
    Test if the Gemini API key is valid.

    Returns:
        True if API key works, False otherwise
    """
    client = GeminiClient()
    try:
        # Make a simple text-only request to test auth
        endpoint = f"{GeminiClient.API_BASE}/gemini-2.5-flash:generateContent"
        headers = {
            "x-goog-api-key": client.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "contents": [{"parts": [{"text": "Say 'hello'"}]}],
        }
        response = requests.post(endpoint, headers=headers, json=payload, timeout=(5, 10))
        if response.ok:
            print("[gemini] API key is valid")
            return True
        else:
            print(f"[gemini] API key test failed: {response.status_code} - {response.text[:200]}")
            return False
    except Exception as e:
        print(f"[gemini] API key test error: {e}")
        return False


if __name__ == "__main__":
    # Quick test
    from dotenv import load_dotenv
    load_dotenv()

    print("Testing Gemini API key...")
    if test_api_key():
        print("API key works!")
    else:
        print("API key test failed")
