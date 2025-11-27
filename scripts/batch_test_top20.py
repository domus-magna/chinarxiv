#!/usr/bin/env python3
"""
Batch test figure translation on 20 papers with moderate figure counts.
Picks papers with 10-60 figures for cost-effective yet meaningful testing.
"""
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

import requests
import fitz  # PyMuPDF

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass
class PaperResult:
    """Result for a single paper."""
    paper_id: str
    total_figures: int
    figures_with_chinese: int
    figures_translated: int
    figures_passed_qa: int
    translation_cost: float
    errors: list = field(default_factory=list)


def get_openrouter_balance(api_key: str) -> float:
    """Fetch current OpenRouter balance."""
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(5, 30),
        )
        if response.ok:
            data = response.json()
            credits = data.get("data", {})
            total = credits.get("total_credits", 0)
            used = credits.get("total_usage", 0)
            return total - used
    except Exception:
        pass
    return 0.0


def count_figures_in_pdf(pdf_path: str) -> int:
    """Count substantial figures in a PDF."""
    try:
        doc = fitz.open(pdf_path)
        count = 0
        for page in doc:
            images = page.get_images(full=True)
            for img in images:
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                    if base_image and len(base_image.get("image", b"")) > 5000:
                        count += 1
                except Exception:
                    pass
        doc.close()
        return count
    except Exception:
        return 0


def find_best_papers(pdf_dir: Path, min_figs: int = 10, max_figs: int = 60, limit: int = 20) -> List[tuple]:
    """Find papers with moderate figure counts."""
    results = []

    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        count = count_figures_in_pdf(str(pdf_path))
        if min_figs <= count <= max_figs:
            results.append((pdf_path.stem, count, pdf_path))

    # Sort by figure count descending, take top N
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]


