"""
Figure Pipeline - End-to-end figure extraction, translation, and assembly.

This module handles:
- Step 2: Figure detection (Docling + regex)
- Step 3: Figure extraction (PyMuPDF)
- Step 3.5: Figure marker injection (for position tracking)
- Step 4: Figure translation (Gemini 3 Pro Image)
- Step 4.5 + 7: QA validation (Moondream API)
- Step 6: Reassembly (marker replacement with images)

All processing runs in GitHub Actions - nothing runs locally.
"""
from __future__ import annotations

import os
from typing import List, Optional

from .circuit_breaker import get_circuit_breaker


def log(message: str) -> None:
    """Simple logging function."""
    print(f"[figure_pipeline] {message}")


from .models import (
    Figure,
    FigureProcessingResult,
    PipelineConfig,
    ProcessingStatus,
)


class FigurePipeline:
    """
    End-to-end figure processing pipeline.

    Usage:
        config = PipelineConfig(
            gemini_api_key=os.environ["GEMINI_API_KEY"],
            moondream_api_key=os.environ["MOONDREAM_API_KEY"],
        )
        pipeline = FigurePipeline(config)
        result = pipeline.process_paper("chinaxiv-202510.00001")
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        """Initialize pipeline with configuration."""
        self.config = config or PipelineConfig()

        # Lazy-load components to avoid import errors when deps not installed
        self._extractor = None
        self._translator = None
        self._validator = None
        self._storage = None

    @property
    def extractor(self):
        """Lazy-load figure extractor."""
        if self._extractor is None:
            from .extractor import FigureExtractor
            self._extractor = FigureExtractor(self.config)
        return self._extractor

    @property
    def translator(self):
        """Lazy-load figure translator."""
        if self._translator is None:
            from .translator import FigureTranslator
            self._translator = FigureTranslator(self.config)
        return self._translator

    @property
    def validator(self):
        """Lazy-load figure validator."""
        if self._validator is None:
            from .validator import FigureValidator
            self._validator = FigureValidator(self.config)
        return self._validator

    @property
    def storage(self):
        """Lazy-load B2 storage."""
        if self._storage is None:
            from .storage import FigureStorage
            self._storage = FigureStorage(self.config)
        return self._storage

    def process_paper(self, paper_id: str) -> FigureProcessingResult:
        """
        Process all figures for a single paper.

        Pipeline steps:
        1. Extract images from PDF
        2. Validate with Moondream (check if needs translation)
        3. Translate with Gemini 3 Pro Image
        4. QA the translation (compare before/after)
        5. Upload to B2
        6. Update manifest

        Args:
            paper_id: Paper ID (e.g., "chinaxiv-202510.00001")

        Returns:
            FigureProcessingResult with all figures and stats

        Raises:
            RuntimeError: If circuit breaker is open (billing/quota error)
        """
        # Check circuit breaker before starting
        circuit_breaker = get_circuit_breaker()
        circuit_breaker.check()  # Raises if open

        result = FigureProcessingResult(paper_id=paper_id)

        # Find PDF path
        pdf_path = self._find_pdf(paper_id)
        if not pdf_path:
            log(f"PDF not found for {paper_id}")
            return result

        # Step 1: Extract figures
        log(f"Extracting figures from {paper_id}...")
        figures = self.extractor.extract_all(pdf_path)
        result.total_figures = len(figures)
        result.extracted = len([f for f in figures if f.status == ProcessingStatus.EXTRACTED])

        if not figures:
            log(f"No figures found in {paper_id}")
            return result

        # Step 2: Validate and check for Chinese text
        log(f"Validating {len(figures)} figures...")
        for fig in figures:
            if fig.original_path and os.path.exists(fig.original_path):
                validation = self.validator.validate(fig.original_path)
                fig.qa_readable = validation.get("readable", True)
                fig.qa_has_chinese = validation.get("has_chinese", False)
                fig.qa_figure_type = validation.get("figure_type", "unknown")

                if fig.qa_readable:
                    fig.status = ProcessingStatus.VALIDATED
                    result.validated += 1

        # Step 3: Translate figures with Chinese text
        figures_to_translate = [
            f for f in figures
            if f.status == ProcessingStatus.VALIDATED
            and (f.qa_has_chinese or not self.config.skip_translation_if_no_chinese)
        ]
        log(f"Translating {len(figures_to_translate)} figures...")

        # Define QA check function for multi-pass iteration
        def check_has_chinese(image_path: str) -> bool:
            """Return True if Chinese text detected (translation needs retry)."""
            validation = self.validator.validate(image_path)
            return validation.get("has_chinese", False)

        for fig in figures_to_translate:
            try:
                translated_path = self.translator.translate(
                    fig.original_path,
                    fig.figure_number,
                    paper_id,
                    qa_check=check_has_chinese,  # Enable multi-pass iteration
                )
                if translated_path:
                    fig.translated_path = translated_path
                    fig.status = ProcessingStatus.TRANSLATED
                    result.translated += 1
            except Exception as e:
                fig.status = ProcessingStatus.FAILED
                fig.error_message = str(e)
                result.failed += 1

        # Step 4: QA the translations
        translated_figures = [f for f in figures if f.status == ProcessingStatus.TRANSLATED]
        for fig in translated_figures:
            if fig.original_path and fig.translated_path:
                qa_result = self.validator.qa_translation(
                    fig.original_path,
                    fig.translated_path,
                )
                fig.qa_translation_passed = qa_result.get("passed", False)
                fig.qa_translation_notes = qa_result.get("differences", "")

        # Step 5: Upload to B2
        if not self.config.dry_run:
            log("Uploading figures to B2...")
            for fig in figures:
                if fig.original_path:
                    fig.original_url = self.storage.upload(
                        fig.original_path,
                        f"figures/{paper_id}/original/{os.path.basename(fig.original_path)}",
                    )
                if fig.translated_path:
                    fig.translated_url = self.storage.upload(
                        fig.translated_path,
                        f"figures/{paper_id}/translated/{os.path.basename(fig.translated_path)}",
                    )
                if fig.original_url or fig.translated_url:
                    fig.status = ProcessingStatus.UPLOADED
                    result.uploaded += 1

        result.figures = figures
        log(f"Processed {paper_id}: {result.translated}/{result.total_figures} figures translated")

        return result

    def process_batch(
        self,
        paper_ids: List[str],
        workers: int = 4,
    ) -> List[FigureProcessingResult]:
        """
        Process multiple papers in parallel.

        Args:
            paper_ids: List of paper IDs
            workers: Number of parallel workers

        Returns:
            List of FigureProcessingResult objects
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Create per-task pipeline instances to avoid shared state issues
        # (translator mutates _current_model, clients may not be thread-safe)
        def process_single(paper_id: str) -> FigureProcessingResult:
            pipeline = FigurePipeline(self.config)
            return pipeline.process_paper(paper_id)

        results = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(process_single, pid): pid
                for pid in paper_ids
            }
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    log(f"Error processing {pid}: {e}")
                    results.append(FigureProcessingResult(paper_id=pid, failed=1))

        return results

    def get_status(self, paper_id: str) -> Optional[FigureProcessingResult]:
        """
        Get processing status for a paper.

        Args:
            paper_id: Paper ID

        Returns:
            FigureProcessingResult if found, None otherwise
        """
        # TODO: Load from manifest or B2
        raise NotImplementedError("Status retrieval not yet implemented")

    def _find_pdf(self, paper_id: str) -> Optional[str]:
        """Find PDF path for a paper ID."""
        import re
        # Validate paper_id to prevent path traversal attacks
        # Only allow alphanumeric, dots, hyphens, and underscores
        if not re.match(r'^[\w\-\.]+$', paper_id):
            log(f"Invalid paper_id format: {paper_id}")
            return None

        # Double-check no path traversal sequences
        if '..' in paper_id or '/' in paper_id or '\\' in paper_id:
            log(f"Suspicious paper_id rejected: {paper_id}")
            return None

        candidates = [
            os.path.join(self.config.pdf_dir, f"{paper_id}.pdf"),
            os.path.join("data", "pdfs", f"{paper_id}.pdf"),
            os.path.join("/tmp", "pdfs", f"{paper_id}.pdf"),
        ]
        for path in candidates:
            # Resolve to absolute path and verify it's within expected directory
            abs_path = os.path.realpath(path)
            if os.path.exists(abs_path):
                return abs_path
        return None


__all__ = [
    "FigurePipeline",
    "Figure",
    "FigureProcessingResult",
    "PipelineConfig",
    "ProcessingStatus",
]
