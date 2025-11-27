"""
Assembler for reinserting translated figures into documents.

Handles:
- Replacing [FIGURE:N] markers with actual images in body_md
- Generating HTML output with inline figures
- Generating PDF output with embedded figures
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from .models import Figure, PipelineConfig


class FigureAssembler:
    """
    Reassemble documents with translated figures.

    The assembler replaces [FIGURE:N] and [TABLE:N] markers
    in the translated body_md with actual figure images.
    """

    # Pattern to match figure markers
    MARKER_PATTERN = re.compile(r'\[(?:FIGURE|TABLE):([A-Za-z]?\d+[a-z]?)\]')

    def __init__(self, config: Optional[PipelineConfig] = None):
        """Initialize assembler."""
        self.config = config or PipelineConfig()

    def assemble_html(
        self,
        body_md: str,
        figures: List[Figure],
        paper_id: str,
        base_url: str = "",
    ) -> str:
        """
        Replace markers in body_md with HTML img tags.

        Args:
            body_md: Translated body markdown with [FIGURE:N] markers
            figures: List of Figure objects with URLs
            paper_id: Paper ID for paths
            base_url: Base URL for figure images (e.g., B2 CDN URL)

        Returns:
            body_md with markers replaced by <img> tags
        """
        # Build lookup from figure number to URL
        figure_urls: Dict[str, str] = {}
        for fig in figures:
            # Prefer translated URL, fall back to original
            url = fig.translated_url or fig.original_url
            if url:
                figure_urls[fig.figure_number] = url

        def replace_marker(match: re.Match) -> str:
            fig_num = match.group(1)
            url = figure_urls.get(fig_num)

            if not url:
                # Keep marker if no URL available
                return match.group(0)

            # Determine alt text
            fig_type = "Figure" if "FIGURE" in match.group(0) else "Table"
            alt = f"{fig_type} {fig_num}"

            # Return HTML img tag with styling
            return (
                f'\n\n<figure class="paper-figure">\n'
                f'  <img src="{url}" alt="{alt}" loading="lazy" />\n'
                f'  <figcaption>{alt}</figcaption>\n'
                f'</figure>\n\n'
            )

        return self.MARKER_PATTERN.sub(replace_marker, body_md)

    def assemble_markdown(
        self,
        body_md: str,
        figures: List[Figure],
        base_url: str = "",
    ) -> str:
        """
        Replace markers in body_md with Markdown image syntax.

        Args:
            body_md: Translated body markdown with [FIGURE:N] markers
            figures: List of Figure objects with URLs
            base_url: Base URL for figure images

        Returns:
            body_md with markers replaced by ![](url) syntax
        """
        figure_urls: Dict[str, str] = {}
        for fig in figures:
            url = fig.translated_url or fig.original_url
            if url:
                figure_urls[fig.figure_number] = url

        def replace_marker(match: re.Match) -> str:
            fig_num = match.group(1)
            url = figure_urls.get(fig_num)

            if not url:
                return match.group(0)

            fig_type = "Figure" if "FIGURE" in match.group(0) else "Table"
            alt = f"{fig_type} {fig_num}"

            return f'\n\n![{alt}]({url})\n\n'

        return self.MARKER_PATTERN.sub(replace_marker, body_md)

    def get_markers(self, body_md: str) -> List[str]:
        """
        Extract all figure markers from body_md.

        Args:
            body_md: Markdown text with [FIGURE:N] markers

        Returns:
            List of marker strings (e.g., ["1", "2", "S1"])
        """
        matches = self.MARKER_PATTERN.findall(body_md)
        return list(set(matches))  # Deduplicate

    def validate_assembly(
        self,
        body_md: str,
        figures: List[Figure],
    ) -> dict:
        """
        Validate that all markers have corresponding figures.

        Args:
            body_md: Markdown with markers
            figures: List of Figure objects

        Returns:
            Dict with validation results
        """
        markers = self.get_markers(body_md)
        figure_numbers = {fig.figure_number for fig in figures}

        missing = [m for m in markers if m not in figure_numbers]
        extra = [f for f in figure_numbers if f not in markers]

        return {
            "valid": len(missing) == 0,
            "markers_found": len(markers),
            "figures_available": len(figures),
            "missing_figures": missing,
            "extra_figures": list(extra),
        }


def generate_pdf_with_figures(
    paper_data: dict,
    figures: List[Figure],
    output_path: str,
) -> bool:
    """
    Generate a PDF with embedded translated figures.

    This is a placeholder for PDF generation.
    In production, this would use reportlab, weasyprint, or similar.

    Args:
        paper_data: Translation data dict with body_md, title_en, etc.
        figures: List of Figure objects
        output_path: Path to save the PDF

    Returns:
        True if successful
    """
    # TODO: Implement PDF generation
    # Options:
    # 1. weasyprint - HTML to PDF (requires system deps)
    # 2. reportlab - Native Python PDF generation
    # 3. fpdf2 - Lightweight Python PDF
    # 4. pypandoc - Markdown to PDF via pandoc
    raise NotImplementedError(
        "PDF generation not yet implemented. "
        "Consider using weasyprint or pypandoc."
    )
