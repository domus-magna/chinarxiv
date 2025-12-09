#!/usr/bin/env python3
"""
Run prompt variant comparison across multiple test images.

Usage:
    python scripts/run_prompt_comparison.py --input-dir data/test_figures/prompt_test_20 --output-dir /tmp/prompt_comparison
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.figure_translation_poc import (
    GeminiRegenerator,
    KimiTranslator,
    EasyOCREngine,
    OCRResult,
)


def run_ocr_kimi_gemini(
    image_path: str,
    output_path: str,
    prompt_variant: str = "baseline",
) -> dict:
    """Run the ocr_kimi_gemini pipeline with a specific prompt variant."""
    result = {
        "image": os.path.basename(image_path),
        "prompt_variant": prompt_variant,
        "status": "pending",
        "time_seconds": 0,
        "error": None,
        "output_path": None,
    }

    start = time.time()

    try:
        # Step 1: OCR
        ocr = EasyOCREngine()
        ocr_result = ocr.extract(image_path)
        if ocr_result.error:
            result["status"] = "ocr_failed"
            result["error"] = ocr_result.error
            return result

        if not ocr_result.regions:
            result["status"] = "no_text"
            result["error"] = "No text regions found"
            return result

        # Step 2: Translate with Kimi
        kimi = KimiTranslator()
        texts = [r.text for r in ocr_result.regions]
        translations, error = kimi.translate(texts)

        if error:
            result["status"] = "translation_failed"
            result["error"] = error
            return result

        # Apply translations to regions
        for i, trans in enumerate(translations):
            if i < len(ocr_result.regions):
                ocr_result.regions[i].translated = trans

        # Step 3: Regenerate with Gemini using specified prompt variant
        gemini = GeminiRegenerator()
        regen_result = gemini.regenerate(
            image_path,
            ocr_result.regions,
            output_path,
            prompt_variant=prompt_variant,
        )

        if regen_result.error:
            result["status"] = "regeneration_failed"
            result["error"] = regen_result.error
            return result

        result["status"] = "success"
        result["output_path"] = regen_result.output_path
        result["ocr_regions"] = len(ocr_result.regions)
        result["translations"] = len(translations)

    except Exception as e:
        import traceback
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"

    result["time_seconds"] = round(time.time() - start, 1)
    return result


def generate_html_report(results: list[dict], output_dir: Path) -> str:
    """Generate HTML comparison report."""
    # Group results by image
    by_image = {}
    for r in results:
        img = r["image"]
        if img not in by_image:
            by_image[img] = {}
        by_image[img][r["prompt_variant"]] = r

    # Get all variants
    variants = sorted(set(r["prompt_variant"] for r in results))

    # Build HTML
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Prompt Variant Comparison</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
        h1 {{ text-align: center; color: #333; }}
        .summary {{ background: #fff; border-radius: 8px; padding: 20px; margin: 20px 0; }}
        .summary table {{ width: 100%; border-collapse: collapse; }}
        .summary th, .summary td {{ padding: 8px; text-align: left; border-bottom: 1px solid #eee; }}
        .figure {{ background: #fff; border-radius: 8px; padding: 20px; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .figure h2 {{ margin-top: 0; color: #333; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; }}
        .cell {{ text-align: center; }}
        .cell img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; }}
        .cell img:hover {{ border-color: #007bff; }}
        .cell .label {{ font-weight: bold; margin-bottom: 8px; }}
        .cell .timing {{ font-size: 12px; color: #666; }}
        .status {{ font-size: 12px; padding: 2px 6px; border-radius: 3px; margin-top: 4px; display: inline-block; }}
        .status.success {{ background: #d4edda; color: #155724; }}
        .status.failed {{ background: #f8d7da; color: #721c24; }}
        nav {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin: 20px 0; }}
        nav a {{ padding: 8px 16px; background: #fff; border-radius: 4px; text-decoration: none; color: #333; border: 1px solid #ddd; }}
        nav a:hover {{ background: #e0e0e0; }}
    </style>
</head>
<body>
    <h1>Prompt Variant Comparison</h1>
    <p style="text-align:center; color:#666;">Generated: {datetime.utcnow().isoformat()}Z</p>

    <nav>
"""

    # Navigation links
    for img in by_image.keys():
        safe_id = img.replace(".", "_").replace(" ", "_")
        html += f'        <a href="#fig-{safe_id}">{img[:30]}</a>\n'

    html += """    </nav>

    <div class="summary">
        <h2>Summary</h2>
        <table>
            <tr><th>Prompt Variant</th><th>Success</th><th>Failed</th><th>Avg Time</th></tr>
"""

    # Summary stats per variant
    for variant in variants:
        variant_results = [r for r in results if r["prompt_variant"] == variant]
        success = sum(1 for r in variant_results if r["status"] == "success")
        failed = len(variant_results) - success
        times = [r["time_seconds"] for r in variant_results if r["status"] == "success"]
        avg_time = f"{sum(times) / len(times):.1f}s" if times else "N/A"
        html += f"            <tr><td>{variant}</td><td>{success}</td><td>{failed}</td><td>{avg_time}</td></tr>\n"

    html += """        </table>
    </div>
"""

    # Individual figures
    for img, variants_dict in by_image.items():
        safe_id = img.replace(".", "_").replace(" ", "_")
        html += f"""
    <div class="figure" id="fig-{safe_id}">
        <h2>{img}</h2>
        <div class="grid">
            <div class="cell">
                <div class="label">Original</div>
                <img src="figures/{img}/original.png" alt="Original" loading="lazy">
            </div>
"""

        for variant in variants:
            r = variants_dict.get(variant, {})
            status = r.get("status", "N/A")
            timing = f"{r.get('time_seconds', 0)}s" if r else "N/A"
            status_class = "success" if status == "success" else "failed"

            if status == "success" and r.get("output_path"):
                ext = os.path.splitext(r["output_path"])[1] or ".png"
                img_tag = f'<img src="figures/{img}/{variant}{ext}" alt="{variant}" loading="lazy">'
            else:
                error_msg = r.get("error", "Unknown error")[:50] if r else "No data"
                img_tag = f'<div style="padding:40px;background:#eee;border-radius:4px;">Failed: {error_msg}</div>'

            html += f"""            <div class="cell">
                <div class="label">{variant}</div>
                {img_tag}
                <div class="timing">{timing}</div>
                <span class="status {status_class}">{status}</span>
            </div>
"""

        html += """        </div>
    </div>
"""

    html += """
</body>
</html>
"""

    return html


