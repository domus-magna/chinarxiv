#!/usr/bin/env python3
"""
Proof of Concept: OCR-guided figure translation.

This script tests TWO approaches to figure translation:

APPROACH 1 (gemini): OCR → Translate → Gemini Context
1. OCR: Extract Chinese text with bounding boxes (PaddleOCR or EasyOCR)
2. Translate: Convert Chinese text to English via OpenRouter
3. Regenerate: Send original image + translations to Gemini

APPROACH 2 (inpaint): OCR → Inpaint → PIL Overlay
1. OCR: Extract Chinese text with bounding boxes
2. Translate: Convert Chinese text to English via OpenRouter
3. Mask: Create binary mask from OCR bounding boxes
4. Inpaint: Use LaMa to erase all text (deterministic, high quality)
5. Render: Draw English text at original positions with PIL

Usage:
    # Install dependencies first:
    pip install paddlepaddle paddleocr  # For PaddleOCR
    pip install easyocr                  # For EasyOCR
    pip install simple-lama-inpainting   # For inpaint approach

    # Run with Gemini regeneration (default):
    python scripts/figure_translation_poc.py path/to/figure.png --method gemini

    # Run with inpainting + PIL overlay:
    python scripts/figure_translation_poc.py path/to/figure.png --method inpaint

    # Compare both methods:
    python scripts/figure_translation_poc.py path/to/figure.png --compare-methods

    # Compare both OCR engines:
    python scripts/figure_translation_poc.py path/to/figure.png --compare-ocr

Environment variables:
    GEMINI_API_KEY: Google AI Studio API key (required for gemini method)
    OPENROUTER_API_KEY: OpenRouter API key (required for translation)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal, Optional

import requests

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class TextRegion:
    """A detected text region with bounding box."""

    bbox: list[list[float]]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
    text: str
    confidence: float
    translated: str = ""
    id: int = 0  # Region index (0-based)
    angle: float = 0.0  # Rotation angle in degrees


@dataclass
class OCRResult:
    """Result from OCR extraction."""

    regions: list[TextRegion] = field(default_factory=list)
    ocr_engine: str = ""
    extraction_time_ms: float = 0
    error: str = ""
    engine_version: str = ""  # e.g., "2.7.0"
    model: str = ""  # e.g., "ch_PP-OCRv4"
    image_hash: str = ""  # SHA256 of input image for validation


@dataclass
class TranslationResult:
    """Result from text translation."""

    regions: list[TextRegion] = field(default_factory=list)
    translation_time_ms: float = 0
    total_chars: int = 0
    error: str = ""


@dataclass
class RegenerationResult:
    """Result from image regeneration."""

    output_path: str = ""
    regeneration_time_ms: float = 0
    prompt_used: str = ""
    prompt_variant: str = "baseline"
    error: str = ""


@dataclass
class POCResult:
    """Complete POC pipeline result."""

    input_path: str
    ocr: OCRResult
    translation: TranslationResult
    regeneration: RegenerationResult
    baseline_path: str = ""  # Current pipeline output for comparison
    total_time_ms: float = 0


class OCREngine:
    """Base class for OCR engines."""

    def extract(self, image_path: str) -> OCRResult:
        raise NotImplementedError


class PaddleOCREngine(OCREngine):
    """PaddleOCR engine for Chinese text extraction."""

    def __init__(self):
        self._ocr = None

    @property
    def ocr(self):
        """Lazy-load PaddleOCR to avoid import overhead."""
        if self._ocr is None:
            try:
                from paddleocr import PaddleOCR
                # use_angle_cls=True for rotated text
                # lang='ch' for Chinese
                self._ocr = PaddleOCR(
                    use_angle_cls=True,
                    lang='ch',
                    show_log=False,  # Suppress verbose logging
                )
            except ImportError:
                raise ImportError(
                    "PaddleOCR not installed. Run: pip install paddlepaddle paddleocr"
                )
        return self._ocr

    def extract(self, image_path: str) -> OCRResult:
        """Extract Chinese text with bounding boxes."""
        start = time.time()
        result = OCRResult(ocr_engine="PaddleOCR")

        try:
            ocr_output = self.ocr.ocr(image_path, cls=True)

            # Handle empty results
            if not ocr_output or not ocr_output[0]:
                result.extraction_time_ms = (time.time() - start) * 1000
                return result

            # Parse results
            for line in ocr_output[0]:
                bbox, (text, confidence) = line
                if confidence > 0.5 and text.strip():
                    result.regions.append(TextRegion(
                        bbox=bbox,
                        text=text.strip(),
                        confidence=confidence,
                    ))
        except Exception as e:
            result.error = str(e)

        result.extraction_time_ms = (time.time() - start) * 1000
        return result


class EasyOCREngine(OCREngine):
    """EasyOCR engine for Chinese text extraction."""

    def __init__(self):
        self._reader = None

    @property
    def reader(self):
        """Lazy-load EasyOCR to avoid import overhead."""
        if self._reader is None:
            try:
                import easyocr
                # ['ch_sim', 'en'] for Simplified Chinese + English
                self._reader = easyocr.Reader(
                    ['ch_sim', 'en'],
                    verbose=False,
                )
            except ImportError:
                raise ImportError(
                    "EasyOCR not installed. Run: pip install easyocr"
                )
        return self._reader

    def extract(self, image_path: str) -> OCRResult:
        """Extract Chinese text with bounding boxes."""
        start = time.time()
        result = OCRResult(ocr_engine="EasyOCR")

        try:
            # EasyOCR returns: [(bbox, text, confidence), ...]
            # bbox format: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            ocr_output = self.reader.readtext(image_path)

            for bbox, text, confidence in ocr_output:
                if confidence > 0.5 and text.strip():
                    result.regions.append(TextRegion(
                        bbox=bbox,
                        text=text.strip(),
                        confidence=confidence,
                    ))
        except Exception as e:
            result.error = str(e)

        result.extraction_time_ms = (time.time() - start) * 1000
        return result


class TextTranslator:
    """Translate Chinese text to English using OpenRouter."""

    API_URL = "https://openrouter.ai/api/v1/chat/completions"
    # Use DeepSeek for consistency with body translation
    MODEL = "deepseek/deepseek-chat-v3-0324"

    def __init__(self):
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/",
            "X-Title": "chinaxiv-figure-translation-poc",
        }

    def translate_batch(self, regions: list[TextRegion]) -> TranslationResult:
        """Translate all text regions in a single API call."""
        start = time.time()
        result = TranslationResult(regions=regions)

        if not regions:
            result.translation_time_ms = (time.time() - start) * 1000
            return result

        # Prepare batch translation prompt
        texts = [r.text for r in regions]
        result.total_chars = sum(len(t) for t in texts)

        prompt = f"""Translate these Chinese text fragments from a scientific figure to English.
Return a JSON array with the translations in the same order.

Input texts:
{json.dumps(texts, ensure_ascii=False, indent=2)}

Requirements:
1. Translate each fragment accurately
2. Keep scientific terminology precise
3. Keep translations concise (figure labels should be short)
4. If a fragment is already English, return it unchanged
5. If a fragment is a number or symbol, return it unchanged

Return ONLY a JSON array of translated strings, nothing else.
Example output: ["Translation 1", "Translation 2", ...]"""

        try:
            response = requests.post(
                self.API_URL,
                headers=self._get_headers(),
                json={
                    "model": self.MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,  # Low for consistency
                    "max_tokens": 2000,
                },
                timeout=(10, 60),
            )

            if not response.ok:
                result.error = f"API error {response.status_code}: {response.text[:200]}"
                result.translation_time_ms = (time.time() - start) * 1000
                return result

            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Parse JSON response
            # Strip markdown code blocks if present
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                content = content.rsplit("```", 1)[0]

            translations = json.loads(content)

            if len(translations) != len(regions):
                print(f"  Warning: Translation count mismatch: got {len(translations)}, expected {len(regions)}")
                # Still apply what we got - better than nothing

            for i, trans in enumerate(translations):
                if i < len(result.regions):
                    result.regions[i].translated = trans

        except json.JSONDecodeError as e:
            result.error = f"Failed to parse translation response: {e}"
        except Exception as e:
            result.error = f"Translation error: {e}"

        result.translation_time_ms = (time.time() - start) * 1000
        return result


@dataclass
class VerificationResult:
    """Result from verifying translated output."""

    expected_texts: list[str] = field(default_factory=list)
    found_texts: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)  # {"expected": str, "found": str, "type": str}
    match_rate: float = 0.0
    passed: bool = False


class GeminiRegenerator:
    """Regenerate figure with translated text using Gemini."""

    # Use REST API for image generation (SDK doesn't support image output well)
    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    MODEL = "gemini-3-pro-image-preview"  # Supports image generation

    # Prompt variations for testing (based on Google's best practices research)
    PROMPT_VARIANTS = {
        "baseline": """Regenerate this scientific figure with ALL Chinese text replaced by the English translations below.

TRANSLATION MAP (in reading order, top-to-bottom, left-to-right):
{translation_map}

CRITICAL INSTRUCTIONS:
1. Use EXACTLY the English translations provided - do NOT modify, rephrase, or invent new translations
2. Place each translation at the location where the original Chinese text appears
3. PRESERVE all visual elements exactly: lines, colors, data points, axes, gridlines, bars, etc.
4. Match the font SIZE of the original text as closely as possible
5. Use a clean, readable font appropriate for scientific figures
6. Ensure all text is legible - adjust font size if needed to fit the space
7. The output image should have the same dimensions as the input

WHAT TO PRESERVE UNCHANGED:
- All numerical values and units
- All graphical elements (lines, bars, points, shapes)
- Colors and styling
- Layout and positioning
- Any English text already present

Generate the translated figure image.""",

        "concise": """Replace all Chinese text in this figure with English translations.

Translations:
{translation_map}

Keep everything else unchanged. Same dimensions.""",

        "narrative": """This is a scientific figure containing Chinese text that needs to be translated to English.

