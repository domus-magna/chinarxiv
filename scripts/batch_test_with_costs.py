#!/usr/bin/env python3
"""
Batch test figure translation pipeline with cost benchmarking.

Tests 10 papers and tracks:
- OpenRouter balance before/after (Gemini costs)
- Moondream API calls (free tier: 5,000/day)
- Total figures processed
- Success/failure rates
"""
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass
class CostTracker:
    """Track API costs across the batch."""
    openrouter_balance_before: float = 0.0
    openrouter_balance_after: float = 0.0
    moondream_calls: int = 0
    translation_calls: int = 0
    figures_processed: int = 0
    figures_translated: int = 0
    figures_passed_qa: int = 0
    papers_processed: int = 0
    errors: list = field(default_factory=list)

    @property
    def openrouter_cost(self) -> float:
        """Calculate OpenRouter spend."""
        return self.openrouter_balance_before - self.openrouter_balance_after

    @property
    def moondream_cost(self) -> float:
        """Calculate Moondream cost (free tier: 5,000/day)."""
        # Moondream pricing: $0.0001 per query after free tier
        # Free tier covers 5,000 requests/day
        if self.moondream_calls <= 5000:
            return 0.0
        return (self.moondream_calls - 5000) * 0.0001

    def summary(self) -> str:
        """Generate cost summary."""
        lines = [
            "=" * 70,
            "BATCH TEST COST SUMMARY",
            "=" * 70,
            f"Papers processed: {self.papers_processed}",
            f"Figures found: {self.figures_processed}",
            f"Figures translated: {self.figures_translated}",
            f"Figures passed QA: {self.figures_passed_qa}",
            "",
            "--- API Costs ---",
            f"OpenRouter balance before: ${self.openrouter_balance_before:.4f}",
            f"OpenRouter balance after:  ${self.openrouter_balance_after:.4f}",
            f"OpenRouter cost (Gemini):  ${self.openrouter_cost:.4f}",
            f"Translation API calls:     {self.translation_calls}",
            "",
            f"Moondream API calls:       {self.moondream_calls}",
            f"Moondream cost:            ${self.moondream_cost:.4f} (free tier: 5,000/day)",
            "",
            f"TOTAL ESTIMATED COST:      ${self.openrouter_cost + self.moondream_cost:.4f}",
            "",
            "--- Per-Figure Costs ---",
        ]

        if self.figures_translated > 0:
            cost_per_figure = self.openrouter_cost / self.figures_translated
            lines.append(f"OpenRouter cost/figure:    ${cost_per_figure:.4f}")

        if self.moondream_calls > 0 and self.figures_processed > 0:
            calls_per_figure = self.moondream_calls / self.figures_processed
            lines.append(f"Moondream calls/figure:    {calls_per_figure:.1f}")

        if self.errors:
            lines.extend([
                "",
                "--- Errors ---",
                *[f"  - {e}" for e in self.errors[:10]],
            ])
            if len(self.errors) > 10:
                lines.append(f"  ... and {len(self.errors) - 10} more")

        lines.append("=" * 70)
        return "\n".join(lines)


def get_openrouter_balance(api_key: str) -> float:
    """Fetch current OpenRouter balance."""
    response = requests.get(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=(5, 30),
    )
    if response.ok:
        data = response.json()
        # Response format: {"data": {"total_credits": X, "total_usage": Y}}
        # Balance = total_credits - total_usage
        credits = data.get("data", {})
        total = credits.get("total_credits", 0)
        used = credits.get("total_usage", 0)
        return total - used
    else:
        print(f"Warning: Could not fetch OpenRouter balance: {response.status_code}")
        return 0.0


class MoondreamTracker:
    """Wrapper to track Moondream API calls."""

    def __init__(self, model, tracker: CostTracker):
        self._model = model
        self._tracker = tracker

    def query(self, img, prompt: str) -> dict:
        """Query with tracking."""
        self._tracker.moondream_calls += 1
        return self._model.query(img, prompt)


