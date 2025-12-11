#!/usr/bin/env python3
"""
Ablation study for figure translation quality.

Tests:
1. Baseline prompt (single pass)
2. Stronger prompt (single pass)
3. Baseline prompt (iterative, up to 3 passes)
4. Stronger prompt (iterative, up to 3 passes)

Measures:
- Chinese text remaining (via Moondream)
- Number of passes needed
- Total API cost estimate
"""
import base64
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# Import Moondream for QA
import moondream as md  # noqa: E402


# ============================================================================
# Prompt Variants
# ============================================================================

PROMPT_BASELINE = """Edit this scientific figure: translate all Chinese text to English.

Requirements:
- Keep ALL visual elements exactly the same (colors, shapes, data, layout)
- Only change the text language from Chinese to English
- Preserve font sizes proportionally
- Keep numbers, mathematical symbols, and units unchanged
- If there are axis labels, translate them accurately
- Technical terms should be translated precisely for academic use

Generate a new version of this image with the Chinese text replaced by English translations."""

PROMPT_STRONG = """CRITICAL TASK: Translate EVERY Chinese character in this scientific figure to English.

You MUST:
1. Find ALL Chinese text - including small labels, axis titles, legends, annotations, watermarks, and captions
2. Translate EACH piece of Chinese text to accurate English
3. Leave ZERO Chinese characters in the output image
4. Preserve the exact visual layout, colors, data values, and design

IMPORTANT: Scan the ENTIRE image systematically:
- Top to bottom
- Left to right
- Check all corners and edges
- Check inside data points, bars, or other elements
- Check legends and keys
- Check titles and subtitles
- Check axis labels and tick marks
- Check any footnotes or annotations

If you see ANY Chinese character, translate it. The goal is 100% English output.

Generate a new image with ALL Chinese text replaced by English."""

PROMPT_ITERATIVE_FOLLOWUP = """This image still contains Chinese text that was not translated in the previous attempt.

FIND AND TRANSLATE the remaining Chinese characters. Look carefully at:
- Small labels that may have been missed
- Text inside or near data elements
- Legends, keys, and annotations
- Axis labels and tick marks
- Any text in corners or edges

The output should have ZERO Chinese characters remaining.

Generate an updated image with ALL remaining Chinese text translated to English."""


@dataclass
class TranslationResult:
    """Result of a translation attempt."""
    variant: str
    passes: int
    has_english: bool
    has_chinese: bool
    passed_qa: bool
    output_path: Optional[str]
    total_time: float