Looking at this figure, I need you to recreate it exactly as it appears, but replace any Chinese text with the English translations provided below. The visual elements - all the charts, graphs, lines, colors, and data points - should remain exactly as they are. Only the text language should change.

Here are the translations to use:
{translation_map}

The output should be identical to the input except the Chinese text is now in English.""",

        "preserve_first": """PRESERVE these elements exactly as they appear in the image:
- All chart elements: axes, gridlines, data points, bars, lines
- All colors, styling, and visual formatting
- All numerical values and units
- The image dimensions and aspect ratio
- Any English text already present

CHANGE only the Chinese text, using these translations:
{translation_map}

Generate the updated figure.""",

        "edit_style": """Edit this image: Replace the Chinese text with these English translations while keeping the background, design, and all other elements unchanged.

Translations:
{translation_map}

Match the font style, size, and color to look natural and consistent with the original.""",
    }

    def __init__(self, ocr_engine: Optional[OCREngine] = None):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self._ocr_engine = ocr_engine

    def regenerate(
        self,
        image_path: str,
        translations: list[TextRegion],
        output_path: str,
        prompt_variant: str = "baseline",
    ) -> RegenerationResult:
        """Regenerate image with translated text.

        Args:
            image_path: Path to source image
            translations: List of TextRegion with translated text
            output_path: Where to save output
            prompt_variant: Which prompt template to use (baseline, concise, narrative, preserve_first, edit_style)
        """
        start = time.time()
        result = RegenerationResult(output_path=output_path)

        # Build translation map for prompt
        translation_map = []
        for region in translations:
            if region.translated:
                translation_map.append({
                    "original": region.text,
                    "translated": region.translated,
                    "location": self._describe_location(region.bbox),
                    "confidence": round(region.confidence, 2),
                })

        if not translation_map:
            result.error = "No translations to apply"
            result.regeneration_time_ms = (time.time() - start) * 1000
            return result

        # Get the prompt template and format it
        if prompt_variant not in self.PROMPT_VARIANTS:
            print(f"  Warning: Unknown prompt variant '{prompt_variant}', using 'baseline'")
            prompt_variant = "baseline"

        prompt_template = self.PROMPT_VARIANTS[prompt_variant]
        translation_map_json = json.dumps(translation_map, indent=2, ensure_ascii=False)
        prompt = prompt_template.format(translation_map=translation_map_json)

        result.prompt_used = prompt
        result.prompt_variant = prompt_variant  # Track which variant was used

        try:
            result_data = self._regenerate_with_rest(image_path, prompt, output_path)
            if result_data.get("error"):
                result.error = result_data["error"]
            elif result_data.get("output_path"):
                # Use the corrected output path (with proper extension)
                result.output_path = result_data["output_path"]
        except Exception as e:
            result.error = str(e)

        result.regeneration_time_ms = (time.time() - start) * 1000
        return result

    def verify_output(
        self,
        output_path: str,
        expected_translations: list[str],
        threshold: float = 0.85,
    ) -> VerificationResult:
        """
        Verify translated output by OCR'ing and comparing to expected text.

        Args:
            output_path: Path to the translated image
            expected_translations: List of expected English text strings
            threshold: Similarity threshold for fuzzy matching (0-1)

        Returns:
            VerificationResult with errors and match rate
        """
        result = VerificationResult(expected_texts=expected_translations)

        if not self._ocr_engine:
            result.errors.append({"type": "no_ocr", "message": "No OCR engine configured"})
            return result

        # OCR the output image
        ocr_result = self._ocr_engine.extract(output_path)
        if ocr_result.error:
            result.errors.append({"type": "ocr_error", "message": ocr_result.error})
            return result

        result.found_texts = [r.text for r in ocr_result.regions]

        # Fuzzy match each expected translation against found texts
        matched = 0
        for expected in expected_translations:
            if not expected.strip():
                matched += 1
                continue

            best_match, best_ratio = self._find_best_match(expected, result.found_texts)

            if best_ratio >= threshold:
                matched += 1
            else:
                # Record the error for correction
                result.errors.append({
                    "type": "mismatch" if best_match else "missing",
                    "expected": expected,
                    "found": best_match or "(not found)",
                    "similarity": best_ratio,
                })

        result.match_rate = matched / len(expected_translations) if expected_translations else 1.0
        result.passed = result.match_rate >= 0.95 and len(result.errors) == 0

        return result

    def _find_best_match(self, target: str, candidates: list[str]) -> tuple[str, float]:
        """Find best fuzzy match for target in candidates."""
        from difflib import SequenceMatcher

        best_match = ""
        best_ratio = 0.0

        target_lower = target.lower().strip()

        for candidate in candidates:
            candidate_lower = candidate.lower().strip()

            # Try exact substring match first
            if target_lower in candidate_lower or candidate_lower in target_lower:
                ratio = 0.95
            else:
                ratio = SequenceMatcher(None, target_lower, candidate_lower).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate

        return best_match, best_ratio

    def regenerate_with_retry(
        self,
        image_path: str,
        translations: list[TextRegion],
        output_path: str,
        max_retries: int = 2,
        verify: bool = True,
    ) -> RegenerationResult:
        """
        Regenerate with verification and targeted corrections.

        Args:
            image_path: Original image path
            translations: List of text regions with translations
            output_path: Where to save output
            max_retries: Maximum correction attempts
            verify: Whether to verify and correct

        Returns:
            RegenerationResult with final output
        """
        # First attempt
        result = self.regenerate(image_path, translations, output_path)

        if result.error or not verify or not self._ocr_engine:
            return result

        expected = [t.translated for t in translations if t.translated]

        for retry in range(max_retries):
            # Verify the output
            verification = self.verify_output(result.output_path, expected)

            print(f"  [Verification] Match rate: {verification.match_rate:.1%}, Errors: {len(verification.errors)}")

            if verification.passed:
                print(f"  [Verification] PASSED after {retry} correction(s)")
                return result

            if not verification.errors:
                break

            # Build correction prompt
            print(f"  [Retry {retry + 1}/{max_retries}] Correcting {len(verification.errors)} errors...")

            correction_result = self._apply_corrections(
                result.output_path,
                verification.errors,
                output_path,
            )

            if correction_result.get("error"):
                print(f"  [Retry] Correction failed: {correction_result['error']}")
                break

            if correction_result.get("output_path"):
                result.output_path = correction_result["output_path"]
                result.regeneration_time_ms += correction_result.get("time_ms", 0)

        return result

    def _apply_corrections(
        self,
        current_image: str,
        errors: list[dict],
        output_path: str,
    ) -> dict:
        """Apply targeted corrections to fix specific text errors."""
        start = time.time()

        # Build correction list
        corrections = []
        for err in errors[:10]:  # Limit to 10 corrections per pass
            if err["type"] == "mismatch":
                corrections.append({
                    "wrong": err["found"],
                    "correct": err["expected"],
                })
            elif err["type"] == "missing":
                corrections.append({
                    "missing": err["expected"],
                })

        prompt = f"""Fix the following text errors in this image.

CORRECTIONS NEEDED:
{json.dumps(corrections, indent=2, ensure_ascii=False)}

For each correction:
- If "wrong" and "correct" are provided: Find the wrong text and replace it with the correct text
- If "missing" is provided: The text should be present but wasn't detected - ensure it's clearly visible

IMPORTANT:
1. Make ONLY the corrections listed - do not change anything else
2. Preserve all visual elements, layout, and formatting
3. Ensure corrected text is clearly legible
4. Keep the same font style and size as surrounding text