def main():
    print("=" * 70)
    print("BATCH TEST: 20 Papers with Moderate Figure Counts")
    print("=" * 70)

    # Get API keys
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    moondream_key = os.environ.get("MOONDREAM_API_KEY")

    if not openrouter_key:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)
    if not moondream_key:
        print("ERROR: MOONDREAM_API_KEY not set")
        sys.exit(1)

    # Get initial balance
    balance_before = get_openrouter_balance(openrouter_key)
    print(f"\nOpenRouter balance before: ${balance_before:.4f}")

    # Find papers with moderate figure counts
    pdf_dir = Path(__file__).parent.parent / "data/pdfs"
    print(f"\nScanning {pdf_dir} for papers with 10-60 figures...")

    papers = find_best_papers(pdf_dir, min_figs=10, max_figs=60, limit=20)

    if len(papers) < 20:
        print(f"Warning: Only found {len(papers)} papers with 10-60 figures")

    print(f"\nSelected {len(papers)} papers for testing:")
    print("-" * 50)
    for paper_id, count, _ in papers:
        print(f"  {paper_id}: {count} figures")

    total_expected_figures = sum(c for _, c, _ in papers)
    estimated_cost = total_expected_figures * 0.33
    print(f"\nTotal expected figures: {total_expected_figures}")
    print(f"Estimated max cost: ${estimated_cost:.2f}")

    # Confirm
    print("\n" + "=" * 70)
    print("Starting batch translation...")
    print("=" * 70)

    # Import pipeline components
    from figure_pipeline.extractor import FigureExtractor
    from figure_pipeline.validator import FigureValidator
    from figure_pipeline.translator import FigureTranslator

    extractor = FigureExtractor()
    validator = FigureValidator()
    translator = FigureTranslator()

    output_base = Path("/tmp/batch_test_20")
    output_base.mkdir(exist_ok=True)

    results: List[PaperResult] = []
    total_moondream_calls = 0

    for i, (paper_id, expected_figs, pdf_path) in enumerate(papers, 1):
        print(f"\n[{i}/{len(papers)}] Processing: {paper_id} ({expected_figs} expected figures)")

        result = PaperResult(
            paper_id=paper_id,
            total_figures=0,
            figures_with_chinese=0,
            figures_translated=0,
            figures_passed_qa=0,
            translation_cost=0.0,
        )

        try:
            # Extract figures
            output_dir = output_base / paper_id
            figures = extractor.extract_all(str(pdf_path), str(output_dir / "extracted"))

            # Filter substantial
            substantial = [
                f for f in figures
                if os.path.exists(f.original_path) and os.path.getsize(f.original_path) > 5000
            ]
            result.total_figures = len(substantial)
            print(f"  Extracted {len(figures)} figures, {len(substantial)} substantial")

            # Process each figure (limit to first 10 per paper for cost control)
            for fig in substantial[:10]:
                total_moondream_calls += 1  # should_translate check

                try:
                    should_translate = validator.should_translate(fig.original_path)

                    if not should_translate:
                        continue

                    result.figures_with_chinese += 1

                    # QA check function
                    def check_chinese(image_path: str) -> bool:
                        from PIL import Image
                        nonlocal total_moondream_calls
                        total_moondream_calls += 1
                        img = Image.open(image_path)
                        r = validator.model.query(img, "Does this image contain any Chinese characters? Answer yes or no.")
                        return "yes" in r.get("answer", "").lower()

                    # Translate
                    translated_path = translator.translate(
                        fig.original_path,
                        fig.figure_number,
                        paper_id,
                        str(output_dir / "translated"),
                        max_passes=3,
                        qa_check=check_chinese,
                    )

                    if translated_path:
                        result.figures_translated += 1

                        # Final QA
                        total_moondream_calls += 3  # qa_translation uses 3 queries
                        qa = validator.qa_translation(fig.original_path, translated_path)
                        if qa.get("passed"):
                            result.figures_passed_qa += 1

                    time.sleep(0.5)  # Rate limit

                except Exception as e:
                    result.errors.append(f"Fig {fig.figure_number}: {e}")

            results.append(result)

        except Exception as e:
            result.errors.append(f"Paper error: {e}")
            results.append(result)

        # Rate limit between papers
        time.sleep(1)

    # Get final balance
    balance_after = get_openrouter_balance(openrouter_key)
    total_cost = balance_before - balance_after

    # Summary
    print("\n" + "=" * 70)
    print("BATCH TEST RESULTS")
    print("=" * 70)

    # Top 5 by figures
    top_5 = sorted(results, key=lambda r: r.total_figures, reverse=True)[:5]
    print("\nTOP 5 PAPERS BY FIGURE COUNT:")
    print("-" * 60)
    print(f"{'Paper ID':<35} {'Total':>8} {'Chinese':>10} {'Translated':>12}")
    print("-" * 60)
    for r in top_5:
        print(f"{r.paper_id:<35} {r.total_figures:>8} {r.figures_with_chinese:>10} {r.figures_translated:>12}")

    # Overall stats
    print("\n" + "-" * 60)
    print("OVERALL STATISTICS:")
    print("-" * 60)
    print(f"Papers processed:        {len(results)}")
    print(f"Total figures found:     {sum(r.total_figures for r in results)}")
    print(f"Figures with Chinese:    {sum(r.figures_with_chinese for r in results)}")
    print(f"Figures translated:      {sum(r.figures_translated for r in results)}")
    print(f"Figures passed QA:       {sum(r.figures_passed_qa for r in results)}")

    print("\n" + "-" * 60)
    print("COST SUMMARY:")
    print("-" * 60)
    print(f"OpenRouter balance before: ${balance_before:.4f}")
    print(f"OpenRouter balance after:  ${balance_after:.4f}")
    print(f"OpenRouter cost (Gemini):  ${total_cost:.4f}")
    print(f"Moondream API calls:       {total_moondream_calls}")
    print(f"Moondream cost:            $0.00 (free tier)")
    print(f"TOTAL COST:                ${total_cost:.4f}")

    translated = sum(r.figures_translated for r in results)
    if translated > 0:
        print(f"\nCost per translated figure: ${total_cost/translated:.4f}")

    # Errors
    all_errors = []
    for r in results:
        for e in r.errors:
            all_errors.append(f"{r.paper_id}: {e}")

    if all_errors:
        print("\n" + "-" * 60)
        print("ERRORS:")
        print("-" * 60)
        for e in all_errors[:10]:
            print(f"  - {e}")
        if len(all_errors) > 10:
            print(f"  ... and {len(all_errors) - 10} more")

    # Save summary
    summary_file = output_base / "batch_summary.txt"
    with open(summary_file, "w") as f:
        f.write(f"Batch Test Results\n")
        f.write(f"==================\n\n")
        f.write(f"Papers: {len(results)}\n")
        f.write(f"Total figures: {sum(r.total_figures for r in results)}\n")
        f.write(f"With Chinese: {sum(r.figures_with_chinese for r in results)}\n")
        f.write(f"Translated: {sum(r.figures_translated for r in results)}\n")
        f.write(f"Passed QA: {sum(r.figures_passed_qa for r in results)}\n")
        f.write(f"Total cost: ${total_cost:.4f}\n\n")
        f.write("Top 5 by figure count:\n")
        for r in top_5:
            f.write(f"  {r.paper_id}: {r.total_figures} figures\n")

    print(f"\nResults saved to: {summary_file}")
    print(f"Translated figures in: {output_base}")


if __name__ == "__main__":
    main()
