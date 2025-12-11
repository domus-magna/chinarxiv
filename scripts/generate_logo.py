#!/usr/bin/env python3
"""
Generate logo concepts using Gemini 3 Pro Image (Nano Banana Pro).

Uses REST API directly (no SDK) for text-to-image and image-to-image generation.

Usage examples:
  python scripts/generate_logo.py                           # default ChinaRxiv prompt
  python scripts/generate_logo.py --prompt "A red dragon"   # custom prompt
  python scripts/generate_logo.py --count 10                # 10 variations
  python scripts/generate_logo.py --size 2K --aspect 16:9   # banner size

  # Image-to-image with reference (must be PNG):
  python scripts/generate_logo.py --reference logo.png --prompt "Use this exact logo..."
"""
from __future__ import annotations

import argparse
import base64
import os
import time
from pathlib import Path
from typing import Optional

import sys
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from src.logging_utils import log  # noqa: E402

# API config
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL = "gemini-3-pro-image-preview"  # Nano Banana Pro

DEFAULT_PROMPT = """Design a minimalist favicon for "ChinaRxiv", an academic preprint site
that translates Chinese research papers to English. The logo should:
- Work at 32x32 and 64x64 pixel sizes
- Use a simple, recognizable symbol
- Suggest academic papers, translation, or China
- Be distinctive from arXiv (which uses a stylized red X)
- Use 1-2 colors maximum for clarity at small sizes
- Have a clean, modern aesthetic suitable for a scholarly website"""