Generate the corrected image."""

        result_data = self._regenerate_with_rest(current_image, prompt, output_path)
        result_data["time_ms"] = (time.time() - start) * 1000

        return result_data

    def _describe_location(self, bbox: list[list[float]]) -> str:
        """Describe bounding box location with position and percentage coordinates."""
        # bbox is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]] in normalized 0-1 coords
        x_center = sum(p[0] for p in bbox) / 4
        y_center = sum(p[1] for p in bbox) / 4

        # Position description (3x3 grid)
        h_pos = "left" if x_center < 0.33 else "center" if x_center < 0.66 else "right"
        v_pos = "top" if y_center < 0.33 else "middle" if y_center < 0.66 else "bottom"

        # Add percentage coordinates for precision
        x_pct = int(x_center * 100)
        y_pct = int(y_center * 100)

        return f"{v_pos}-{h_pos} ({x_pct}%, {y_pct}%)"

    def _format_translations_xml(self, translation_map: list) -> str:
        """Format translations as XML-style items for Gemini 3."""
        lines = []
        for item in translation_map:
            # Escape any special XML chars in text
            original = item["original"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            translated = item["translated"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            lines.append(
                f'<item location="{item["location"]}" original="{original}" translated="{translated}"/>'
            )
        return "\n".join(lines)

    def _regenerate_with_sdk(
        self,
        image_path: str,
        prompt: str,
        output_path: str,
    ) -> dict:
        """Use google-generativeai SDK."""
        import google.generativeai as genai
        from PIL import Image

        img = Image.open(image_path)

        response = self._model.generate_content(
            [prompt, img],
            generation_config=genai.GenerationConfig(
                temperature=0.2,
                response_mime_type="image/png",
            ),
        )

        # Check for image in response
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    # Save image
                    with open(output_path, 'wb') as f:
                        f.write(part.inline_data.data)
                    return {"success": True}

        # Check for text response (model couldn't generate image)
        text_response = response.text if hasattr(response, 'text') else ""
        if text_response:
            return {"error": f"Model returned text instead of image: {text_response[:200]}"}

        return {"error": "No image in response"}

    # Retry configuration for Gemini API
    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 15, 30]  # seconds between retries
    REQUEST_TIMEOUT = 300  # 5 minutes for image generation

    def _regenerate_with_rest(
        self,
        image_path: str,
        prompt: str,
        output_path: str,
    ) -> dict:
        """Use REST API directly with gemini-3-pro-image-preview."""
        # Read and encode image
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        # Determine MIME type
        ext = Path(image_path).suffix.lower()
        mime_type = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
        }.get(ext, 'image/png')

        # Use gemini-3-pro-image-preview which supports image generation
        url = f"{self.API_BASE}/{self.MODEL}:generateContent"

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
                            "data": image_data,
                        }
                    }
                ]
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            }
        }

        # Retry loop for transient errors (503, 429, timeouts)
        response = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = requests.post(
                    url, headers=headers, json=payload,
                    timeout=(10, self.REQUEST_TIMEOUT)
                )

                # Check for retryable errors
                if response.status_code in [429, 503]:
                    error_msg = f"API error {response.status_code}: {response.text[:200]}"
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self.RETRY_DELAYS[attempt]
                        print(f"  [retry] Gemini {response.status_code}, waiting {delay}s (attempt {attempt + 1}/{self.MAX_RETRIES})")
                        time.sleep(delay)
                        continue
                    return {"error": f"{error_msg} (after {self.MAX_RETRIES} retries)"}

                # Non-retryable error
                if not response.ok:
                    return {"error": f"API error {response.status_code}: {response.text[:500]}"}

                # Success - break out of retry loop
                break

            except requests.exceptions.Timeout as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    print(f"  [retry] Timeout, waiting {delay}s (attempt {attempt + 1}/{self.MAX_RETRIES})")
                    time.sleep(delay)
                    continue
                return {"error": f"Request timeout after {self.REQUEST_TIMEOUT}s (after {self.MAX_RETRIES} retries)"}

            except requests.exceptions.RequestException as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    print(f"  [retry] Request error: {e}, waiting {delay}s")
                    time.sleep(delay)
                    continue
                return {"error": f"Request failed: {e} (after {self.MAX_RETRIES} retries)"}

        if response is None:
            return {"error": "No response received after retries"}

        data = response.json()

        # Extract image from response
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                if "inlineData" in part:
                    inline = part["inlineData"]
                    img_data = base64.b64decode(inline["data"])
                    # Get correct extension from response MIME type
                    resp_mime = inline.get("mimeType", "image/png")
                    ext_map = {
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/webp": ".webp",
                        "image/gif": ".gif",
                    }
                    correct_ext = ext_map.get(resp_mime, ".png")
                    # Fix output path if extension doesn't match
                    actual_output = str(Path(output_path).with_suffix(correct_ext))
                    with open(actual_output, 'wb') as f:
                        f.write(img_data)
                    return {"success": True, "output_path": actual_output, "mime_type": resp_mime}

            # Check for text-only response
            for part in parts:
                if "text" in part:
                    text = part["text"]
                    return {"error": f"Model returned text instead of image: {text[:200]}"}

        return {"error": f"No image in response. Full response: {str(data)[:500]}"}


@dataclass
class InpaintResult:
    """Result from inpaint + overlay approach."""

    output_path: str = ""
    inpaint_time_ms: float = 0
    render_time_ms: float = 0
    total_time_ms: float = 0
    mask_path: str = ""  # Debug: saved mask image
    inpainted_path: str = ""  # Debug: image after inpainting, before text
    error: str = ""


class InpaintRenderer:
    """
    Inpaint + PIL overlay approach for figure translation.

    Pipeline:
    1. Create binary mask from OCR bounding boxes
    2. Use LaMa inpainting to erase text
    3. Render translated text with PIL
    """

    def __init__(self):
        self._inpainter = None
        self._font_paths = self._discover_fonts()

    def _discover_fonts(self) -> list[str]:
        """Find available fonts for text rendering."""
        import platform

        font_paths = []
        system = platform.system()

        if system == "Darwin":  # macOS
            font_dirs = [
                "/System/Library/Fonts",
                "/Library/Fonts",
                str(Path.home() / "Library/Fonts"),
            ]
            # Prefer these fonts for scientific figures
            preferred = [
                "Helvetica.ttc",
                "Arial.ttf",
                "Arial Unicode.ttf",
                "SF-Pro-Text-Regular.otf",
            ]
        elif system == "Linux":
            font_dirs = [
                "/usr/share/fonts",
                "/usr/local/share/fonts",
                str(Path.home() / ".fonts"),
            ]
            preferred = [
                "DejaVuSans.ttf",
                "LiberationSans-Regular.ttf",
                "FreeSans.ttf",
            ]
        else:  # Windows
            font_dirs = [
                "C:/Windows/Fonts",
            ]
            preferred = [
                "arial.ttf",
                "calibri.ttf",
                "segoeui.ttf",
            ]

        # Look for preferred fonts first
        for font_dir in font_dirs:
            font_path = Path(font_dir)
            if font_path.exists():
                for pref in preferred:
                    full_path = font_path / pref
                    if full_path.exists():
                        font_paths.append(str(full_path))
                    # Also check subdirectories
                    for sub in font_path.glob(f"**/{pref}"):
                        font_paths.append(str(sub))

        return font_paths

    @property
    def inpainter(self):
        """Get inpainting method. Returns 'lama' or 'opencv'."""
        if self._inpainter is None:
            # Try LaMa first (better quality), fall back to OpenCV
            try:
                from simple_lama_inpainting import SimpleLama
                self._inpainter = SimpleLama()
                print("  Using LaMa inpainting (high quality)")
            except ImportError:
                print("  LaMa not available, using OpenCV inpainting (fallback)")
                self._inpainter = "opencv"
        return self._inpainter

    def create_mask(
        self,
        image_size: tuple[int, int],
        regions: list[TextRegion],
        dilation: int = 5,
    ) -> "Image.Image":
        """
        Create a binary mask from OCR bounding boxes.

        Args:
            image_size: (width, height) of the image
            regions: List of detected text regions
            dilation: Pixels to expand each bbox (helps ensure full coverage)

        Returns:
            PIL Image with white (255) where text should be erased
        """
        from PIL import Image, ImageDraw
        import numpy as np

        # Create black mask
        mask = Image.new('L', image_size, 0)
        draw = ImageDraw.Draw(mask)

        for region in regions:
            # Convert bbox to polygon points
            # bbox is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            points = [(int(p[0]), int(p[1])) for p in region.bbox]

            # Draw filled polygon
            draw.polygon(points, fill=255)

        # Dilate mask if requested
        if dilation > 0:
            mask_array = np.array(mask)
            try:
                import cv2
                kernel = np.ones((dilation * 2 + 1, dilation * 2 + 1), np.uint8)
                mask_array = cv2.dilate(mask_array, kernel, iterations=1)
            except ImportError:
                # Fallback: simple dilation using scipy or skip
                pass
            mask = Image.fromarray(mask_array)

        return mask

    def inpaint(
        self,
        image_path: str,
        mask: "Image.Image",
    ) -> "Image.Image":
        """
        Remove text using inpainting (LaMa or OpenCV fallback).

        Args:
            image_path: Path to original image
            mask: Binary mask (white = areas to inpaint)

        Returns:
            Inpainted image with text removed
        """
        from PIL import Image
        import numpy as np

        image = Image.open(image_path).convert('RGB')

        inpainter = self.inpainter

        if inpainter == "opencv":
            # Use OpenCV inpainting (fallback)
            import cv2

            # Convert to numpy arrays
            img_array = np.array(image)
            mask_array = np.array(mask)

            # OpenCV uses BGR, not RGB
            img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

            # Use TELEA method (generally better for text removal)
            # radius=5 is reasonable for most text sizes
            result_bgr = cv2.inpaint(img_bgr, mask_array, inpaintRadius=5, flags=cv2.INPAINT_TELEA)

            # Convert back to RGB
            result_rgb = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
            result = Image.fromarray(result_rgb)
        else:
            # Use LaMa (high quality)
            result = inpainter(image, mask)

        return result

    def render_text(
        self,
        image: "Image.Image",
        regions: list[TextRegion],
        font_size: int = 0,  # 0 = auto-size
    ) -> "Image.Image":
        """
        Render translated text on the image.

        Args:
            image: Base image (after inpainting)
            regions: Regions with translated text
            font_size: Font size (0 = auto based on bbox)

        Returns:
            Image with rendered text
        """
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np

        draw = ImageDraw.Draw(image)

        # Load font
        font = None
        for font_path in self._font_paths:
            try:
                # Start with a reasonable size, will adjust per region
                font = ImageFont.truetype(font_path, 14)
                break
            except Exception:
                continue

        if font is None:
            # Fallback to default
            font = ImageFont.load_default()

        for region in regions:
            if not region.translated:
                continue

            # Calculate bbox dimensions
            bbox = region.bbox
            x_coords = [p[0] for p in bbox]
            y_coords = [p[1] for p in bbox]

            min_x, max_x = min(x_coords), max(x_coords)
            min_y, max_y = min(y_coords), max(y_coords)

            bbox_width = max_x - min_x
            bbox_height = max_y - min_y

            # Auto-size font to fit bbox
            if font_size == 0:
                # Start with height-based estimate
                target_size = int(bbox_height * 0.8)
                target_size = max(8, min(target_size, 48))  # Clamp to reasonable range

                for font_path in self._font_paths:
                    try:
                        font = ImageFont.truetype(font_path, target_size)
                        break
                    except Exception:
                        continue

            # Get text dimensions
            text = region.translated
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]

            # Scale down font if text is too wide
            if text_width > bbox_width * 1.2:
                scale = bbox_width / text_width * 0.9
                new_size = max(8, int(target_size * scale))
                for font_path in self._font_paths:
                    try:
                        font = ImageFont.truetype(font_path, new_size)
                        break
                    except Exception:
                        continue
                text_bbox = draw.textbbox((0, 0), text, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]

            # Center text in bbox
            x = min_x + (bbox_width - text_width) / 2
            y = min_y + (bbox_height - text_height) / 2

            # Determine text color based on local background
            # Sample a few pixels around the bbox to estimate background
            img_array = np.array(image)
            sample_y = max(0, int(min_y) - 5)
            sample_x = max(0, int(min_x) - 5)
            sample_region = img_array[
                sample_y:sample_y+10,
                sample_x:sample_x+10
            ]
            if sample_region.size > 0:
                avg_brightness = np.mean(sample_region)
                text_color = "black" if avg_brightness > 127 else "white"
            else:
                text_color = "black"

            # Draw text
            draw.text((x, y), text, font=font, fill=text_color)

        return image

    def translate(
        self,
        image_path: str,
        regions: list[TextRegion],
        output_path: str,
        save_debug: bool = True,
    ) -> InpaintResult:
        """
        Full inpaint + overlay pipeline.

        Args:
            image_path: Path to original image
            regions: OCR regions with translations
            output_path: Where to save final result
            save_debug: Save intermediate images (mask, inpainted)

        Returns:
            InpaintResult with output path and timing
        """
        from PIL import Image
        import time

        start = time.time()
        result = InpaintResult(output_path=output_path)

        try:
            # Load image to get dimensions
            image = Image.open(image_path)
            image_size = image.size

            # Step 1: Create mask
            print("  Creating mask from OCR bounding boxes...")
            mask = self.create_mask(image_size, regions, dilation=3)

            if save_debug:
                mask_path = str(Path(output_path).with_name(
                    Path(output_path).stem + "_mask.png"
                ))
                mask.save(mask_path)
                result.mask_path = mask_path
                print(f"  Saved mask: {mask_path}")

            # Step 2: Inpaint
            print("  Inpainting to remove text...")
            inpaint_start = time.time()
            inpainted = self.inpaint(image_path, mask)
            result.inpaint_time_ms = (time.time() - inpaint_start) * 1000

            if save_debug:
                inpainted_path = str(Path(output_path).with_name(
                    Path(output_path).stem + "_inpainted.png"
                ))
                inpainted.save(inpainted_path)
                result.inpainted_path = inpainted_path
                print(f"  Saved inpainted: {inpainted_path}")

            # Step 3: Render text
            print("  Rendering translated text...")
            render_start = time.time()
            final = self.render_text(inpainted, regions)
            result.render_time_ms = (time.time() - render_start) * 1000

            # Save final result
            final.save(output_path)
            result.output_path = output_path
            print(f"  Saved final: {output_path}")

        except Exception as e:
            result.error = str(e)

        result.total_time_ms = (time.time() - start) * 1000
        return result


class BaselineTranslator:
    """Run current figure translation pipeline for comparison."""

    def translate(self, image_path: str, output_path: str) -> str:
        """Run baseline translation."""
        try:
            from src.figure_pipeline.translator import FigureTranslator

            translator = FigureTranslator()
            return translator.translate(
                image_path=image_path,
                figure_number="poc",
                paper_id="poc-comparison",
                output_dir=str(Path(output_path).parent),
                max_passes=1,
            )
        except Exception as e:
            return f"Error: {e}"


# =============================================================================
# COMPARISON PIPELINE CLASSES
# =============================================================================


import subprocess
import hashlib
import tempfile
import yaml
from datetime import datetime


class DependencyChecker:
    """Check external dependencies before running pipelines."""

    DOCKER_IMAGE = "zyddnys/manga-image-translator:main"

    @staticmethod
    def check_docker() -> tuple[bool, str, bool]:
        """Check Docker availability and image status.

        Returns: (available, message, image_present)
        """
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            if result.returncode != 0:
                return False, "Docker not running", False

            # Check if image exists
            result = subprocess.run(
                ["docker", "images", "-q", DependencyChecker.DOCKER_IMAGE],
                capture_output=True, text=True, timeout=5
            )
            image_present = bool(result.stdout.strip())
            if not image_present:
                return True, "Docker ready, image not pulled (will auto-pull)", False
            return True, "Docker ready", True
        except FileNotFoundError:
            return False, "Docker not installed", False
        except subprocess.TimeoutExpired:
            return False, "Docker check timed out", False

    @staticmethod
    def check_kimi_api() -> tuple[bool, str]:
        """Check if Kimi API key is available (uses OpenRouter)."""
        key = os.environ.get("CUSTOM_OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            return False, "CUSTOM_OPENAI_API_KEY or OPENROUTER_API_KEY not set"
        return True, "Kimi API key present (via OpenRouter)"

    @staticmethod
    def check_gpu() -> tuple[bool, str]:
        """Lightweight GPU check (no Docker container spin-up)."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu_name = result.stdout.strip().split('\n')[0]
                return True, f"GPU: {gpu_name}"
            return False, "CPU only (nvidia-smi not found or no GPU)"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False, "CPU only"

    @staticmethod
    def check_all() -> dict:
        """Run all checks, return status dict."""
        docker_ok, docker_msg, image_present = DependencyChecker.check_docker()
        gpu_ok, gpu_msg = DependencyChecker.check_gpu()
        kimi_ok, kimi_msg = DependencyChecker.check_kimi_api()
        gemini_ok = bool(os.environ.get("GEMINI_API_KEY"))
        openrouter_ok = bool(os.environ.get("OPENROUTER_API_KEY"))

        return {
            "docker": {"ok": docker_ok, "msg": docker_msg, "image_present": image_present},
            "gpu": {"ok": gpu_ok, "msg": gpu_msg},
            "kimi": {"ok": kimi_ok, "msg": kimi_msg},
            "gemini": {"ok": gemini_ok, "msg": "GEMINI_API_KEY set" if gemini_ok else "Missing"},
            "openrouter": {"ok": openrouter_ok, "msg": "OPENROUTER_API_KEY set" if openrouter_ok else "Missing"},
        }

    @staticmethod
    def auto_pull_image() -> bool:
        """Pull manga-image-translator image with progress."""
        print(f"Pulling {DependencyChecker.DOCKER_IMAGE}...")
        result = subprocess.run(
            ["docker", "pull", DependencyChecker.DOCKER_IMAGE],
            capture_output=False  # Show progress
        )
        return result.returncode == 0

    @staticmethod
    def print_status():
        """Print formatted dependency status."""
        status = DependencyChecker.check_all()
        print("Checking dependencies...")
        for name, info in status.items():
            icon = "✓" if info["ok"] else "✗"
            print(f"  {name.capitalize()}: {icon} {info['msg']}")


