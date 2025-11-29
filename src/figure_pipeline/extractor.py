"""
Figure extraction using PyMuPDF.

Extracts images from PDF files with metadata (page number, bounding box).
Includes size-based filtering to remove icons/decorations and overly large images.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

from .models import (
    Figure,
    FigureLocation,
    FigureType,
    PipelineConfig,
    ProcessingStatus,
)

# Size thresholds for filtering
MIN_IMAGE_SIZE_BYTES = 20 * 1024      # 20KB - filter out tiny icons/decorations
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB - filter out huge background images
MIN_IMAGE_DIMENSION = 100             # Minimum width/height in pixels

# Visual content thresholds
MIN_VARIANCE = 100.0     # Minimum pixel variance (filters blank/solid images)
MIN_EDGE_DENSITY = 0.01  # Minimum edge pixels as fraction of total (filters simple shapes)

# Deduplication thresholds
HASH_SIZE = 8            # Size of perceptual hash (8x8 = 64 bits)
HASH_DIFF_THRESHOLD = 5  # Maximum hamming distance for images to be considered duplicates


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

    def _passes_size_filter(
        self,
        image_bytes: bytes,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Check if image passes size-based filtering criteria.

        Args:
            image_bytes: Raw image data
            width: Image width in pixels (if known)
            height: Image height in pixels (if known)

        Returns:
            Tuple of (passes, reason) - reason explains why it failed if False
        """
        size_bytes = len(image_bytes)

        # Check file size bounds
        if size_bytes < MIN_IMAGE_SIZE_BYTES:
            return False, f"too small ({size_bytes / 1024:.1f}KB < {MIN_IMAGE_SIZE_BYTES / 1024}KB)"
        if size_bytes > MAX_IMAGE_SIZE_BYTES:
            return False, f"too large ({size_bytes / 1024 / 1024:.1f}MB > {MAX_IMAGE_SIZE_BYTES / 1024 / 1024}MB)"

        # Check dimensions if provided
        if width is not None and width < MIN_IMAGE_DIMENSION:
            return False, f"width too small ({width}px < {MIN_IMAGE_DIMENSION}px)"
        if height is not None and height < MIN_IMAGE_DIMENSION:
            return False, f"height too small ({height}px < {MIN_IMAGE_DIMENSION}px)"

        return True, "passed"

    def _has_visual_content(self, image_bytes: bytes) -> Tuple[bool, str]:
        """
        Check if image has meaningful visual content using local analysis.

        Filters out:
        - Blank or nearly blank images (low variance)
        - Simple decorative elements (low edge density)
        - Solid color fills

        Uses PIL for fast local analysis (no API calls).

        Args:
            image_bytes: Raw image data

        Returns:
            Tuple of (has_content, reason) - reason explains why if False
        """
        try:
            from PIL import Image
            import io

            # Load image
            img = Image.open(io.BytesIO(image_bytes))

            # Convert to grayscale for analysis
            gray = img.convert('L')
            pixels = list(gray.getdata())

            # Calculate variance (low variance = blank/solid)
            mean = sum(pixels) / len(pixels)
            variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)

            if variance < MIN_VARIANCE:
                return False, f"low variance ({variance:.1f} < {MIN_VARIANCE})"

            # Calculate edge density using simple Sobel-like detection
            width, height = gray.size
            if width < 3 or height < 3:
                return True, "passed"  # Too small to analyze

            edge_count = 0

            # Sample-based edge detection (faster than full scan)
            sample_step = max(1, min(width, height) // 50)
            sampled_pixels = 0

            for y in range(1, height - 1, sample_step):
                for x in range(1, width - 1, sample_step):
                    # Get pixel values around current position
                    pl = gray.getpixel((x - 1, y))
                    pr = gray.getpixel((x + 1, y))
                    pt = gray.getpixel((x, y - 1))
                    pb = gray.getpixel((x, y + 1))

                    # Simple gradient magnitude
                    gx = abs(pr - pl)
                    gy = abs(pb - pt)
                    gradient = gx + gy

                    sampled_pixels += 1
                    if gradient > 30:  # Edge threshold
                        edge_count += 1

            if sampled_pixels > 0:
                edge_density = edge_count / sampled_pixels
                if edge_density < MIN_EDGE_DENSITY:
                    return False, f"low edge density ({edge_density:.3f} < {MIN_EDGE_DENSITY})"

            return True, "passed"

        except Exception as e:
            # If analysis fails, assume it has content
            return True, f"analysis failed: {e}"

    def _compute_perceptual_hash(self, image_bytes: bytes) -> Optional[str]:
        """
        Compute perceptual hash (average hash) for deduplication.

        Uses a simple average hash algorithm:
        1. Resize image to HASH_SIZE x HASH_SIZE
        2. Convert to grayscale
        3. Compute average pixel value
        4. Generate hash: 1 if pixel > average, else 0

        Args:
            image_bytes: Raw image data

        Returns:
            Hex string of hash, or None if computation fails
        """
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(image_bytes))

            # Resize and convert to grayscale
            img = img.resize((HASH_SIZE, HASH_SIZE), Image.Resampling.LANCZOS)
            gray = img.convert('L')

            # Get pixels and compute average
            pixels = list(gray.getdata())
            avg = sum(pixels) / len(pixels)

            # Generate hash bits
            bits = ''.join('1' if p > avg else '0' for p in pixels)

            # Convert to hex
            hash_int = int(bits, 2)
            return format(hash_int, f'0{HASH_SIZE * HASH_SIZE // 4}x')

        except Exception:
            return None

    def _hamming_distance(self, hash1: str, hash2: str) -> int:
        """
        Compute hamming distance between two hex hash strings.

        Args:
            hash1: First hex hash
            hash2: Second hex hash

        Returns:
            Number of differing bits
        """
        if len(hash1) != len(hash2):
            return 999  # Different lengths = very different

        # Convert hex to int and XOR
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
        xor = int1 ^ int2

        # Count set bits (hamming distance)
        return bin(xor).count('1')

    def _is_duplicate(self, new_hash: str, existing_hashes: list) -> bool:
        """
        Check if a hash is a duplicate of any existing hash.

        Args:
            new_hash: Hash to check
            existing_hashes: List of existing hashes

        Returns:
            True if duplicate found
        """
        for existing in existing_hashes:
            if self._hamming_distance(new_hash, existing) <= HASH_DIFF_THRESHOLD:
                return True
        return False

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
        try:
            image_count = 0
            filtered_count = 0
            duplicate_count = 0
            total_extracted = 0
            seen_hashes = []  # For deduplication

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
                        width = base_image.get("width")
                        height = base_image.get("height")

                        total_extracted += 1

                        # Apply size-based filtering
                        passes, reason = self._passes_size_filter(image_bytes, width, height)
                        if not passes:
                            filtered_count += 1
                            continue

                        # Apply visual content detection
                        has_content, content_reason = self._has_visual_content(image_bytes)
                        if not has_content:
                            filtered_count += 1
                            continue

                        # Deduplication check
                        img_hash = self._compute_perceptual_hash(image_bytes)
                        if img_hash and self._is_duplicate(img_hash, seen_hashes):
                            duplicate_count += 1
                            continue
                        if img_hash:
                            seen_hashes.append(img_hash)

                        # Determine figure number (only count images that pass filtering)
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
                                bbox = item.get("bbox", [0, 0, 0, 0])
                                break

                        # Create Figure object
                        figure = Figure(
                            paper_id=paper_id,
                            figure_number=figure_number,
                            figure_type=FigureType.FIGURE,
                            location=FigureLocation(
                                page_number=page_num + 1,
                                bounding_box=tuple(bbox) if bbox else None,
                                marker=f"[FIGURE:{figure_number}]",
                            ),
                            status=ProcessingStatus.EXTRACTED,
                            original_path=output_path,
                        )
                        figures.append(figure)

                    except Exception as e:
                        print(f"[extractor] Failed to extract image {img_index} from page {page_num}: {e}")

            # Log filtering summary
            if filtered_count > 0 or duplicate_count > 0:
                print(f"[extractor] Filtered {filtered_count} (size/content), {duplicate_count} duplicates from {total_extracted} images")

        finally:
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

        _page = doc[page_num]  # Reserved for future single-page implementation
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
