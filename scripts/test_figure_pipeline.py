#!/usr/bin/env python3
"""
Test the figure translation pipeline end-to-end.

Tests:
1. Figure extraction from PDF
2. Pre-translation validation with Moondream
3. Figure translation with Gemini via OpenRouter
4. Post-translation QA with Moondream
"""
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


def main():
    print("=" * 60)
    print("Figure Translation Pipeline Test")
    print("=" * 60)

    # Find a test PDF
    pdf_dir = Path(__file__).parent.parent / "data/pdfs"
    test_pdf = None

    for pdf in sorted(pdf_dir.glob("*.pdf")):
        # Pick one that's likely to have figures
        test_pdf = pdf
        break

    if not test_pdf:
        print("No PDFs found in data/pdfs/")
        sys.exit(1)

    print(f"\nTest PDF: {test_pdf.name}")

    # Create temp output directory
    output_dir = Path("/tmp/figure_pipeline_test")
    output_dir.mkdir(exist_ok=True)

    # =========================================================================
    # Step 1: Extract figures
    # =========================================================================
    print("\n" + "-" * 60)
    print("STEP 1: Figure Extraction (PyMuPDF)")
    print("-" * 60)

    from figure_pipeline.extractor import FigureExtractor

    extractor = FigureExtractor()
    figures = extractor.extract_all(str(test_pdf), str(output_dir / "extracted"))

    print(f"Extracted {len(figures)} figures")

    if not figures:
        print("No figures found in PDF. Try another PDF.")
        sys.exit(1)

    # Filter to substantial images (>5KB)
    substantial = [f for f in figures if os.path.getsize(f.original_path) > 5000]
    print(f"Substantial figures (>5KB): {len(substantial)}")

    if not substantial:
        print("No substantial figures found. Try another PDF.")
        sys.exit(1)

    # Use first substantial figure for testing
    test_figure = substantial[0]
    print(f"\nUsing figure {test_figure.figure_number}:")
    print(f"  Path: {test_figure.original_path}")
    print(f"  Page: {test_figure.location.page_number}")
    print(f"  Size: {os.path.getsize(test_figure.original_path) / 1024:.1f} KB")

    # =========================================================================
    # Step 2: Pre-translation validation with Moondream
    # =========================================================================
    print("\n" + "-" * 60)
    print("STEP 2: Pre-Translation Validation (Moondream)")
    print("-" * 60)

    from figure_pipeline.validator import FigureValidator

    validator = FigureValidator()

    validation = validator.validate(test_figure.original_path)
    print(f"Readable: {validation['readable']}")
    print(f"Has Chinese text: {validation['has_chinese']}")
    print(f"Figure type: {validation['figure_type']}")

    should_translate = validator.should_translate(test_figure.original_path)
    print(f"\nShould translate: {should_translate}")

    if not should_translate:
        print("\nFigure does not need translation (no Chinese text or unreadable)")
        print("Test PASSED (validation working correctly)")
        return

    # =========================================================================
    # Step 3: Translate figure with Gemini via OpenRouter (with QA iteration)
    # =========================================================================
    print("\n" + "-" * 60)
    print("STEP 3: Figure Translation (Gemini via OpenRouter + QA iteration)")
    print("-" * 60)

    from figure_pipeline.translator import FigureTranslator

    translator = FigureTranslator()

    print(f"Model: {translator.MODEL}")
    print("Max passes: 3 (will iterate until no Chinese detected)")
    print("Sending to OpenRouter API...")

    # Create QA check function using validator
    def check_chinese(image_path: str) -> bool:
        """Returns True if Chinese text is detected."""
        img = Image.open(image_path)
        result = validator.model.query(
            img,
            "Does this image contain any Chinese characters? Answer yes or no."
        )
        has_chinese = "yes" in result.get("answer", "").lower()
        print(f"  QA check: Chinese detected = {has_chinese}")
        return has_chinese

    try:
        translated_path = translator.translate(
            test_figure.original_path,
            test_figure.figure_number,
            test_pdf.stem,
            str(output_dir / "translated"),
            max_passes=3,
            qa_check=check_chinese,
        )

        if translated_path:
            print(f"Translated figure saved: {translated_path}")
            print(f"Size: {os.path.getsize(translated_path) / 1024:.1f} KB")
        else:
            print("Translation returned None (model may not have generated image)")
            print("\nThis could mean:")
            print("- Model doesn't support image generation yet")
            print("- Rate limit or quota issue")
            print("- Input image format issue")
            sys.exit(1)
    except Exception as e:
        print(f"Translation failed: {e}")
        sys.exit(1)

    # =========================================================================
    # Step 4: Post-translation QA with Moondream
    # =========================================================================
    print("\n" + "-" * 60)
    print("STEP 4: Post-Translation QA (Moondream)")
    print("-" * 60)

    qa_result = validator.qa_translation(test_figure.original_path, translated_path)

    print(f"QA Passed: {qa_result['passed']}")
    print(f"Has English: {qa_result.get('has_english', 'N/A')}")
    print(f"Chinese Remaining: {qa_result.get('has_chinese_remaining', 'N/A')}")
    print(f"Figure Type Match: {qa_result.get('figure_type_match', 'N/A')}")
    print(f"Details: {qa_result.get('details', qa_result.get('differences', 'N/A'))}")

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 60)
    print("PIPELINE TEST COMPLETE")
    print("=" * 60)
    print(f"Original: {test_figure.original_path}")
    print(f"Translated: {translated_path}")
    print(f"QA Status: {'PASSED' if qa_result['passed'] else 'NEEDS REVIEW'}")
    print("\nOpen both images to visually compare:")
    print(f"  open '{test_figure.original_path}' '{translated_path}'")


if __name__ == "__main__":
    main()