class MangaTranslatorPipeline:
    """Run manga-image-translator via Docker."""

    DOCKER_IMAGE = "zyddnys/manga-image-translator:main"
    CONFIG = {"translator": {"translator": "m2m100_big", "target_lang": "ENG"}}
    TIMEOUT_GPU = 120
    TIMEOUT_CPU = 600  # Increased to 10 min to allow for first-run model download (~1.5GB)

    def __init__(self, use_gpu: bool = False):
        self.use_gpu = use_gpu

    def translate(self, image_path: str, output_dir: Path) -> dict:
        """Run translation, return status dict."""
        start = time.time()
        result = {
            "status": "pending",
            "timing_seconds": None,
            "output_path": None,
            "gpu_used": self.use_gpu,
            "translator_model": "m2m100_big",
            "notes": None,
            "error": None,
        }

        image_path = Path(image_path).resolve()  # Convert to absolute path
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write config to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(self.CONFIG, f)
            config_path = f.name

        try:
            # Build docker command
            timeout = self.TIMEOUT_GPU if self.use_gpu else self.TIMEOUT_CPU
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{image_path.parent}:/app/data",
                "-v", f"{output_dir}:/app/result",
                "-v", f"{config_path}:/app/config.json",
            ]

            if self.use_gpu:
                cmd.extend(["--gpus", "all"])

            cmd.extend([
                self.DOCKER_IMAGE,
                "local", "-v",
                "--config-file", "/app/config.json",
                "-i", f"/app/data/{image_path.name}",
                "-o", "/app/result",
            ])

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if proc.returncode != 0:
                result["status"] = "failed"
                result["error"] = proc.stderr[:500] if proc.stderr else "Unknown error"
            else:
                # Find output file - manga-image-translator uses various naming conventions
                # Check patterns in order of likelihood:
                # 1. Exact match (input filename copied to output)
                # 2. *-translated.* suffix
                # 3. Any image file containing the input stem
                # 4. Any image file in output_dir (fallback)
                output_found = None

                expected_output = output_dir / image_path.name
                if expected_output.exists():
                    output_found = expected_output
                else:
                    # Check for -translated suffix
                    translated_patterns = [
                        f"{image_path.stem}-translated.*",
                        f"{image_path.stem}_translated.*",
                        f"{image_path.stem}*",
                    ]
                    for pattern in translated_patterns:
                        matches = list(output_dir.glob(pattern))
                        if matches:
                            # Filter to image files only
                            image_matches = [m for m in matches if m.suffix.lower() in ['.png', '.jpg', '.jpeg', '.webp']]
                            if image_matches:
                                output_found = image_matches[0]
                                break

                    # Fallback: any image file in output dir
                    if not output_found:
                        all_images = list(output_dir.glob("*.png")) + list(output_dir.glob("*.jpg")) + list(output_dir.glob("*.jpeg"))
                        if all_images:
                            output_found = all_images[0]
                            result["notes"] = f"Fallback: found {output_found.name}"

                if output_found:
                    result["status"] = "success"
                    result["output_path"] = str(output_found)
                else:
                    result["status"] = "failed"
                    # Include debug info: list what files ARE in output_dir
                    all_files = list(output_dir.iterdir())
                    result["error"] = f"Output file not found. Files in {output_dir}: {[f.name for f in all_files[:5]]}"

        except subprocess.TimeoutExpired:
            result["status"] = "timeout"
            result["notes"] = f"Exceeded {timeout}s timeout"
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
        finally:
            # Clean up temp config
            try:
                os.unlink(config_path)
            except Exception:
                pass

        result["timing_seconds"] = time.time() - start
        if not self.use_gpu and result["status"] == "success":
            result["notes"] = "CPU mode"

        return result


