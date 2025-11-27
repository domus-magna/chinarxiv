"""
Pydantic data models for figure pipeline.

These models define the data structures used throughout the figure
extraction, translation, and assembly pipeline.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field


class FigureType(str, Enum):
    """Type of figure detected in a paper."""
    FIGURE = "figure"
    TABLE = "table"


class ProcessingStatus(str, Enum):
    """Processing status for a figure."""
    PENDING = "pending"
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    TRANSLATED = "translated"
    UPLOADED = "uploaded"
    FAILED = "failed"


class FigureLocation(BaseModel):
    """Location of a figure within a document."""
    page_number: int = Field(..., description="PDF page number (1-indexed)")
    bounding_box: Optional[Tuple[float, float, float, float]] = Field(
        None, description="(x1, y1, x2, y2) coordinates in PDF units"
    )
    marker: str = Field(..., description="Marker injected into text, e.g., '[FIGURE:1]'")
    section_title: Optional[str] = Field(None, description="Section where figure appears")


class Figure(BaseModel):
    """A figure extracted from a paper."""
    paper_id: str = Field(..., description="ID of the parent paper")
    figure_number: str = Field(..., description="Figure number as string, e.g., '1', '2a', 'S1'")
    figure_type: FigureType = Field(..., description="Type: figure or table")
    location: Optional[FigureLocation] = Field(None, description="Location in document")

    # Caption information
    caption_zh: Optional[str] = Field(None, description="Original Chinese caption")
    caption_en: Optional[str] = Field(None, description="Translated English caption")

    # Detection metadata
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Detection confidence score")

    # Processing state
    status: ProcessingStatus = Field(
        ProcessingStatus.PENDING, description="Current processing status"
    )
    error_message: Optional[str] = Field(None, description="Error message if status is FAILED")

    # File paths (local, during processing)
    original_path: Optional[str] = Field(None, description="Local path to original image")
    translated_path: Optional[str] = Field(None, description="Local path to translated image")

    # B2 URLs (after upload)
    original_url: Optional[str] = Field(None, description="B2 URL for original image")
    translated_url: Optional[str] = Field(None, description="B2 URL for translated image")

    # QA results
    qa_readable: Optional[bool] = Field(None, description="Is figure readable?")
    qa_has_chinese: Optional[bool] = Field(None, description="Does figure contain Chinese text?")
    qa_figure_type: Optional[str] = Field(None, description="Detected figure type from QA")
    qa_translation_passed: Optional[bool] = Field(
        None, description="Did translation QA pass (figures identical except text)?"
    )
    qa_translation_notes: Optional[str] = Field(
        None, description="Notes from translation QA comparison"
    )


class FigureProcessingResult(BaseModel):
    """Result of processing all figures for a paper."""
    paper_id: str = Field(..., description="ID of the processed paper")
    total_figures: int = Field(0, description="Total figures detected")
    extracted: int = Field(0, description="Successfully extracted count")
    validated: int = Field(0, description="Passed validation count")
    translated: int = Field(0, description="Successfully translated count")
    uploaded: int = Field(0, description="Successfully uploaded to B2 count")
    failed: int = Field(0, description="Failed count")
    figures: List[Figure] = Field(default_factory=list, description="List of Figure objects")

    @property
    def success_rate(self) -> float:
        """Calculate success rate as translated/total."""
        if self.total_figures == 0:
            return 0.0
        return self.translated / self.total_figures


class PipelineConfig(BaseModel):
    """Configuration for the figure pipeline."""
    # API keys
    gemini_api_key: Optional[str] = Field(None, description="Google Gemini API key")
    moondream_api_key: Optional[str] = Field(None, description="Moondream Cloud API key")

    # Storage
    b2_bucket: str = Field("chinaxiv", description="Backblaze B2 bucket name")
    b2_key_id: Optional[str] = Field(None, description="B2 application key ID")
    b2_app_key: Optional[str] = Field(None, description="B2 application key")

    # Processing options
    max_figures_per_paper: int = Field(50, description="Maximum figures to process per paper")
    skip_translation_if_no_chinese: bool = Field(
        True, description="Skip translation if figure has no Chinese text"
    )
    dry_run: bool = Field(False, description="If True, don't upload to B2 or modify files")

    # Paths
    temp_dir: str = Field("/tmp/figure_pipeline", description="Temporary directory for processing")
    pdf_dir: str = Field("data/pdfs", description="Directory containing PDF files")
    output_dir: str = Field("data/figures", description="Output directory for extracted figures")