def main():
    parser = argparse.ArgumentParser(description="Run prompt variant comparison")
    parser.add_argument("--input-dir", required=True, help="Directory with test images")
    parser.add_argument("--output-dir", required=True, help="Output directory for results")
    parser.add_argument("--variants", default="baseline,concise,narrative,preserve_first,edit_style",
                       help="Comma-separated list of prompt variants to test")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of images to process (0=all)")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    variants = [v.strip() for v in args.variants.split(",")]

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(exist_ok=True)

    # Find test images
    images = []
    for ext in ["*.jpeg", "*.jpg", "*.png"]:
        images.extend(input_dir.glob(ext))
    images = sorted(images)

    if args.limit > 0:
        images = images[:args.limit]

    print(f"Found {len(images)} images to process")
    print(f"Testing {len(variants)} prompt variants: {variants}")
    print(f"Total runs: {len(images) * len(variants)}")
    print("=" * 60)

    all_results = []

    for i, img_path in enumerate(images, 1):
        img_name = img_path.name
        print(f"\n[{i}/{len(images)}] Processing: {img_name}")

        # Create output directory for this image
        img_output_dir = figures_dir / img_name
        img_output_dir.mkdir(exist_ok=True)

        # Copy original
        shutil.copy2(img_path, img_output_dir / "original.png")

        for variant in variants:
            print(f"  [{variant}] Running...", end=" ", flush=True)

            output_path = str(img_output_dir / f"{variant}.png")
            result = run_ocr_kimi_gemini(str(img_path), output_path, variant)
            all_results.append(result)

            status_symbol = "✓" if result["status"] == "success" else "✗"
            print(f"{status_symbol} {result['status']} ({result['time_seconds']}s)")

            if result.get("error"):
                print(f"      Error: {result['error'][:100]}")

    # Save results JSON
    results_path = output_dir / "results.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to: {results_path}")

    # Generate HTML report
    html = generate_html_report(all_results, output_dir)
    html_path = output_dir / "index.html"
    with open(html_path, "w") as f:
        f.write(html)
    print(f"HTML report saved to: {html_path}")

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for variant in variants:
        variant_results = [r for r in all_results if r["prompt_variant"] == variant]
        success = sum(1 for r in variant_results if r["status"] == "success")
        print(f"  {variant}: {success}/{len(variant_results)} success")


if __name__ == "__main__":
    main()