class KimiTranslator:
    """Translate text using Kimi K2 via OpenRouter."""

    MODEL = "moonshotai/kimi-k2"  # Paid version (no privacy config required)
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
    TIMEOUT = 60
    MAX_RETRIES = 2
    RETRY_DELAY = 1.0
    RETRY_BACKOFF = 2.0

    def __init__(self):
        # Try CUSTOM_OPENAI_API_KEY first, fall back to OPENROUTER_API_KEY
        self.api_key = os.environ.get("CUSTOM_OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("CUSTOM_OPENAI_API_KEY or OPENROUTER_API_KEY not set")

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/",
            "X-Title": "chinaxiv-figure-translation-poc",
        }

    def translate(self, chinese_texts: list[str]) -> tuple[list[str], str]:
        """Batch translate Chinese to English.

        Returns: (translations, error_msg)
        """
        if not chinese_texts:
            return [], ""

        prompt = f"""Translate these Chinese text fragments from a scientific figure to English.
Return a JSON array with the translations in the same order.

Input texts:
{json.dumps(chinese_texts, ensure_ascii=False, indent=2)}

Requirements:
1. Translate each fragment accurately
2. Keep scientific terminology precise
3. Keep translations concise (figure labels should be short)
4. If a fragment is already English, return it unchanged
5. If a fragment is a number or symbol, return it unchanged

Return ONLY a JSON array of translated strings, nothing else."""

        delay = self.RETRY_DELAY
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = requests.post(
                    self.BASE_URL,
                    headers=self._get_headers(),
                    json={
                        "model": self.MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "max_tokens": 2000,
                    },
                    timeout=(10, self.TIMEOUT),
                )

                # Check for immediate failures
                if response.status_code in [401, 402, 403]:
                    return [], f"API error {response.status_code}: {response.text[:200]}"

                # Retry on rate limit
                if response.status_code == 429:
                    if attempt < self.MAX_RETRIES:
                        time.sleep(delay)
                        delay *= self.RETRY_BACKOFF
                        continue
                    return [], "Rate limited after retries"

                if not response.ok:
                    error_text = response.text[:300]
                    # Check for OpenRouter privacy configuration error
                    if "No endpoints found matching your data policy" in error_text:
                        return [], (
                            "Kimi requires OpenRouter privacy configuration. "
                            "Visit https://openrouter.ai/settings/privacy and enable data sharing for free models. "
                            f"Original error: {error_text}"
                        )
                    return [], f"API error {response.status_code}: {error_text}"

                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

                # Parse JSON response
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                    content = content.rsplit("```", 1)[0]

                translations = json.loads(content)
                return translations, ""

            except json.JSONDecodeError as e:
                return [], f"Failed to parse response: {e}"
            except requests.Timeout:
                if attempt < self.MAX_RETRIES:
                    time.sleep(delay)
                    delay *= self.RETRY_BACKOFF
                    continue
                return [], "Request timed out after retries"
            except Exception as e:
                return [], str(e)

        return [], "Max retries exceeded"


@dataclass
class ComparisonResult:
    """Result from running comparison on a single figure."""
    figure_id: str
    label: str
    type: str
    source_path: str
    image_hash: str
    ocr: dict
    pipelines: dict
    start_time: str = ""
    end_time: str = ""