class AblationTest:
    """Run ablation tests on figure translation."""

    API_URL = "https://openrouter.ai/api/v1/chat/completions"
    MODEL = "google/gemini-3-pro-image-preview"

    def __init__(self):
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

        moondream_key = os.environ.get("MOONDREAM_API_KEY")
        if not moondream_key:
            raise ValueError("MOONDREAM_API_KEY not set")
        self.validator = md.vl(api_key=moondream_key)

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/",
            "X-Title": "chinaxiv-ablation",
        }

    def _image_to_base64(self, image_path: str) -> str:
        """Convert image to base64 data URL."""
        ext = Path(image_path).suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
        mime_type = mime_map.get(ext, "image/png")

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        return f"data:{mime_type};base64,{b64}"

    def _translate_once(self, image_path: str, prompt: str, output_path: str) -> Optional[str]:
        """Single translation pass."""
        data_url = self._image_to_base64(image_path)

        payload = {
            "model": self.MODEL,
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

        response = requests.post(
            self.API_URL,
            headers=self._get_headers(),
            json=payload,
            timeout=(10, 120),
        )

        if not response.ok:
            print(f"  API error: {response.status_code}")
            return None

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return None

        images = choices[0].get("message", {}).get("images", [])
        if not images:
            return None

        img_data = images[0]
        if img_data.get("type") == "image_url":
            img_url = img_data.get("image_url", {}).get("url", "")
            if img_url.startswith("data:"):
                parts = img_url.split(",", 1)
                if len(parts) == 2:
                    image_bytes = base64.b64decode(parts[1])
                    with open(output_path, "wb") as f:
                        f.write(image_bytes)
                    return output_path

        return None

    def _check_chinese(self, image_path: str) -> bool:
        """Check if image has Chinese text."""
        img = Image.open(image_path)
        result = self.validator.query(
            img,
            "Does this image contain any Chinese characters? Answer yes or no."
        )
        return "yes" in result.get("answer", "").lower()

    def _check_english(self, image_path: str) -> bool:
        """Check if image has English text."""
        img = Image.open(image_path)
        result = self.validator.query(
            img,
            "Does this image contain English text? Answer yes or no."
        )
        return "yes" in result.get("answer", "").lower()

    def run_variant(
        self,
        image_path: str,
        variant_name: str,
        prompt: str,
        output_dir: str,
        max_passes: int = 1,
        followup_prompt: Optional[str] = None,
    ) -> TranslationResult:
        """Run a single variant test."""
        print(f"\n{'='*60}")
        print(f"VARIANT: {variant_name}")
        print(f"{'='*60}")

        start_time = time.time()
        current_input = image_path
        final_output = None

        for pass_num in range(1, max_passes + 1):
            print(f"\n  Pass {pass_num}/{max_passes}...")

            # Use followup prompt for passes > 1
            current_prompt = prompt if pass_num == 1 else (followup_prompt or prompt)

            output_path = os.path.join(
                output_dir,
                f"{variant_name.replace(' ', '_')}_pass{pass_num}.jpg"
            )

            result = self._translate_once(current_input, current_prompt, output_path)

            if not result:
                print(f"  Translation failed on pass {pass_num}")
                break

            print(f"  Output saved: {output_path}")
            final_output = result

            # Check for Chinese text
            has_chinese = self._check_chinese(result)
            print(f"  Chinese remaining: {has_chinese}")

            if not has_chinese:
                print(f"  SUCCESS: No Chinese detected after pass {pass_num}")
                break

            # For next iteration, use current output as input
            current_input = result

            # Rate limiting between passes
            if pass_num < max_passes:
                time.sleep(2)

        total_time = time.time() - start_time

        # Final QA
        if final_output:
            has_english = self._check_english(final_output)
            has_chinese = self._check_chinese(final_output)
            passed = has_english and not has_chinese
        else:
            has_english = False
            has_chinese = True
            passed = False

        return TranslationResult(
            variant=variant_name,
            passes=pass_num,
            has_english=has_english,
            has_chinese=has_chinese,
            passed_qa=passed,
            output_path=final_output,
            total_time=total_time,
        )


def main():
    print("=" * 70)
    print("FIGURE TRANSLATION ABLATION STUDY")
    print("=" * 70)

    # Find test image
    test_image = "/tmp/figure_pipeline_test/extracted/fig_1.jpeg"
    if not os.path.exists(test_image):
        print(f"Test image not found: {test_image}")
        print("Run test_figure_pipeline.py first to extract a test image.")
        sys.exit(1)

    print(f"\nTest image: {test_image}")
    print(f"Size: {os.path.getsize(test_image) / 1024:.1f} KB")

    # Create output directory
    output_dir = "/tmp/ablation_test"
    os.makedirs(output_dir, exist_ok=True)

    tester = AblationTest()

    # Check original image
    print("\n--- Original Image Check ---")
    has_chinese = tester._check_chinese(test_image)
    has_english = tester._check_english(test_image)
    print(f"Original has Chinese: {has_chinese}")
    print(f"Original has English: {has_english}")

    results = []

    # Variant 1: Baseline single pass
    results.append(tester.run_variant(
        test_image,
        "baseline_single",
        PROMPT_BASELINE,
        output_dir,
        max_passes=1,
    ))

    # Variant 2: Strong prompt single pass
    results.append(tester.run_variant(
        test_image,
        "strong_single",
        PROMPT_STRONG,
        output_dir,
        max_passes=1,
    ))

    # Variant 3: Baseline iterative (up to 3 passes)
    results.append(tester.run_variant(
        test_image,
        "baseline_iterative",
        PROMPT_BASELINE,
        output_dir,
        max_passes=3,
        followup_prompt=PROMPT_ITERATIVE_FOLLOWUP,
    ))

    # Variant 4: Strong prompt iterative (up to 3 passes)
    results.append(tester.run_variant(
        test_image,
        "strong_iterative",
        PROMPT_STRONG,
        output_dir,
        max_passes=3,
        followup_prompt=PROMPT_ITERATIVE_FOLLOWUP,
    ))

    # Summary
    print("\n" + "=" * 70)
    print("ABLATION RESULTS SUMMARY")
    print("=" * 70)
    print(f"{'Variant':<25} {'Passes':<8} {'English':<10} {'Chinese':<10} {'QA':<8} {'Time':<10}")
    print("-" * 70)

    for r in results:
        print(f"{r.variant:<25} {r.passes:<8} {str(r.has_english):<10} {str(r.has_chinese):<10} "
              f"{'PASS' if r.passed_qa else 'FAIL':<8} {r.total_time:.1f}s")

    # Best result
    passed = [r for r in results if r.passed_qa]
    if passed:
        best = min(passed, key=lambda r: r.passes)
        print(f"\nBest variant: {best.variant} (passed in {best.passes} pass(es))")
    else:
        least_chinese = min(results, key=lambda r: (r.has_chinese, -r.has_english))
        print(f"\nNo variant passed QA. Best attempt: {least_chinese.variant}")

    print(f"\nOutput directory: {output_dir}")
    print("\nOpen all results:")
    print(f"  open {output_dir}/*.jpg")


if __name__ == "__main__":
    main()