def get_api_key() -> str:
    """Get API key from environment."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY not set")
    return key


def load_reference_image(reference_path: Path) -> str:
    """
    Load and encode a reference image as base64.

    Args:
        reference_path: Path to PNG image file

    Returns:
        Base64-encoded image data

    Raises:
        ValueError: If file is not PNG or doesn't exist
    """
    if not reference_path.exists():
        raise ValueError(f"Reference image not found: {reference_path}")

    if reference_path.suffix.lower() != ".png":
        raise ValueError(f"Reference image must be PNG format, got: {reference_path.suffix}")

    with open(reference_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def generate_image(
    prompt: str,
    output_path: Path,
    size: str = "1K",
    aspect_ratio: str = "1:1",
    reference_path: Optional[Path] = None,
    timeout: tuple[int, int] = (10, 120),
) -> bool:
    """
    Generate an image from text prompt, optionally using a reference image.

    Args:
        prompt: Text description of desired image
        output_path: Where to save the generated image
        size: Image size - "1K", "2K", or "4K"
        aspect_ratio: e.g., "1:1", "16:9", "4:3"
        reference_path: Optional PNG image to use as reference
        timeout: (connect_timeout, read_timeout) in seconds

    Returns:
        True if successful, False otherwise
    """
    api_key = get_api_key()
    endpoint = f"{API_BASE}/{MODEL}:generateContent"

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    # Build parts list - text prompt first, then optional reference image
    parts = [{"text": prompt}]

    if reference_path:
        try:
            b64_image = load_reference_image(reference_path)
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": b64_image
                }
            })
            log(f"Using reference image: {reference_path}")
        except ValueError as e:
            log(f"Error loading reference: {e}")
            return False

    payload = {
        "contents": [{
            "parts": parts
        }],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
                "imageSize": size,
            }
        }
    }

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        log("Request timeout")
        return False
    except requests.exceptions.RequestException as e:
        log(f"Network error: {e}")
        return False

    if not response.ok:
        log(f"API error {response.status_code}: {response.text[:200]}")
        return False

    try:
        data = response.json()
    except ValueError as e:
        log(f"Invalid JSON: {e}")
        return False

    # Extract image from response
    candidates = data.get("candidates", [])
    if not candidates:
        feedback = data.get("promptFeedback", {})
        if feedback:
            block_reason = feedback.get("blockReason", "unknown")
            log(f"Request blocked: {block_reason}")
        else:
            log("No candidates in response")
        return False

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])

    for part in parts:
        if "inlineData" in part:
            inline_data = part["inlineData"]
            b64_image = inline_data.get("data", "")
            if b64_image:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(b64_image))
                return True

    # Check for text response (model couldn't generate image)
    for part in parts:
        if "text" in part:
            log(f"Model returned text: {part['text'][:200]}")

    log("No image in response")
    return False


def generate_preview_html(output_dir: Path, images: list[Path]) -> Path:
    """Generate an HTML preview page for comparing generated images."""
    html_path = output_dir / "preview.html"

    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Logo Candidates</title>
    <style>
        body { font-family: system-ui; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        h1 { color: #333; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 20px; }
        .card { background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .card img { width: 100%; height: auto; border-radius: 4px; }
        .card .name { margin-top: 10px; font-size: 14px; color: #666; text-align: center; }
        .preview-sizes { display: flex; gap: 10px; align-items: center; margin-top: 10px; justify-content: center; }
        .preview-sizes img { width: auto; }
        .size-64 { width: 64px; height: 64px; }
        .size-32 { width: 32px; height: 32px; }
        .size-16 { width: 16px; height: 16px; }
    </style>
</head>
<body>
    <h1>Logo Candidates</h1>
    <p>Generated with Gemini 3 Pro Image (Nano Banana Pro)</p>
    <div class="grid">
"""

    for img in sorted(images):
        html += f"""        <div class="card">
            <img src="{img.name}" alt="{img.stem}">
            <div class="name">{img.stem}</div>
            <div class="preview-sizes">
                <img src="{img.name}" class="size-64" title="64x64">
                <img src="{img.name}" class="size-32" title="32x32">
                <img src="{img.name}" class="size-16" title="16x16">
            </div>
        </div>
"""

    html += """    </div>
</body>
</html>
"""

    html_path.write_text(html)
    return html_path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate logo concepts using Gemini 3 Pro Image (Nano Banana Pro)"
    )
    ap.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Text prompt describing the desired image",
    )
    ap.add_argument(
        "--count",
        type=int,
        default=5,
        help="Number of variations to generate (default: 5)",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path("site/assets/logo_candidates"),
        help="Directory to save generated images (default: site/assets/logo_candidates)",
    )
    ap.add_argument(
        "--size",
        choices=["1K", "2K", "4K"],
        default="1K",
        help="Image resolution (default: 1K)",
    )
    ap.add_argument(
        "--aspect-ratio",
        default="1:1",
        help="Aspect ratio e.g., 1:1, 16:9, 4:3 (default: 1:1)",
    )
    ap.add_argument(
        "--prefix",
        default="logo",
        help="Filename prefix (default: logo)",
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between API calls in seconds (default: 2.0)",
    )
    ap.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="Reference PNG image for image-to-image generation (must be PNG format)",
    )
    args = ap.parse_args()

    # Validate reference image upfront if provided
    if args.reference:
        if not args.reference.exists():
            log(f"Error: Reference image not found: {args.reference}")
            return 1
        if args.reference.suffix.lower() != ".png":
            log(f"Error: Reference image must be PNG format, got: {args.reference.suffix}")
            return 1
        log(f"Using reference image: {args.reference}")

    # Validate API key
    try:
        get_api_key()
    except ValueError as e:
        log(f"Error: {e}")
        return 1

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    log(f"Generating {args.count} logo variations...")
    log(f"Output directory: {output_dir}")
    log(f"Size: {args.size}, Aspect ratio: {args.aspect_ratio}")
    log("")

    generated = []
    failed = 0

    for i in range(args.count):
        output_path = output_dir / f"{args.prefix}_{i+1:03d}.png"
        log(f"[{i+1}/{args.count}] Generating {output_path.name}...")

        if generate_image(
            prompt=args.prompt,
            output_path=output_path,
            size=args.size,
            aspect_ratio=args.aspect_ratio,
            reference_path=args.reference,
        ):
            log(f"[{i+1}/{args.count}] ✓ Saved {output_path.name}")
            generated.append(output_path)
        else:
            log(f"[{i+1}/{args.count}] ✗ Failed")
            failed += 1

        # Rate limiting
        if i < args.count - 1:
            time.sleep(args.delay)

    log("")
    log(f"Summary: {len(generated)} generated, {failed} failed")

    if generated:
        preview_path = generate_preview_html(output_dir, generated)
        log(f"Preview page: {preview_path}")
        log(f"Open with: open {preview_path}")

    return 0 if generated else 1


if __name__ == "__main__":
    raise SystemExit(main())