def main():
    print("=" * 70)
    print("FIGURE TRANSLATION BATCH TEST WITH COST BENCHMARKING")
    print("=" * 70)

    # Initialize tracker
    tracker = CostTracker()

    # Get API keys
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    moondream_key = os.environ.get("MOONDREAM_API_KEY")

    if not openrouter_key:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)
    if not moondream_key:
        print("ERROR: MOONDREAM_API_KEY not set")
        sys.exit(1)

    # Get balance before
    print("\n--- Checking OpenRouter balance ---")
    tracker.openrouter_balance_before = get_openrouter_balance(openrouter_key)
    print(f"Balance before: ${tracker.openrouter_balance_before:.4f}")

    # Find 10 PDFs
    pdf_dir = Path(__file__).parent.parent / "data/pdfs"
    pdfs = sorted(pdf_dir.glob("*.pdf"))[:10]

    if len(pdfs) < 10:
        print(f"Warning: Only found {len(pdfs)} PDFs (wanted 10)")

    print(f"\n--- Processing {len(pdfs)} papers ---")

    # Import pipeline components
    from figure_pipeline.extractor import FigureExtractor
    from figure_pipeline.validator import FigureValidator
    from figure_pipeline.translator import FigureTranslator

    extractor = FigureExtractor()
    validator = FigureValidator()
    translator = FigureTranslator()

    # Wrap validator model for tracking
    original_model = validator.model
    tracked_model = MoondreamTracker(original_model, tracker)
    validator._model = tracked_model

    # Output directory
    output_base = Path("/tmp/figure_batch_test")
    output_base.mkdir(exist_ok=True)

    # Process each PDF
    for i, pdf_path in enumerate(pdfs, 1):
        paper_id = pdf_path.stem
        print(f"\n[{i}/{len(pdfs)}] Processing: {paper_id}")

        try:
            # Extract figures
            output_dir = output_base / paper_id
            figures = extractor.extract_all(str(pdf_path), str(output_dir / "extracted"))

            # Filter to substantial images (>5KB)
            substantial = [
                f for f in figures
                if os.path.exists(f.original_path) and os.path.getsize(f.original_path) > 5000
            ]
            print(f"  Extracted {len(figures)} figures, {len(substantial)} substantial (>5KB)")

            tracker.figures_processed += len(substantial)

            # Process each figure
            for fig in substantial:
                try:
                    # Check if needs translation
                    should_translate = validator.should_translate(fig.original_path)

                    if not should_translate:
                        print(f"    Fig {fig.figure_number}: No Chinese text, skipping")
                        continue

                    # Define QA check function
                    def check_chinese(image_path: str) -> bool:
                        """Returns True if Chinese text is detected."""
                        from PIL import Image
                        img = Image.open(image_path)
                        result = tracked_model.query(
                            img,
                            "Does this image contain any Chinese characters? Answer yes or no."
                        )
                        return "yes" in result.get("answer", "").lower()

                    # Translate
                    print(f"    Fig {fig.figure_number}: Translating...")
                    translated_path = translator.translate(
                        fig.original_path,
                        fig.figure_number,
                        paper_id,
                        str(output_dir / "translated"),
                        max_passes=3,
                        qa_check=check_chinese,
                    )

                    tracker.translation_calls += 1

                    if translated_path:
                        tracker.figures_translated += 1

                        # Run final QA
                        qa_result = validator.qa_translation(fig.original_path, translated_path)
                        if qa_result.get("passed"):
                            tracker.figures_passed_qa += 1
                            print(f"    Fig {fig.figure_number}: PASSED QA")
                        else:
                            print(f"    Fig {fig.figure_number}: QA issues - {qa_result.get('details', 'unknown')}")
                    else:
                        print(f"    Fig {fig.figure_number}: Translation failed")
                        tracker.errors.append(f"{paper_id}/fig_{fig.figure_number}: Translation returned None")

                    # Rate limiting between figures
                    time.sleep(1)

                except Exception as e:
                    print(f"    Fig {fig.figure_number}: ERROR - {e}")
                    tracker.errors.append(f"{paper_id}/fig_{fig.figure_number}: {e}")

            tracker.papers_processed += 1

        except Exception as e:
            print(f"  ERROR processing paper: {e}")
            tracker.errors.append(f"{paper_id}: {e}")

        # Rate limiting between papers
        time.sleep(2)

    # Get balance after
    print("\n--- Checking OpenRouter balance ---")
    tracker.openrouter_balance_after = get_openrouter_balance(openrouter_key)
    print(f"Balance after: ${tracker.openrouter_balance_after:.4f}")

    # Print summary
    print("\n" + tracker.summary())

    # Save results
    results_file = output_base / "cost_summary.txt"
    with open(results_file, "w") as f:
        f.write(tracker.summary())
    print(f"\nResults saved to: {results_file}")

    # Return success based on figures translated
    if tracker.figures_translated > 0:
        print(f"\nSUCCESS: Translated {tracker.figures_translated} figures from {tracker.papers_processed} papers")
        return 0
    else:
        print("\nWARNING: No figures were translated")
        return 1


if __name__ == "__main__":
    sys.exit(main())