class ComparisonRunner:
    """Run all pipelines on manifest figures."""

    PIPELINES = [
        "baseline",
        "ocr_gemini",
        "ocr_kimi_gemini",  # OCR + Kimi translate + Gemini render (recommended)
        "ocr_inpaint_deepseek",
        "manga_translator",
        "ocr_inpaint_kimi",
    ]

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.available_pipelines = set(self.PIPELINES)
        self._check_dependencies()

    def _check_dependencies(self):
        """Run dependency checks, disable unavailable pipelines."""
        status = DependencyChecker.check_all()

        # Check Docker for manga_translator
        if not status["docker"]["ok"]:
            self.available_pipelines.discard("manga_translator")
            print(f"  Disabling manga_translator: {status['docker']['msg']}")

        # Check Kimi API
        if not status["kimi"]["ok"]:
            self.available_pipelines.discard("ocr_inpaint_kimi")
            print(f"  Disabling ocr_inpaint_kimi: {status['kimi']['msg']}")

        # Check Gemini
        if not status["gemini"]["ok"]:
            self.available_pipelines.discard("baseline")
            self.available_pipelines.discard("ocr_gemini")
            self.available_pipelines.discard("ocr_kimi_gemini")
            print(f"  Disabling baseline, ocr_gemini, ocr_kimi_gemini: {status['gemini']['msg']}")

        # Check Kimi for ocr_kimi_gemini
        if not status["kimi"]["ok"]:
            self.available_pipelines.discard("ocr_kimi_gemini")
            # ocr_inpaint_kimi already handled above

        # Check OpenRouter (for DeepSeek)
        if not status["openrouter"]["ok"]:
            self.available_pipelines.discard("ocr_gemini")
            self.available_pipelines.discard("ocr_inpaint_deepseek")
            print(f"  Disabling ocr_gemini, ocr_inpaint_deepseek: {status['openrouter']['msg']}")

    def _compute_image_hash(self, image_path: str) -> str:
        """Compute SHA256 hash of image file."""
        hasher = hashlib.sha256()
        with open(image_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
        return f"sha256:{hasher.hexdigest()[:16]}"

    def run_figure(self, figure: dict, ocr_cache: Optional[OCRResult] = None) -> ComparisonResult:
        """Run all available pipelines on a single figure."""
        figure_id = figure["id"]
        source_path = figure["source"]
        label = figure.get("label", figure_id)
        fig_type = figure.get("type", "unknown")

        print(f"\n{'='*60}")
        print(f"Processing {figure_id}: {label}")
        print(f"{'='*60}")

        # Setup figure output directory
        fig_output_dir = self.output_dir / "figures" / figure_id
        fig_output_dir.mkdir(parents=True, exist_ok=True)

        # Copy original
        from shutil import copy2
        original_path = fig_output_dir / "original.png"
        if not original_path.exists():
            from PIL import Image
            img = Image.open(source_path)
            img.save(original_path, "PNG")

        result = ComparisonResult(
            figure_id=figure_id,
            label=label,
            type=fig_type,
            source_path=source_path,
            image_hash=self._compute_image_hash(source_path),
            ocr={},
            pipelines={},
            start_time=datetime.utcnow().isoformat() + "Z",
        )

        # Run OCR (shared across pipelines)
        if ocr_cache:
            ocr_result = ocr_cache
        else:
            print("\n[OCR] Extracting text...")
            # Try PaddleOCR first, fall back to EasyOCR
            try:
                engine = PaddleOCREngine()
                ocr_result = engine.extract(source_path)
                if ocr_result.error and "paddleocr" in ocr_result.error.lower():
                    raise ImportError(ocr_result.error)
            except ImportError as e:
                print(f"  PaddleOCR not available ({e}), using EasyOCR...")
                engine = EasyOCREngine()
                ocr_result = engine.extract(source_path)
            # Add IDs to regions
            for i, region in enumerate(ocr_result.regions):
                region.id = i

        result.ocr = {
            "engine": ocr_result.ocr_engine,
            "model": ocr_result.model or "ch_PP-OCRv4",
            "timing_seconds": ocr_result.extraction_time_ms / 1000,
            "regions": [
                {
                    "id": r.id,
                    "bbox": [[int(x) for x in point] for point in r.bbox] if hasattr(r.bbox[0], '__iter__') else [int(x) for x in r.bbox],
                    "text": r.text,
                    "confidence": float(r.confidence),
                    "angle": float(r.angle) if r.angle else 0.0,
                }
                for r in ocr_result.regions
            ],
        }
        print(f"  Found {len(ocr_result.regions)} regions in {ocr_result.extraction_time_ms:.0f}ms")

        # Run each pipeline
        for pipeline_name in self.PIPELINES:
            if pipeline_name not in self.available_pipelines:
                result.pipelines[pipeline_name] = {
                    "status": "skipped",
                    "timing_seconds": None,
                    "output_path": None,
                    "notes": "Pipeline disabled - missing dependencies",
                }
                continue

            print(f"\n[{pipeline_name}] Running...")
            start = time.time()

            try:
                if pipeline_name == "baseline":
                    pipeline_result = self._run_baseline(source_path, fig_output_dir)
                elif pipeline_name == "ocr_gemini":
                    pipeline_result = self._run_ocr_gemini(source_path, ocr_result, fig_output_dir)
                elif pipeline_name == "ocr_kimi_gemini":
                    pipeline_result = self._run_ocr_kimi_gemini(source_path, ocr_result, fig_output_dir)
                elif pipeline_name == "ocr_inpaint_deepseek":
                    pipeline_result = self._run_ocr_inpaint(source_path, ocr_result, fig_output_dir, "deepseek")
                elif pipeline_name == "manga_translator":
                    pipeline_result = self._run_manga_translator(source_path, fig_output_dir)
                elif pipeline_name == "ocr_inpaint_kimi":
                    pipeline_result = self._run_ocr_inpaint(source_path, ocr_result, fig_output_dir, "kimi")
                else:
                    pipeline_result = {"status": "unknown", "error": f"Unknown pipeline: {pipeline_name}"}

                pipeline_result["timing_seconds"] = time.time() - start
                result.pipelines[pipeline_name] = pipeline_result

                status_icon = "✓" if pipeline_result.get("status") == "success" else "✗"
                print(f"  {status_icon} {pipeline_result.get('status')} ({pipeline_result['timing_seconds']:.1f}s)")

            except Exception as e:
                result.pipelines[pipeline_name] = {
                    "status": "failed",
                    "timing_seconds": time.time() - start,
                    "output_path": None,
                    "error": str(e),
                }
                print(f"  ✗ failed: {e}")

        result.end_time = datetime.utcnow().isoformat() + "Z"
        return result

    def _run_baseline(self, image_path: str, output_dir: Path) -> dict:
        """Run baseline Gemini-only translation."""
        output_path = output_dir / "baseline.png"
        regenerator = GeminiRegenerator()

        # Create dummy regions (baseline doesn't use OCR translations)
        result_data = regenerator._regenerate_with_rest(
            image_path,
            "Translate ALL Chinese text in this scientific figure to English. "
            "Preserve the visual layout exactly. Generate the translated figure.",
            str(output_path),
        )

        if result_data.get("error"):
            return {"status": "failed", "error": result_data["error"], "output_path": None}

        return {
            "status": "success",
            "output_path": result_data.get("output_path", str(output_path)),
            "model": "gemini-2.0-flash-exp",
            "notes": "Direct Gemini translation (no OCR)",
        }

    def _run_ocr_gemini(self, image_path: str, ocr_result: OCRResult, output_dir: Path) -> dict:
        """Run OCR + translate + Gemini regenerate."""
        output_path = output_dir / "ocr_gemini.png"

        # Translate with DeepSeek
        translator = TextTranslator()
        trans_result = translator.translate_batch(ocr_result.regions)

        if trans_result.error:
            return {"status": "failed", "error": trans_result.error, "output_path": None}

        # Regenerate with Gemini
        regenerator = GeminiRegenerator()
        regen_result = regenerator.regenerate(
            image_path,
            trans_result.regions,
            str(output_path),
        )

        if regen_result.error:
            return {"status": "failed", "error": regen_result.error, "output_path": None}

        return {
            "status": "success",
            "output_path": regen_result.output_path,
            "translator_model": "deepseek/deepseek-chat-v3-0324",
            "renderer_model": "gemini-2.0-flash-exp",
            "translations": [
                {"region_id": r.id, "src": r.text, "tgt": r.translated}
                for r in trans_result.regions if r.translated
            ],
        }

    def _run_ocr_kimi_gemini(self, image_path: str, ocr_result: OCRResult, output_dir: Path) -> dict:
        """Run OCR + Kimi translate + Gemini regenerate (recommended pipeline)."""
        output_path = output_dir / "ocr_kimi_gemini.png"

        # Translate with Kimi K2
        kimi = KimiTranslator()
        texts = [r.text for r in ocr_result.regions]
        translations, error = kimi.translate(texts)

        if error:
            return {"status": "failed", "error": error, "output_path": None}

        if len(translations) != len(ocr_result.regions):
            print(f"  Warning: Translation count mismatch: got {len(translations)}, expected {len(ocr_result.regions)}")

        # Apply translations to regions
        translated_regions = []
        for i, region in enumerate(ocr_result.regions):
            new_region = TextRegion(
                id=region.id,
                text=region.text,
                bbox=region.bbox,
                confidence=region.confidence,
                translated=translations[i] if i < len(translations) else region.text,
            )
            translated_regions.append(new_region)

        # Regenerate with Gemini
        regenerator = GeminiRegenerator()
        regen_result = regenerator.regenerate(
            image_path,
            translated_regions,
            str(output_path),
        )

        if regen_result.error:
            return {"status": "failed", "error": regen_result.error, "output_path": None}

        return {
            "status": "success",
            "output_path": regen_result.output_path,
            "translator_model": "moonshotai/kimi-k2",
            "renderer_model": "gemini-3-pro-image-preview",
            "translations": [
                {"region_id": r.id, "src": r.text, "tgt": r.translated}
                for r in translated_regions if r.translated
            ],
        }

    def _run_ocr_inpaint(self, image_path: str, ocr_result: OCRResult, output_dir: Path, translator_type: str) -> dict:
        """Run OCR + translate + inpaint + PIL overlay."""
        suffix = "deepseek" if translator_type == "deepseek" else "kimi"
        output_path = output_dir / f"ocr_inpaint_{suffix}.png"

        # Translate
        if translator_type == "deepseek":
            translator = TextTranslator()
            trans_result = translator.translate_batch(ocr_result.regions)
            if trans_result.error:
                return {"status": "failed", "error": trans_result.error, "output_path": None}
            translated_regions = trans_result.regions
            translator_model = "deepseek/deepseek-chat-v3-0324"
        else:  # kimi
            kimi = KimiTranslator()
            texts = [r.text for r in ocr_result.regions]
            translations, error = kimi.translate(texts)
            if error:
                return {"status": "failed", "error": error, "output_path": None}
            # Apply translations
            translated_regions = []
            for i, region in enumerate(ocr_result.regions):
                r = TextRegion(
                    bbox=region.bbox,
                    text=region.text,
                    confidence=region.confidence,
                    translated=translations[i] if i < len(translations) else "",
                    id=region.id,
                    angle=region.angle,
                )
                translated_regions.append(r)
            translator_model = "moonshotai/kimi-k2:free"

        # Inpaint and render
        renderer = InpaintRenderer()
        inpaint_result = renderer.translate(
            image_path,
            translated_regions,
            str(output_path),
            save_debug=False,
        )

        if inpaint_result.error:
            return {"status": "failed", "error": inpaint_result.error, "output_path": None}

        return {
            "status": "success",
            "output_path": inpaint_result.output_path,
            "translator_model": translator_model,
            "inpaint_method": "cv2.INPAINT_TELEA",
            "font": "system",
            "mask_dilation_px": 3,
            "translations": [
                {"region_id": r.id, "src": r.text, "tgt": r.translated, "bbox": [[int(x) for x in point] for point in r.bbox] if hasattr(r.bbox[0], '__iter__') else [int(x) for x in r.bbox]}
                for r in translated_regions if r.translated
            ],
        }

    def _run_manga_translator(self, image_path: str, output_dir: Path) -> dict:
        """Run manga-image-translator via Docker."""
        status = DependencyChecker.check_all()
        use_gpu = status["gpu"]["ok"]

        pipeline = MangaTranslatorPipeline(use_gpu=use_gpu)
        result = pipeline.translate(image_path, output_dir / "manga_temp")

        # Move output to expected location
        if result["status"] == "success" and result["output_path"]:
            from shutil import move
            final_path = output_dir / "manga_translator.png"
            move(result["output_path"], final_path)
            result["output_path"] = str(final_path)

        return result

    def run_all(self, manifest_path: Path) -> dict:
        """Run comparison on all manifest figures."""
        # Load manifest
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)

        figures = manifest.get("figures", [])
        print(f"\nLoaded {len(figures)} figures from manifest")

        results = {
            "run_id": datetime.utcnow().strftime("%Y%m%d_%H%M%S"),
            "run_timestamp": datetime.utcnow().isoformat() + "Z",
            "manifest_checksum": self._compute_image_hash(str(manifest_path)),
            "environment": self._get_environment(),
            "figures": [],
            "summary": {
                "total_figures": len(figures),
                "pipelines_run": {p: {"success": 0, "failed": 0, "skipped": 0} for p in self.PIPELINES},
                "total_runtime_seconds": 0,
            },
        }

        start = time.time()

        for figure in figures:
            # Skip if source doesn't exist
            source_path = figure.get("source", "")
            if not source_path or not Path(source_path).exists():
                print(f"\nSkipping {figure.get('id')}: source not found at {source_path}")
                continue

            fig_result = self.run_figure(figure)
            results["figures"].append({
                "id": fig_result.figure_id,
                "label": fig_result.label,
                "type": fig_result.type,
                "source_path": fig_result.source_path,
                "image_hash": fig_result.image_hash,
                "ocr": fig_result.ocr,
                "pipelines": fig_result.pipelines,
            })

            # Update summary
            for pipeline_name, pipeline_result in fig_result.pipelines.items():
                status = pipeline_result.get("status", "unknown")
                if status == "success":
                    results["summary"]["pipelines_run"][pipeline_name]["success"] += 1
                elif status == "skipped":
                    results["summary"]["pipelines_run"][pipeline_name]["skipped"] += 1
                else:
                    results["summary"]["pipelines_run"][pipeline_name]["failed"] += 1

        results["summary"]["total_runtime_seconds"] = time.time() - start

        # Save results
        results_path = self.output_dir / "results.json"
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nResults saved to {results_path}")

        return results

    def _get_environment(self) -> dict:
        """Get environment info for reproducibility."""
        import platform
        env = {
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "random_seed": 42,
        }

        try:
            import paddleocr
            env["paddleocr_version"] = paddleocr.__version__
        except Exception:
            pass

        try:
            import cv2
            env["opencv_version"] = cv2.__version__
        except Exception:
            pass

        # Get Docker image digest if available
        try:
            result = subprocess.run(
                ["docker", "images", "--digests", "--format", "{{.Digest}}", DependencyChecker.DOCKER_IMAGE],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                env["docker_image"] = f"{DependencyChecker.DOCKER_IMAGE}@{result.stdout.strip()}"
        except Exception:
            pass

        return env


class HTMLReportGenerator:
    """Generate HTML report from results.json."""

    def __init__(self):
        pass

    def generate(self, results: dict, output_path: Path):
        """Build HTML from structured results."""
        output_path = Path(output_path)

        # CSS
        css = """
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
        h1 { text-align: center; color: #333; }
        .nav { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin: 20px 0; }
        .nav a { padding: 8px 16px; background: #fff; border-radius: 4px; text-decoration: none; color: #333; border: 1px solid #ddd; }
        .nav a:hover { background: #e0e0e0; }
        .figure { background: #fff; border-radius: 8px; padding: 20px; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .figure h2 { margin-top: 0; color: #333; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
        .cell { text-align: center; }
        .cell img { max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; }
        .cell img:hover { border-color: #007bff; }
        .cell .label { font-weight: bold; margin-bottom: 8px; }
        .cell .timing { font-size: 12px; color: #666; }
        .cell .status { font-size: 12px; padding: 2px 6px; border-radius: 3px; margin-top: 4px; display: inline-block; }
        .status.success { background: #d4edda; color: #155724; }
        .status.failed { background: #f8d7da; color: #721c24; }
        .status.skipped { background: #fff3cd; color: #856404; }
        .status.timeout { background: #cce5ff; color: #004085; }
        .summary { background: #fff; border-radius: 8px; padding: 20px; margin: 20px 0; }
        .summary table { width: 100%; border-collapse: collapse; }
        .summary th, .summary td { padding: 8px; text-align: left; border-bottom: 1px solid #eee; }
        .text-panel { background: #f8f9fa; padding: 10px; border-radius: 4px; margin-top: 10px; font-size: 12px; max-height: 200px; overflow-y: auto; }
        .text-panel pre { margin: 0; white-space: pre-wrap; }
        details { margin-top: 10px; }
        summary { cursor: pointer; color: #007bff; }
        """

        # Build HTML
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Figure Translation Comparison Report</title>
    <style>{css}</style>
</head>
<body>
    <h1>Figure Translation Comparison Report</h1>
    <p style="text-align:center; color:#666;">Generated: {results.get('run_timestamp', 'Unknown')}</p>

    <nav class="nav">
"""
        # Navigation
        for fig in results.get("figures", []):
            html += f'        <a href="#fig-{fig["figure_id"]}">{fig["figure_id"]}</a>\n'

        html += """    </nav>

    <div class="summary">
        <h2>Summary</h2>
        <table>
            <tr><th>Pipeline</th><th>Success</th><th>Failed</th><th>Skipped</th></tr>
"""
        for pipeline, stats in results.get("summary", {}).get("pipelines_run", {}).items():
            html += f'            <tr><td>{pipeline}</td><td>{stats["success"]}</td><td>{stats["failed"]}</td><td>{stats["skipped"]}</td></tr>\n'

        html += f"""        </table>
        <p>Total runtime: {results.get('summary', {}).get('total_runtime_seconds', 0):.1f}s</p>
    </div>
"""

        # Figures
        for fig in results.get("figures", []):
            html += f"""
    <div class="figure" id="fig-{fig['figure_id']}">
        <h2>{fig['figure_id']}: {fig.get('label', 'Unknown')}</h2>
        <p>Type: {fig.get('type', 'unknown')} | OCR regions: {len(fig.get('ocr', {}).get('regions', []))}</p>

        <div class="grid">
            <div class="cell">
                <div class="label">Original</div>
                <img src="figures/{fig['figure_id']}/original.png" alt="Original" loading="lazy">
            </div>
"""
            for pipeline_name in ComparisonRunner.PIPELINES:
                pipeline_data = fig.get("pipelines", {}).get(pipeline_name, {})
                status = pipeline_data.get("status", "unknown")
                timing = pipeline_data.get("timing_seconds")
                timing_str = f"{timing:.1f}s" if timing else "N/A"
                img_path = pipeline_data.get("output_path", "")

                # Make path relative
                if img_path:
                    img_path = f"figures/{fig['figure_id']}/{Path(img_path).name}"

                html += f"""            <div class="cell">
                <div class="label">{pipeline_name}</div>
"""
                if img_path and status == "success":
                    html += f'                <img src="{img_path}" alt="{pipeline_name}" loading="lazy">\n'
                else:
                    html += f'                <div style="padding:40px;background:#eee;border-radius:4px;">No image</div>\n'

                html += f"""                <div class="timing">{timing_str}</div>
                <span class="status {status}">{status}</span>
            </div>
"""

            # Text panel with OCR and translations
            html += """        </div>

        <details>
            <summary>OCR & Translations</summary>
            <div class="text-panel">
                <pre>"""

            for region in fig.get("ocr", {}).get("regions", []):
                html += f"[{region['id']}] {region['text']}\n"

            html += """</pre>
            </div>
        </details>
    </div>
"""

        html += """
</body>
</html>
"""

        # Write HTML
        output_path.write_text(html)
        print(f"HTML report saved to {output_path}")


def run_poc(
    image_path: str,
    ocr_engine: Literal["paddle", "easy"] = "paddle",
    method: Literal["gemini", "inpaint"] = "gemini",
    include_baseline: bool = False,
    output_dir: Optional[str] = None,
    max_retries: int = 0,
) -> POCResult:
    """Run the complete POC pipeline."""
    start = time.time()

    # Setup output directory
    if output_dir is None:
        output_dir = Path(image_path).parent / "poc_output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = POCResult(
        input_path=image_path,
        ocr=OCRResult(),
        translation=TranslationResult(),
        regeneration=RegenerationResult(),
    )

    # Step 1: OCR
    print(f"\n[1/3] Extracting text with {ocr_engine.upper()}...")
    engine: OCREngine
    if ocr_engine == "paddle":
        engine = PaddleOCREngine()
    else:
        engine = EasyOCREngine()

    result.ocr = engine.extract(image_path)

    if result.ocr.error:
        print(f"  ERROR: {result.ocr.error}")
        return result

    print(f"  Found {len(result.ocr.regions)} text regions in {result.ocr.extraction_time_ms:.0f}ms")
    for i, region in enumerate(result.ocr.regions):
        print(f"    [{i+1}] \"{region.text}\" (conf: {region.confidence:.2f})")

    if not result.ocr.regions:
        print("  No Chinese text found - skipping translation")
        result.total_time_ms = (time.time() - start) * 1000
        return result

    # Step 2: Translate
    print(f"\n[2/3] Translating {len(result.ocr.regions)} text regions...")
    translator = TextTranslator()
    result.translation = translator.translate_batch(result.ocr.regions)

    if result.translation.error:
        print(f"  ERROR: {result.translation.error}")
    else:
        print(f"  Translated {result.translation.total_chars} chars in {result.translation.translation_time_ms:.0f}ms")
        for region in result.translation.regions:
            print(f"    \"{region.text}\" → \"{region.translated}\"")

    # Step 3: Render translated image
    if method == "inpaint":
        print(f"\n[3/3] Rendering with inpaint + PIL overlay...")
        output_path = output_dir / f"{Path(image_path).stem}_inpaint_translated.png"

        renderer = InpaintRenderer()
        inpaint_result = renderer.translate(
            image_path=image_path,
            regions=result.translation.regions,
            output_path=str(output_path),
            save_debug=True,
        )

        # Convert InpaintResult to RegenerationResult for consistency
        result.regeneration = RegenerationResult(
            output_path=inpaint_result.output_path,
            regeneration_time_ms=inpaint_result.total_time_ms,
            error=inpaint_result.error,
        )

        if result.regeneration.error:
            print(f"  ERROR: {result.regeneration.error}")
        else:
            print(f"  Inpaint: {inpaint_result.inpaint_time_ms:.0f}ms")
            print(f"  Render: {inpaint_result.render_time_ms:.0f}ms")
            print(f"  Output: {result.regeneration.output_path}")

    else:  # method == "gemini"
        print(f"\n[3/3] Regenerating image with Gemini...")
        if max_retries > 0:
            print(f"  (with verification and up to {max_retries} correction retries)")

        output_path = output_dir / f"{Path(image_path).stem}_ocr_translated.png"

        # Pass OCR engine to regenerator for verification
        regenerator = GeminiRegenerator(ocr_engine=engine if max_retries > 0 else None)

        if max_retries > 0:
            result.regeneration = regenerator.regenerate_with_retry(
                image_path=image_path,
                translations=result.translation.regions,
                output_path=str(output_path),
                max_retries=max_retries,
                verify=True,
            )
        else:
            result.regeneration = regenerator.regenerate(
                image_path=image_path,
                translations=result.translation.regions,
                output_path=str(output_path),
            )

        if result.regeneration.error:
            print(f"  ERROR: {result.regeneration.error}")
        else:
            print(f"  Generated in {result.regeneration.regeneration_time_ms:.0f}ms")
            print(f"  Output: {result.regeneration.output_path}")

    # Step 4: Baseline comparison (optional)
    if include_baseline:
        print(f"\n[Baseline] Running current pipeline for comparison...")
        baseline_path = output_dir / f"{Path(image_path).stem}_baseline.png"

        baseline = BaselineTranslator()
        baseline_result = baseline.translate(image_path, str(baseline_path))

        if baseline_result and not baseline_result.startswith("Error"):
            result.baseline_path = baseline_result
            print(f"  Output: {result.baseline_path}")
        else:
            print(f"  ERROR: {baseline_result}")

    result.total_time_ms = (time.time() - start) * 1000

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total time: {result.total_time_ms:.0f}ms")
    print(f"  OCR: {result.ocr.extraction_time_ms:.0f}ms")
    print(f"  Translation: {result.translation.translation_time_ms:.0f}ms")
    print(f"  Regeneration: {result.regeneration.regeneration_time_ms:.0f}ms")
    print(f"\nOutputs:")
    print(f"  New approach: {result.regeneration.output_path}")
    if result.baseline_path:
        print(f"  Baseline: {result.baseline_path}")

    return result


def compare_results_with_gemini(
    original_path: str,
    new_approach_path: str,
    baseline_path: str,
) -> dict:
    """Use Gemini text model to compare translation quality."""
    try:
        import google.generativeai as genai
        from PIL import Image

        genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
        model = genai.GenerativeModel('gemini-2.0-flash-exp')

        original = Image.open(original_path)
        new_approach = Image.open(new_approach_path)
        baseline = Image.open(baseline_path)

        prompt = """Compare these three scientific figure images:

Image 1: ORIGINAL (Chinese)
Image 2: NEW APPROACH (OCR → Translate → Regenerate)
Image 3: BASELINE (Direct Gemini translation)

Evaluate each translated version (Image 2 and Image 3) on these criteria:

1. TEXT COHERENCE: Are the English translations complete sentences/phrases without gibberish?
   Rate 1-5: 1=gibberish, 5=perfectly coherent

2. LAYOUT PRESERVATION: Is the visual layout (data, lines, colors) preserved from original?
   Rate 1-5: 1=completely different, 5=identical to original

3. READABILITY: Is the text legible and properly sized?
   Rate 1-5: 1=unreadable, 5=perfectly readable

4. ACCURACY: Do the translations appear to be correct (if you can infer meaning)?
   Rate 1-5: 1=wrong, 5=appears accurate

Return a JSON object with this structure:
{
  "new_approach": {"coherence": N, "layout": N, "readability": N, "accuracy": N, "notes": "..."},
  "baseline": {"coherence": N, "layout": N, "readability": N, "accuracy": N, "notes": "..."},
  "winner": "new_approach" or "baseline" or "tie",
  "explanation": "..."
}"""

        response = model.generate_content([prompt, original, new_approach, baseline])

        # Parse response
        content = response.text.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            content = content.rsplit("```", 1)[0]

        return json.loads(content)

    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="POC: OCR-guided figure translation with multi-pipeline comparison",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Original POC arguments
    parser.add_argument("image_path", nargs="?", help="Path to figure image (required for single-image modes)")
    parser.add_argument(
        "--ocr",
        choices=["paddle", "easy"],
        default="paddle",
        help="OCR engine to use (default: paddle)",
    )
    parser.add_argument(
        "--method",
        choices=["gemini", "inpaint"],
        default="gemini",
        help="Translation method: gemini (AI regeneration) or inpaint (LaMa + PIL)",
    )
    parser.add_argument(
        "--compare-methods",
        action="store_true",
        help="Run both methods (gemini and inpaint) and compare results",
    )
    parser.add_argument(
        "--compare-ocr",
        action="store_true",
        help="Run both OCR engines and compare results",
    )
    parser.add_argument(
        "--include-baseline",
        action="store_true",
        help="Run current pipeline for comparison",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Use Gemini to evaluate translation quality (requires --include-baseline)",
    )
    parser.add_argument(
        "--retry",
        type=int,
        default=0,
        metavar="N",
        help="Verify output and retry with targeted corrections up to N times (default: 0 = no retry)",
    )
    parser.add_argument(
        "--output-dir",
        default="comparison_report",
        help="Output directory (default: comparison_report)",
    )

    # New comparison pipeline arguments
    parser.add_argument(
        "--smoke-test",
        metavar="IMAGE",
        help="Quick validation: run all pipelines on a single figure",
    )
    parser.add_argument(
        "--batch-compare",
        metavar="MANIFEST",
        help="Full comparison: run all pipelines on manifest figures (YAML file)",
    )
    parser.add_argument(
        "--download-manifest",
        metavar="MANIFEST",
        help="Download/normalize figures defined in manifest (YAML file)",
    )
    parser.add_argument(
        "--regenerate-html",
        metavar="RESULTS_JSON",
        help="Regenerate HTML report from existing results.json",
    )
    parser.add_argument(
        "--check-deps",
        action="store_true",
        help="Check dependencies (Docker, GPU, API keys) and exit",
    )

    args = parser.parse_args()

    # Handle dependency check mode
    if args.check_deps:
        print("Checking dependencies...")
        DependencyChecker.print_status()
        sys.exit(0)

    # Handle smoke-test mode
    if args.smoke_test:
        if not os.path.exists(args.smoke_test):
            print(f"Error: Image not found: {args.smoke_test}")
            sys.exit(1)

        print("=" * 60)
        print("SMOKE TEST: Multi-Pipeline Comparison")
        print("=" * 60)

        # Check dependencies first
        print("\nChecking dependencies...")
        DependencyChecker.print_status()

        # Run comparison
        output_dir = Path(args.output_dir)
        runner = ComparisonRunner(output_dir)

        print(f"\nRunning smoke test on: {args.smoke_test}")
        result = runner.run_figure({
            "id": "smoke_test",
            "source": args.smoke_test,
            "type": "smoke_test",
            "label": Path(args.smoke_test).name,
        })

        # Generate HTML report - convert dataclass to dict for JSON serialization
        result_dict = asdict(result)
        results = {
            "run_id": f"smoke_{int(time.time())}",
            "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "figures": [result_dict],
            "summary": {
                "total_figures": 1,
                "pipelines_run": {},
            },
        }

        # Count pipeline results
        for pipeline_name, pipeline_result in result.pipelines.items():
            status = pipeline_result.get("status", "unknown")
            if pipeline_name not in results["summary"]["pipelines_run"]:
                results["summary"]["pipelines_run"][pipeline_name] = {"success": 0, "failed": 0, "skipped": 0}
            results["summary"]["pipelines_run"][pipeline_name][status] = 1

        # Save results.json
        results_path = output_dir / "results.json"
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {results_path}")

        # Generate HTML
        generator = HTMLReportGenerator()
        html_path = output_dir / "index.html"
        generator.generate(results, html_path)
        print(f"HTML report: {html_path}")

        # Summary
        print("\n" + "=" * 60)
        print("SMOKE TEST RESULTS")
        print("=" * 60)
        for pipeline_name, pipeline_result in result.pipelines.items():
            status = pipeline_result.get("status", "unknown")
            timing = pipeline_result.get("timing_seconds")
            status_icon = "✓" if status == "success" else ("⊘" if status == "skipped" else "✗")
            timing_str = f"({timing:.1f}s)" if timing is not None else ""
            print(f"  {pipeline_name}: {status_icon} {status} {timing_str}")

        sys.exit(0)

    # Handle batch-compare mode
    if args.batch_compare:
        if not os.path.exists(args.batch_compare):
            print(f"Error: Manifest not found: {args.batch_compare}")
            sys.exit(1)

        print("=" * 60)
        print("BATCH COMPARISON: Multi-Pipeline")
        print("=" * 60)

        # Check dependencies
        print("\nChecking dependencies...")
        DependencyChecker.print_status()

        # Run comparison
        output_dir = Path(args.output_dir)
        runner = ComparisonRunner(output_dir)

        results = runner.run_all(Path(args.batch_compare))

        # Save results.json
        results_path = output_dir / "results.json"
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {results_path}")

        # Generate HTML
        generator = HTMLReportGenerator()
        html_path = output_dir / "index.html"
        generator.generate(results, html_path)
        print(f"HTML report: {html_path}")

        sys.exit(0)

    # Handle regenerate-html mode
    if args.regenerate_html:
        if not os.path.exists(args.regenerate_html):
            print(f"Error: Results file not found: {args.regenerate_html}")
            sys.exit(1)

        with open(args.regenerate_html) as f:
            results = json.load(f)

        output_dir = Path(args.regenerate_html).parent
        generator = HTMLReportGenerator()
        html_path = output_dir / "index.html"
        generator.generate(results, html_path)
        print(f"HTML report regenerated: {html_path}")

        sys.exit(0)

    # Handle download-manifest mode
    if args.download_manifest:
        print("Error: --download-manifest not yet implemented")
        print("Please manually download figures and update manifest with local paths.")
        sys.exit(1)

    # Original POC mode - requires image_path
    if not args.image_path:
        parser.print_help()
        print("\nError: image_path is required for single-image mode")
        sys.exit(1)

    if not os.path.exists(args.image_path):
        print(f"Error: Image not found: {args.image_path}")
        sys.exit(1)

    # Check environment
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("Error: OPENROUTER_API_KEY not set")
        sys.exit(1)

    # Gemini API key only required for gemini method
    if args.method == "gemini" or args.compare_methods:
        if not os.environ.get("GEMINI_API_KEY"):
            print("Error: GEMINI_API_KEY not set (required for gemini method)")
            sys.exit(1)

    if args.compare_methods:
        print("="*60)
        print("Comparing Translation Methods")
        print("="*60)

        print("\n--- GEMINI METHOD ---")
        result_gemini = run_poc(
            args.image_path,
            ocr_engine=args.ocr,
            method="gemini",
            include_baseline=False,
            output_dir=args.output_dir,
            max_retries=args.retry,
        )

        print("\n--- INPAINT METHOD ---")
        result_inpaint = run_poc(
            args.image_path,
            ocr_engine=args.ocr,
            method="inpaint",
            include_baseline=False,
            output_dir=args.output_dir,
            max_retries=0,  # No retry for inpaint
        )

        print("\n" + "="*60)
        print("METHOD COMPARISON")
        print("="*60)
        print(f"Gemini:  {result_gemini.regeneration.regeneration_time_ms:.0f}ms → {result_gemini.regeneration.output_path}")
        print(f"Inpaint: {result_inpaint.regeneration.regeneration_time_ms:.0f}ms → {result_inpaint.regeneration.output_path}")
        print("\nOpen both files to compare quality visually.")

    elif args.compare_ocr:
        print("="*60)
        print("Comparing OCR engines")
        print("="*60)

        print("\n--- PaddleOCR ---")
        result_paddle = run_poc(
            args.image_path,
            ocr_engine="paddle",
            method=args.method,
            include_baseline=False,
            output_dir=args.output_dir,
            max_retries=args.retry,
        )

        print("\n--- EasyOCR ---")
        result_easy = run_poc(
            args.image_path,
            ocr_engine="easy",
            method=args.method,
            include_baseline=args.include_baseline,
            output_dir=args.output_dir,
            max_retries=args.retry,
        )

        print("\n" + "="*60)
        print("OCR COMPARISON")
        print("="*60)
        print(f"PaddleOCR: {len(result_paddle.ocr.regions)} regions, {result_paddle.ocr.extraction_time_ms:.0f}ms")
        print(f"EasyOCR: {len(result_easy.ocr.regions)} regions, {result_easy.ocr.extraction_time_ms:.0f}ms")

    else:
        result = run_poc(
            args.image_path,
            ocr_engine=args.ocr,
            method=args.method,
            include_baseline=args.include_baseline,
            output_dir=args.output_dir,
            max_retries=args.retry,
        )

        # Evaluate if requested
        if args.evaluate and args.include_baseline:
            if result.regeneration.output_path and result.baseline_path:
                print("\n" + "="*60)
                print("QUALITY EVALUATION (by Gemini)")
                print("="*60)

                eval_result = compare_results_with_gemini(
                    args.image_path,
                    result.regeneration.output_path,
                    result.baseline_path,
                )

                if "error" in eval_result:
                    print(f"Evaluation error: {eval_result['error']}")
                else:
                    print(json.dumps(eval_result, indent=2))
            else:
                print("\nCannot evaluate: missing outputs")


if __name__ == "__main__":
    main()
