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
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from .circuit_breaker import get_circuit_breaker
from .models import (
    Figure,
    FigureProcessingResult,
    PipelineConfig,
    ProcessingStatus,
)


def log(message: str) -> None:
    """Simple logging function."""
    print(f"[figure_pipeline] {message}")


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

    def process_paper(
        self,
        paper_id: str,
        max_concurrent_figures: int = 8,
    ) -> FigureProcessingResult:
        """
        Process all figures for a single paper.

        Pipeline steps:
        1. Extract images from PDF
        2. Validate with Moondream (check if needs translation)
        3. Translate with Gemini 3 Pro Image (parallel)
        4. QA the translation (compare before/after)
        5. Upload to B2
        6. Update manifest

        Args:
            paper_id: Paper ID (e.g., "chinaxiv-202510.00001")
            max_concurrent_figures: Max figures to translate concurrently (default: 8)

        Returns:
            FigureProcessingResult with all figures and stats

        Raises:
            RuntimeError: If circuit breaker is open (billing/quota error)
        """
        # Check circuit breaker before starting
        circuit_breaker = get_circuit_breaker()
        circuit_breaker.check()  # Raises if open

        # Get concurrency from env var (for GitHub Actions) or use default
        env_concurrent = os.environ.get("FIGURE_CONCURRENT")
        if env_concurrent:
            try:
                # Clamp to at least 1 to prevent ValueError in ThreadPoolExecutor or deadlock in acquire()
                max_concurrent = max(1, int(env_concurrent))
            except ValueError:
                log(f"Warning: Invalid FIGURE_CONCURRENT='{env_concurrent}', using default {max_concurrent_figures}")
                max_concurrent = max_concurrent_figures
        else:
            max_concurrent = max_concurrent_figures

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

        # Step 3: Translate figures with Chinese text (PARALLEL)
        figures_to_translate = [
            f for f in figures
            if f.status == ProcessingStatus.VALIDATED
            and (f.qa_has_chinese or not self.config.skip_translation_if_no_chinese)
        ]
        log(f"Translating {len(figures_to_translate)} figures (max {max_concurrent} concurrent)...")

        # Define QA check function for multi-pass iteration
        def check_has_chinese(image_path: str) -> bool:
            """Return True if Chinese text detected (translation needs retry)."""
            validation = self.validator.validate(image_path)
            return validation.get("has_chinese", False)

        # Translate figures in parallel
        translation_results = self._translate_figures_parallel(
            figures_to_translate,
            paper_id,
            check_has_chinese,
            max_concurrent,
        )

        # Apply results to figures
        for fig, translated_path, error in translation_results:
            if translated_path:
                fig.translated_path = translated_path
                fig.status = ProcessingStatus.TRANSLATED
                result.translated += 1
            elif error:
                fig.status = ProcessingStatus.FAILED
                fig.error_message = error
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

            # Step 6: Update manifest with translated figure URLs
            translated_with_urls = [
                {"number": fig.figure_number, "url": fig.translated_url}
                for fig in figures
                if fig.translated_url
            ]
            if translated_with_urls:
                log(f"Updating manifest with {len(translated_with_urls)} translated figures...")
                self.storage.update_manifest(paper_id, translated_with_urls)

                # Step 7: Update PostgreSQL has_figures column
                self._update_db_has_figures(paper_id)

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
        from concurrent.futures import as_completed
        from .rate_limiter import get_rate_limiter

        # FIX M: Reset rate limiter at start of each batch to clear stale state
        rate_limiter = get_rate_limiter()
        rate_limiter.reset()

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

    def _translate_figures_parallel(
        self,
        figures: List[Figure],
        paper_id: str,
        check_has_chinese,
        max_concurrent: int,
    ) -> List[tuple]:
        """
        Translate multiple figures in parallel using ThreadPoolExecutor.

        Uses AdaptiveRateLimiter to handle 429 errors gracefully.

        Args:
            figures: List of Figure objects to translate
            paper_id: Paper ID for output directory
            check_has_chinese: Callback to check if Chinese remains (for multi-pass)
            max_concurrent: Maximum concurrent translation workers

        Returns:
            List of (figure, translated_path, error) tuples
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from .rate_limiter import get_rate_limiter, is_rate_limit_error

        rate_limiter = get_rate_limiter()

        def translate_one(fig: Figure) -> tuple:
            """Translate a single figure, returning (figure, path, error).

            Uses global semaphore for concurrency control and retries on 429s.
            """
            max_retries = 3

            if not fig.original_path:
                return (fig, None, "No original_path set")

            for attempt in range(max_retries):
                try:
                    # Acquire global semaphore to enforce cross-paper concurrency cap
                    with rate_limiter.acquire():
                        translated_path = self.translator.translate(
                            image_path=fig.original_path,
                            figure_number=fig.figure_number,
                            paper_id=paper_id,
                            qa_check=check_has_chinese,
                        )

                    # FIX P1-1: Only call on_success if translation actually succeeded
                    if translated_path:
                        rate_limiter.on_success()
                        return (fig, translated_path, None)
                    else:
                        # Translator returned None = translation failed (exhausted internal retries)
                        return (fig, None, "Translation returned None after retries")

                except Exception as e:
                    error_str = str(e)
                    # FIX M1: Extract status_code from typed exceptions (e.g., HTTPError)
                    status_code = getattr(e, "status_code", None)

                    # FIX P1-3: Check if this is a rate limit error (429/503) and retry
                    if is_rate_limit_error(status_code, error_str) and attempt < max_retries - 1:
                        delay = rate_limiter.on_rate_limit(error_str)
                        log(f"Rate limit/overload ({status_code}) for figure {fig.figure_number}, "
                            f"retry {attempt + 1}/{max_retries} after {delay}s...")
                        time.sleep(delay)
                        continue  # Retry

                    # Non-retryable error or exhausted retries
                    log(f"Error translating figure {fig.figure_number}: {e}")
                    return (fig, None, error_str)

            # Should not reach here, but just in case
            return (fig, None, "Max retries exceeded")

        results = []

        # Use rate limiter's current concurrency (may be lower after 429s)
        effective_concurrent = min(max_concurrent, rate_limiter.get_concurrent())
        log(f"Using {effective_concurrent} concurrent workers (rate limiter: {rate_limiter.get_concurrent()})")

        # Use ThreadPoolExecutor for parallel translation
        # (translator.translate is synchronous, so we parallelize with threads)
        with ThreadPoolExecutor(max_workers=effective_concurrent) as executor:
            # Submit all translation jobs
            future_to_fig = {
                executor.submit(translate_one, fig): fig
                for fig in figures
            }

            # Collect results as they complete
            for future in as_completed(future_to_fig):
                fig = future_to_fig[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    log(f"Unexpected error for figure {fig.figure_number}: {e}")
                    results.append((fig, None, str(e)))

        # Log rate limiter stats at end
        stats = rate_limiter.get_stats()
        if stats["total_rate_limits"] > 0:
            log(f"Rate limiter stats: {stats['total_successes']} successes, "
                f"{stats['total_rate_limits']} rate limits, "
                f"current concurrency: {stats['current_concurrent']}")

        return results

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

    def _update_db_has_figures(self, paper_id: str) -> bool:
        """
        Update PostgreSQL has_figures column after successful figure translation.

        This is called after figures are uploaded to B2 and manifest is updated.
        It's a best-effort operation - failure doesn't affect the pipeline result.

        Args:
            paper_id: Paper ID to update

        Returns:
            True if updated, False if skipped/failed
        """
        import re

        # Validate paper_id format (same validation as _find_pdf)
        if not re.match(r'^[\w\-\.]+$', paper_id) or '..' in paper_id:
            log(f"Invalid paper_id format for DB update: {paper_id}")
            return False

        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            log(f"DATABASE_URL not set, skipping has_figures update for {paper_id}")
            return False

        try:
            import psycopg2
        except ImportError:
            log("psycopg2 not installed, skipping has_figures update")
            return False

        try:
            conn = psycopg2.connect(database_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE papers SET has_figures = TRUE WHERE id = %s AND has_figures = FALSE",
                        (paper_id,),
                    )
                    updated = cur.rowcount > 0
                    conn.commit()
                    if updated:
                        log(f"Updated has_figures=TRUE for {paper_id}")
                    return updated
            finally:
                conn.close()
        except Exception as e:
            log(f"Failed to update has_figures for {paper_id}: {e}")
            return False


__all__ = [
    "FigurePipeline",
    "Figure",
    "FigureProcessingResult",
    "PipelineConfig",
    "ProcessingStatus",
]
