#!/usr/bin/env python3
"""
Scan all PDFs in data/pdfs/ and count figures per paper.
Uses PyMuPDF for fast extraction (no translation).
"""
import os
import sys
from pathlib import Path
from collections import defaultdict

import fitz  # PyMuPDF

def count_figures_in_pdf(pdf_path: str) -> int:
    """Count extractable images in a PDF."""
    try:
        doc = fitz.open(pdf_path)
        count = 0
        for page in doc:
            images = page.get_images(full=True)
            # Filter to substantial images (>5KB when extracted)
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
    except Exception as e:
        print(f"Error processing {pdf_path}: {e}")
        return 0


def main():
    pdf_dir = Path(__file__).parent.parent / "data/pdfs"

    if not pdf_dir.exists():
        print(f"PDF directory not found: {pdf_dir}")
        sys.exit(1)

    pdfs = list(pdf_dir.glob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs to scan")

    results = []

    for i, pdf_path in enumerate(sorted(pdfs), 1):
        if i % 20 == 0:
            print(f"Scanning... {i}/{len(pdfs)}")

        fig_count = count_figures_in_pdf(str(pdf_path))
        results.append((pdf_path.stem, fig_count))

    # Sort by figure count descending
    results.sort(key=lambda x: x[1], reverse=True)

    # Report top 30
    print("\n" + "=" * 60)
    print("TOP 30 PAPERS BY FIGURE COUNT")
    print("=" * 60)
    print(f"{'Paper ID':<35} {'Figures':>10}")
    print("-" * 60)

    for paper_id, count in results[:30]:
        print(f"{paper_id:<35} {count:>10}")

    # Stats
    total_figs = sum(c for _, c in results)
    papers_with_figs = sum(1 for _, c in results if c > 0)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total PDFs scanned: {len(results)}")
    print(f"Papers with figures: {papers_with_figs}")
    print(f"Total figures found: {total_figs}")
    print(f"Average figures/paper: {total_figs/len(results):.1f}")

    # Write top papers to file for batch processing
    top_20_file = Path(__file__).parent.parent / "data" / "top_20_by_figures.txt"
    top_20_file.parent.mkdir(exist_ok=True)

    with open(top_20_file, "w") as f:
        for paper_id, count in results[:20]:
            f.write(f"{paper_id}\n")

    print(f"\nTop 20 papers saved to: {top_20_file}")

    return results[:20]


if __name__ == "__main__":
    main()
