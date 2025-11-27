"""
Figure extraction using PyMuPDF.

Extracts images from PDF files with metadata (page number, bounding box).
"""
from __future__ import annotations

import os
from typing import List, Optional

from .models import (
    Figure,
    FigureLocation,
    FigureType,
    PipelineConfig,
    ProcessingStatus,
)


class FigureExtractor:
    """
    Extract figures from PDFs using PyMuPDF.

    PyMuPDF (fitz) is chosen for:
    - Fastest extraction (42ms/page)
    - Preserves native format (PNG, JPEG, TIFF)
    - xref-based extraction without quality loss
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        """Initialize extractor."""
        self.config = config or PipelineConfig()
        self._fitz = None

    @property
    def fitz(self):
        """Lazy-load fitz (PyMuPDF)."""
        if self._fitz is None:
            try:
                import fitz
                self._fitz = fitz
            except ImportError:
                raise ImportError(
                    "PyMuPDF not installed. Install with: pip install pymupdf"
                )
        return self._fitz

    def extract_all(self, pdf_path: str, output_dir: Optional[str] = None) -> List[Figure]:
        """
        Extract all images from PDF.

        Args:
            pdf_path: Path to PDF file
            output_dir: Directory to save extracted images (default: temp_dir)

        Returns:
            List of Figure objects with extraction metadata
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        output_dir = output_dir or self.config.temp_dir
        os.makedirs(output_dir, exist_ok=True)

        paper_id = os.path.basename(pdf_path).replace(".pdf", "")
        figures: List[Figure] = []

        doc = self.fitz.open(pdf_path)
        image_count = 0

        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images(full=True)

            for img_index, img_info in enumerate(image_list):
                xref = img_info[0]

                # Extract image
                try:
                    base_image = doc.extract_image(xref)
                    if not base_image:
                        continue

                    image_bytes = base_image["image"]
                    image_ext = base_image.get("ext", "png")

                    # Determine figure number
                    image_count += 1
                    figure_number = str(image_count)

                    # Save to disk
                    filename = f"fig_{figure_number}.{image_ext}"
                    output_path = os.path.join(output_dir, filename)

                    with open(output_path, "wb") as f:
                        f.write(image_bytes)

                    # Get bounding box if available
                    bbox = None
                    for item in page.get_image_info(xrefs=True):
                        if item.get("xref") == xref:
                            bbox = (
                                item.get("bbox", [0, 0, 0, 0])
                            )
                            break

                    # Create Figure object
                    figure = Figure(
                        paper_id=paper_id,
                        figure_number=figure_number,
                        figure_type=FigureType.FIGURE,  # Default, can be classified later
                        location=FigureLocation(
                            page_number=page_num + 1,  # 1-indexed
                            bounding_box=tuple(bbox) if bbox else None,
                            marker=f"[FIGURE:{figure_number}]",
                        ),
                        status=ProcessingStatus.EXTRACTED,
                        original_path=output_path,
                    )
                    figures.append(figure)

                except Exception as e:
                    # Log but continue extracting other images
                    from ..utils import log
                    log(f"Failed to extract image {img_index} from page {page_num}: {e}")

        doc.close()
        return figures

    def extract_page(self, pdf_path: str, page_num: int) -> List[Figure]:
        """
        Extract images from a specific page.

        Args:
            pdf_path: Path to PDF file
            page_num: Page number (0-indexed)

        Returns:
            List of Figure objects from that page
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        doc = self.fitz.open(pdf_path)
        if page_num >= len(doc):
            raise ValueError(f"Page {page_num} does not exist (PDF has {len(doc)} pages)")

        page = doc[page_num]
        figures = []

        # Similar extraction logic for single page
        # TODO: Implement single-page extraction

        doc.close()
        return figures

    def get_page_count(self, pdf_path: str) -> int:
        """Get number of pages in PDF."""
        doc = self.fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
