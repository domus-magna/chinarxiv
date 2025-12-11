#!/usr/bin/env python3
"""
Universal pipeline orchestrator.

ALL pipeline logic lives here - the GitHub Action just calls this.
This keeps the YAML dumb and the logic testable.

Usage:
    # Process a specific month
    python -m src.orchestrator --scope month --target 202401

    # Process specific papers
    python -m src.orchestrator --scope list --target chinaxiv-202401.00001,chinaxiv-202401.00002

    # Resume pending/zombie papers (scheduled runs)
    python -m src.orchestrator --scope smart-resume

    # Force re-process
    python -m src.orchestrator --scope month --target 202401 --force

Environment:
    DATABASE_URL: PostgreSQL connection string (required)
    OPENROUTER_API_KEY: Text translation API key
    GEMINI_API_KEY: Figure translation API key
    MOONDREAM_API_KEY: Figure QA API key
    BRIGHTDATA_*: PDF harvesting credentials
    BACKBLAZE_*: B2 storage credentials
    DISCORD_WEBHOOK_URL: Alerting (optional)
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from .utils import log
from .alerts import alert_critical, pipeline_complete, pipeline_started


# ============================================================================
# Constants
# ============================================================================

ZOMBIE_TIMEOUT = timedelta(hours=4)  # Papers processing > 4 hours are zombies

# Default stages (full pipeline)
DEFAULT_STAGES = ['harvest', 'text', 'figures', 'pdf', 'post']


# ============================================================================
# Data Classes
# ============================================================================

class ProcessingStatus(Enum):
    """Processing status values."""
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETE = 'complete'
    FAILED = 'failed'
    SKIPPED = 'skipped'


@dataclass
class ProcessingResult:
    """Result of processing a single paper."""
    paper_id: str
    status: str  # 'success', 'failed', 'skipped'
    error: Optional[str] = None
    stages_completed: list[str] = None

    def __post_init__(self):
        if self.stages_completed is None:
            self.stages_completed = []


@dataclass
class OrchestratorStats:
    """Aggregate stats for orchestrator run."""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# ============================================================================
# Database Operations
# ============================================================================

def get_db_connection():
    """Get PostgreSQL connection."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def get_papers_by_month(conn, month: str) -> list[str]:
    """Get paper IDs for a specific month (YYYYMM format)."""
    cursor = conn.cursor()
    # Papers are ID'd like chinaxiv-YYYYMM.NNNNN
    pattern = f'chinaxiv-{month}.%'
    cursor.execute(
        "SELECT id FROM papers WHERE id LIKE %s ORDER BY id",
        (pattern,)
    )
    return [row['id'] for row in cursor.fetchall()]


def get_papers_needing_work(
    conn,
    text_only: bool = False,
    figures_only: bool = False,
    include_failed: bool = False
) -> list[str]:
    """
    Get paper IDs that need processing.

    Includes:
    - Papers with processing_status = 'pending'
    - Zombie papers (processing > ZOMBIE_TIMEOUT hours ago)
    - Papers missing specific stages (text, figures, pdf)
    - Optionally: failed papers (for retry)

    Args:
        text_only: Only find papers needing text translation
        figures_only: Only find papers needing figure translation
        include_failed: Include papers that previously failed (for retry)
    """
    cursor = conn.cursor()

    if text_only:
        # Papers that have no text translation
        if include_failed:
            cursor.execute("""
                SELECT id FROM papers
                WHERE text_status NOT IN ('complete', 'skipped')
                ORDER BY id
            """)
        else:
            cursor.execute("""
                SELECT id FROM papers
                WHERE text_status NOT IN ('complete', 'skipped', 'failed')
                ORDER BY id
            """)
    elif figures_only:
        # Papers that have text but no figures
        if include_failed:
            cursor.execute("""
                SELECT id FROM papers
                WHERE text_status = 'complete'
                  AND figures_status NOT IN ('complete', 'skipped')
                ORDER BY id
            """)
        else:
            cursor.execute("""
                SELECT id FROM papers
                WHERE text_status = 'complete'
                  AND figures_status NOT IN ('complete', 'skipped', 'failed')
                ORDER BY id
            """)
    else:
        # Papers that need any work (pending, zombie, or optionally failed)
        if include_failed:
            cursor.execute("""
                SELECT id FROM papers
                WHERE processing_status IN ('pending', 'failed')
                   OR (processing_status = 'processing'
                       AND processing_started_at < NOW() - INTERVAL '4 hours')
                ORDER BY id
            """)
        else:
            cursor.execute("""
                SELECT id FROM papers
                WHERE processing_status = 'pending'
                   OR (processing_status = 'processing'
                       AND processing_started_at < NOW() - INTERVAL '4 hours')
                ORDER BY id
            """)

    return [row['id'] for row in cursor.fetchall()]


def get_paper_status(conn, paper_id: str) -> dict:
    """Get current processing status for a paper."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, processing_status, text_status, figures_status, pdf_status,
               has_chinese_pdf, has_english_pdf, processing_started_at, processing_error,
               text_completed_at, figures_completed_at, pdf_completed_at
        FROM papers
        WHERE id = %s
    """, (paper_id,))
    row = cursor.fetchone()
    return dict(row) if row else {}


def acquire_paper_lock(conn, paper_id: str, include_failed: bool = False) -> bool:
    """
    Try to acquire exclusive lock on a paper for processing.

    Uses UPDATE with RETURNING to atomically claim the paper.
    Only succeeds if paper is pending, is a zombie, or (optionally) failed.

    Args:
        conn: Database connection
        paper_id: Paper ID to lock
        include_failed: If True, also allow locking failed papers for retry

    Returns:
        True if lock acquired, False if paper is already being processed
    """
    cursor = conn.cursor()

    if include_failed:
        cursor.execute("""
            UPDATE papers
            SET processing_status = 'processing',
                processing_started_at = NOW(),
                processing_error = NULL
            WHERE id = %s
              AND (processing_status IN ('pending', 'failed')
                   OR (processing_status = 'processing'
                       AND processing_started_at < NOW() - INTERVAL '4 hours'))
            RETURNING id
        """, (paper_id,))
    else:
        cursor.execute("""
            UPDATE papers
            SET processing_status = 'processing',
                processing_started_at = NOW(),
                processing_error = NULL
            WHERE id = %s
              AND (processing_status = 'pending'
                   OR (processing_status = 'processing'
                       AND processing_started_at < NOW() - INTERVAL '4 hours'))
            RETURNING id
        """, (paper_id,))

    conn.commit()
    return cursor.fetchone() is not None


def update_stage_status(
    conn,
    paper_id: str,
    stage: str,
    status: str,
    error: Optional[str] = None
) -> None:
    """Update status for a specific stage."""
    cursor = conn.cursor()

    if stage == 'text':
        if status == 'complete':
            cursor.execute("""
                UPDATE papers
                SET text_status = 'complete', text_completed_at = NOW()
                WHERE id = %s
            """, (paper_id,))
        else:
            cursor.execute("""
                UPDATE papers SET text_status = %s WHERE id = %s
            """, (status, paper_id))

    elif stage == 'figures':
        if status == 'complete':
            cursor.execute("""
                UPDATE papers
                SET figures_status = 'complete', figures_completed_at = NOW()
                WHERE id = %s
            """, (paper_id,))
        else:
            cursor.execute("""
                UPDATE papers SET figures_status = %s WHERE id = %s
            """, (status, paper_id))

    elif stage == 'pdf':
        if status == 'complete':
            cursor.execute("""
                UPDATE papers
                SET pdf_status = 'complete', pdf_completed_at = NOW()
                WHERE id = %s
            """, (paper_id,))
        else:
            cursor.execute("""
                UPDATE papers SET pdf_status = %s WHERE id = %s
            """, (status, paper_id))

    conn.commit()


def mark_paper_complete(conn, paper_id: str) -> None:
    """Mark paper as fully processed."""
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE papers
        SET processing_status = 'complete',
            processing_started_at = NULL,
            processing_error = NULL
        WHERE id = %s
    """, (paper_id,))
    conn.commit()


def mark_paper_failed(conn, paper_id: str, error: str) -> None:
    """Mark paper as failed with error message."""
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE papers
        SET processing_status = 'failed',
            processing_error = %s
        WHERE id = %s
    """, (error[:500], paper_id))  # Truncate long errors
    conn.commit()


def release_paper_lock(conn, paper_id: str) -> None:
    """Release lock on a paper (set back to pending)."""
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE papers
        SET processing_status = 'pending',
            processing_started_at = NULL
        WHERE id = %s
          AND processing_status = 'processing'
    """, (paper_id,))
    conn.commit()


# ============================================================================
# Pipeline Stage Functions
# ============================================================================

def run_harvest(conn, paper_id: str) -> bool:
    """
    Harvest/download PDF for a paper.

    Tries in order:
    1. Check if PDF exists locally
    2. Download from B2 storage
    3. Download from source (requires pdf_url in DB or records)

    Args:
        conn: Database connection (to lookup pdf_url if needed)
        paper_id: Paper identifier

    Returns:
        True if PDF is available, False otherwise
    """
    from .pdf_pipeline import download_pdf

    log(f"  Harvesting PDF for {paper_id}...")

    # Ensure data/pdfs directory exists
    pdf_dir = "data/pdfs"
    os.makedirs(pdf_dir, exist_ok=True)

    pdf_path = f"{pdf_dir}/{paper_id}.pdf"

    # 1. Check if PDF already exists locally
    if os.path.exists(pdf_path):
        log(f"    PDF already exists: {pdf_path}")
        return True

    # 2. Try to download from B2 first (cheaper than source scraping)
    try:
        if _download_pdf_from_b2(paper_id, pdf_path):
            log(f"    Downloaded from B2: {pdf_path}")
            return True
    except Exception as e:
        log(f"    B2 download failed: {e}")

    # 3. Fall back to downloading from source - need pdf_url
    cursor = conn.cursor()
    cursor.execute("SELECT pdf_url, source_url FROM papers WHERE id = %s", (paper_id,))
    row = cursor.fetchone()

    if not row or not row.get('pdf_url'):
        log(f"    No pdf_url in database for {paper_id}")
        # Try to find in records files
        pdf_url, source_url = _find_pdf_url_in_records(paper_id)
        if not pdf_url:
            log(f"    Could not find pdf_url for {paper_id}")
            return False
    else:
        pdf_url = row['pdf_url']
        source_url = row.get('source_url')

    # Download from source
    try:
        referer = source_url if source_url else None
        success = download_pdf(pdf_url, pdf_path, referer=referer, session_id=paper_id)
        if success:
            log(f"    Downloaded PDF from source: {pdf_path}")
            return True
    except Exception as e:
        log(f"    PDF download failed: {e}")

    return False


def _download_pdf_from_b2(paper_id: str, output_path: str) -> bool:
    """
    Download PDF from B2 storage.

    Returns:
        True if downloaded successfully, False otherwise
    """
    import boto3
    from botocore.config import Config

    key_id = os.environ.get('AWS_ACCESS_KEY_ID') or os.environ.get('BACKBLAZE_KEY_ID')
    secret = os.environ.get('AWS_SECRET_ACCESS_KEY') or os.environ.get('BACKBLAZE_APPLICATION_KEY')
    endpoint = os.environ.get('BACKBLAZE_S3_ENDPOINT')
    bucket = os.environ.get('BACKBLAZE_BUCKET')
    prefix = os.environ.get('BACKBLAZE_PREFIX', '')

    if not all([key_id, secret, endpoint, bucket]):
        return False

    s3 = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(signature_version='s3v4', region_name='us-west-004')
    )

    key = f"{prefix}pdfs/{paper_id}.pdf"
    try:
        s3.download_file(bucket, key, output_path)
        return os.path.exists(output_path)
    except Exception:
        return False


def _find_pdf_url_in_records(paper_id: str) -> tuple[str, str]:
    """
    Find pdf_url for a paper from records files.

    Returns:
        Tuple of (pdf_url, source_url) or (None, None) if not found
    """
    import json
    from pathlib import Path

    records_dir = Path("data/records")
    if not records_dir.exists():
        return None, None

    # Extract month from paper_id (e.g., chinaxiv-202401.00001 -> 202401)
    try:
        month = paper_id.split('-')[1].split('.')[0]
        records_file = records_dir / f"chinaxiv_{month}.json"
    except (IndexError, ValueError):
        return None, None

    if not records_file.exists():
        return None, None

    try:
        with open(records_file, encoding='utf-8') as f:
            records = json.load(f)
        for rec in records:
            if rec.get('id') == paper_id:
                return rec.get('pdf_url'), rec.get('source_url')
    except Exception:
        pass

    return None, None


def run_text_translation(paper_id: str, dry_run: bool = False) -> bool:
    """
    Run text translation for a paper.

    Returns:
        True if translation succeeded, False otherwise
    """
    from .translate import translate_paper

    log(f"  Translating text for {paper_id}...")

    try:
        result_path = translate_paper(paper_id, dry_run=dry_run)
        if result_path and os.path.exists(result_path):
            log(f"    Translation saved: {result_path}")
            return True
        return False
    except Exception as e:
        log(f"    Translation failed: {e}")
        raise


def run_figure_translation(paper_id: str, dry_run: bool = False) -> bool:
    """
    Run figure translation for a paper.

    Returns:
        True if figure translation succeeded (or paper has no figures), False otherwise
    """
    from .figure_pipeline import FigurePipeline, PipelineConfig

    log(f"  Translating figures for {paper_id}...")

    config = PipelineConfig(
        gemini_api_key=os.environ.get("GEMINI_API_KEY"),
        moondream_api_key=os.environ.get("MOONDREAM_API_KEY"),
        dry_run=dry_run,
    )

    try:
        pipeline = FigurePipeline(config)
        result = pipeline.process_paper(paper_id)

        if result.total_figures == 0:
            log(f"    No figures found in {paper_id}")
            return True  # Success - no figures to translate

        if result.translated > 0:
            log(f"    Translated {result.translated}/{result.total_figures} figures")
            return True

        if result.failed > 0:
            log(f"    Figure translation failed: {result.failed} failures")
            return False

        return True
    except Exception as e:
        log(f"    Figure translation failed: {e}")
        raise


def run_pdf_generation(paper_id: str, dry_run: bool = False) -> bool:
    """
    Generate English PDF for a paper.

    Returns:
        True if PDF generation succeeded, False otherwise
    """
    # Import here to avoid circular imports
    try:
        from scripts.generate_english_pdfs import generate_english_pdf
    except ImportError:
        log(f"    PDF generation not available")
        return False

    log(f"  Generating English PDF for {paper_id}...")

    try:
        result = generate_english_pdf(paper_id, dry_run=dry_run)
        if result:
            log(f"    English PDF generated")
            return True
        return False
    except Exception as e:
        log(f"    PDF generation failed: {e}")
        raise


def run_post_processing(paper_id: str, dry_run: bool = False) -> bool:
    """
    Run post-processing (upload to B2, update manifest, etc.).

    Returns:
        True if post-processing succeeded, False otherwise
    """
    log(f"  Post-processing {paper_id}...")

    if dry_run:
        log(f"    [DRY RUN] Would upload to B2")
        return True

    # Upload translation to B2
    try:
        from .tools.b2_publish import upload_translation
        translation_path = f"data/translated/{paper_id}.json"
        if os.path.exists(translation_path):
            upload_translation(paper_id, translation_path)
            log(f"    Uploaded translation to B2")
    except Exception as e:
        log(f"    B2 upload failed: {e}")
        # Non-fatal - continue

    return True


# ============================================================================
# Main Processing Logic
# ============================================================================

def process_paper(
    paper_id: str,
    stages: list[str],
    dry_run: bool = False,
    notify: Optional[Callable] = None,
    is_partial_mode: bool = False,
    include_failed: bool = False
) -> ProcessingResult:
    """
    Process a single paper through the pipeline stages.

    Args:
        paper_id: Paper identifier
        stages: List of stages to run (harvest, text, figures, pdf, post)
        dry_run: If True, skip actual processing
        notify: Optional callback for alerts
        is_partial_mode: If True (text-only or figures-only), don't mark paper
                        as fully complete - only update stage-specific status
        include_failed: If True, allow retrying previously failed papers

    Returns:
        ProcessingResult with status and any errors
    """
    result = ProcessingResult(paper_id=paper_id, status='success')
    conn = None

    try:
        conn = get_db_connection()

        # Try to acquire lock
        if not acquire_paper_lock(conn, paper_id, include_failed=include_failed):
            log(f"SKIP {paper_id} - already being processed or not eligible")
            result.status = 'skipped'
            return result

        # Get current status to check what's already done
        status = get_paper_status(conn, paper_id)

        # Run stages
        for stage in stages:
            try:
                if stage == 'harvest':
                    if not run_harvest(conn, paper_id):
                        raise RuntimeError("PDF not available")
                    result.stages_completed.append('harvest')

                elif stage == 'text':
                    # Skip if already complete
                    if status.get('text_status') == 'complete':
                        log(f"    Text already complete for {paper_id}")
                        result.stages_completed.append('text')
                        continue

                    update_stage_status(conn, paper_id, 'text', 'processing')
                    if run_text_translation(paper_id, dry_run=dry_run):
                        update_stage_status(conn, paper_id, 'text', 'complete')
                        result.stages_completed.append('text')
                    else:
                        update_stage_status(conn, paper_id, 'text', 'failed')
                        raise RuntimeError("Text translation failed")

                elif stage == 'figures':
                    # Skip if already complete
                    if status.get('figures_status') == 'complete':
                        log(f"    Figures already complete for {paper_id}")
                        result.stages_completed.append('figures')
                        continue

                    update_stage_status(conn, paper_id, 'figures', 'processing')
                    if run_figure_translation(paper_id, dry_run=dry_run):
                        update_stage_status(conn, paper_id, 'figures', 'complete')
                        result.stages_completed.append('figures')
                    else:
                        update_stage_status(conn, paper_id, 'figures', 'failed')
                        raise RuntimeError("Figure translation failed")

                elif stage == 'pdf':
                    # Skip if already complete
                    if status.get('pdf_status') == 'complete':
                        log(f"    PDF already complete for {paper_id}")
                        result.stages_completed.append('pdf')
                        continue

                    update_stage_status(conn, paper_id, 'pdf', 'processing')
                    if run_pdf_generation(paper_id, dry_run=dry_run):
                        update_stage_status(conn, paper_id, 'pdf', 'complete')
                        result.stages_completed.append('pdf')
                    else:
                        # PDF generation is optional - don't fail
                        update_stage_status(conn, paper_id, 'pdf', 'skipped')
                        log(f"    PDF generation skipped for {paper_id}")

                elif stage == 'post':
                    run_post_processing(paper_id, dry_run=dry_run)
                    result.stages_completed.append('post')

            except Exception as e:
                log(f"    Stage '{stage}' failed: {e}")
                # Update stage-specific status to failed
                if stage in ('text', 'figures', 'pdf'):
                    update_stage_status(conn, paper_id, stage, 'failed')
                result.status = 'failed'
                result.error = f"{stage}: {str(e)}"
                mark_paper_failed(conn, paper_id, result.error)
                if notify:
                    notify(f"Stage '{stage}' failed for {paper_id}: {e}")
                return result

        # All requested stages completed
        if is_partial_mode:
            # In partial mode (text-only or figures-only), only release the lock
            # Don't mark the paper as fully complete since other stages remain
            release_paper_lock(conn, paper_id)
            log(f"Partial mode completed {paper_id}: stages={result.stages_completed}")
        else:
            # Full pipeline mode - check if paper is actually complete
            # Refresh status to see if all stages are done
            updated_status = get_paper_status(conn, paper_id)
            all_complete = (
                updated_status.get('text_status') == 'complete' and
                updated_status.get('figures_status') in ('complete', 'skipped') and
                updated_status.get('pdf_status') in ('complete', 'skipped')
            )
            if all_complete:
                mark_paper_complete(conn, paper_id)
                log(f"Fully completed {paper_id}: stages={result.stages_completed}")
            else:
                # Not all stages done - release lock but leave as pending
                release_paper_lock(conn, paper_id)
                log(f"Partial completion {paper_id}: stages={result.stages_completed}")
        return result

    except Exception as e:
        result.status = 'failed'
        result.error = str(e)
        log(f"ERROR processing {paper_id}: {e}")
        traceback.print_exc()

        if conn:
            mark_paper_failed(conn, paper_id, str(e))

        if notify:
            notify(f"Processing failed for {paper_id}: {e}")

        return result

    finally:
        if conn:
            conn.close()


def get_work_queue(
    scope: str,
    target: str,
    force: bool = False,
    text_only: bool = False,
    figures_only: bool = False,
    include_failed: bool = False
) -> list[str]:
    """
    Resolve scope to paper IDs and filter by what needs work.

    Args:
        scope: One of 'month', 'list', 'file', 'smart-resume'
        target: YYYYMM for month, comma-list for list, filepath for file
        force: If True, include papers even if already complete
        text_only: Only find papers needing text translation
        figures_only: Only find papers needing figure translation
        include_failed: If True, include failed papers for retry

    Returns:
        List of paper IDs to process
    """
    conn = get_db_connection()

    try:
        # Resolve scope to paper IDs
        if scope == 'month':
            if not target or len(target) != 6:
                raise ValueError(f"Month must be YYYYMM format, got: {target}")
            papers = get_papers_by_month(conn, target)
            log(f"Found {len(papers)} papers for month {target}")

        elif scope == 'list':
            if not target:
                raise ValueError("List scope requires comma-separated paper IDs")
            papers = [p.strip() for p in target.split(',') if p.strip()]
            log(f"Processing {len(papers)} papers from list")

        elif scope == 'file':
            if not target or not os.path.exists(target):
                raise ValueError(f"File not found: {target}")
            with open(target) as f:
                papers = [line.strip() for line in f if line.strip()]
            log(f"Loaded {len(papers)} papers from {target}")

        elif scope == 'smart-resume':
            papers = get_papers_needing_work(
                conn,
                text_only=text_only,
                figures_only=figures_only,
                include_failed=include_failed
            )
            log(f"Found {len(papers)} papers needing work (include_failed={include_failed})")

        else:
            raise ValueError(f"Unknown scope: {scope}")

        # Filter by what needs doing (unless force)
        if force:
            log(f"Force mode: processing all {len(papers)} papers")
            return papers

        # Filter out already-complete papers (and optionally failed papers)
        work_queue = []
        for paper_id in papers:
            status = get_paper_status(conn, paper_id)

            if not status:
                # Paper not in DB - add to queue
                work_queue.append(paper_id)
                continue

            proc_status = status.get('processing_status')

            if proc_status == 'complete':
                # Check if specific stages are requested and incomplete
                if text_only and status.get('text_status') != 'complete':
                    work_queue.append(paper_id)
                elif figures_only and status.get('figures_status') != 'complete':
                    work_queue.append(paper_id)
                # Otherwise skip complete papers
            elif proc_status == 'failed':
                # Only include failed if explicitly requested
                if include_failed:
                    work_queue.append(paper_id)
            else:
                work_queue.append(paper_id)

        log(f"Work queue: {len(work_queue)} papers (filtered from {len(papers)})")
        return work_queue

    finally:
        conn.close()


def run_orchestrator(
    scope: str,
    target: Optional[str],
    force: bool = False,
    dry_run: bool = False,
    workers: int = 10,
    text_only: bool = False,
    figures_only: bool = False,
    include_failed: bool = False,
) -> OrchestratorStats:
    """
    Main orchestrator entry point.

    Args:
        scope: Scope type (month, list, file, smart-resume)
        target: Target value for scope
        force: Force reprocessing of complete papers
        dry_run: Skip actual processing
        workers: Number of parallel workers
        text_only: Only run text translation stage
        figures_only: Only run figure translation stage
        include_failed: Include previously failed papers for retry

    Returns:
        OrchestratorStats with results
    """
    stats = OrchestratorStats()

    # Determine stages and partial mode based on flags
    is_partial_mode = text_only or figures_only

    if text_only:
        stages = ['harvest', 'text', 'post']
        log("Running TEXT ONLY mode")
    elif figures_only:
        stages = ['figures', 'post']
        log("Running FIGURES ONLY mode")
    else:
        stages = DEFAULT_STAGES
        log("Running FULL PIPELINE mode")

    if include_failed:
        log("Including previously failed papers for retry")

    # Get work queue
    try:
        work_queue = get_work_queue(
            scope, target, force,
            text_only=text_only,
            figures_only=figures_only,
            include_failed=include_failed
        )
    except Exception as e:
        log(f"ERROR getting work queue: {e}")
        stats.errors.append(str(e))
        return stats

    stats.total = len(work_queue)

    if not work_queue:
        log("No papers to process")
        return stats

    # Send start notification
    pipeline_started(
        papers_count=len(work_queue),
        source="orchestrator",
        with_figures='figures' in stages,
    )

    # Discord notification callback
    def notify(message: str):
        try:
            alert_critical("Pipeline Error", message, source="orchestrator")
        except Exception:
            pass

    # Process papers
    log(f"Processing {len(work_queue)} papers with {workers} workers...")

    if workers == 1:
        # Sequential processing
        for paper_id in work_queue:
            result = process_paper(
                paper_id, stages, dry_run=dry_run, notify=notify,
                is_partial_mode=is_partial_mode, include_failed=include_failed
            )
            if result.status == 'success':
                stats.success += 1
            elif result.status == 'skipped':
                stats.skipped += 1
            else:
                stats.failed += 1
                if result.error:
                    stats.errors.append(f"{paper_id}: {result.error}")
    else:
        # Parallel processing
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    process_paper, pid, stages, dry_run, notify,
                    is_partial_mode, include_failed
                ): pid
                for pid in work_queue
            }

            for future in as_completed(futures):
                paper_id = futures[future]
                try:
                    result = future.result()
                    if result.status == 'success':
                        stats.success += 1
                    elif result.status == 'skipped':
                        stats.skipped += 1
                    else:
                        stats.failed += 1
                        if result.error:
                            stats.errors.append(f"{paper_id}: {result.error}")
                except Exception as e:
                    stats.failed += 1
                    stats.errors.append(f"{paper_id}: {e}")

    # Send completion notification
    pipeline_complete(
        successes=stats.success,
        failures=stats.failed,
        flagged=0,
        source="orchestrator",
    )

    # Summary
    log("")
    log("=" * 50)
    log("ORCHESTRATOR SUMMARY")
    log("=" * 50)
    log(f"Total:   {stats.total}")
    log(f"Success: {stats.success}")
    log(f"Failed:  {stats.failed}")
    log(f"Skipped: {stats.skipped}")

    if stats.errors:
        log("")
        log("Errors:")
        for error in stats.errors[:10]:  # Show first 10
            log(f"  - {error}")
        if len(stats.errors) > 10:
            log(f"  ... and {len(stats.errors) - 10} more")

    return stats


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Universal pipeline orchestrator'
    )
    parser.add_argument(
        '--scope',
        required=True,
        choices=['month', 'list', 'file', 'smart-resume'],
        help='What to process'
    )
    parser.add_argument(
        '--target',
        help='YYYYMM for month, comma-list for list, filepath for file'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Re-run even if DB says complete'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Skip actual processing'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=10,
        help='Number of parallel workers (default: 10)'
    )
    parser.add_argument(
        '--text-only',
        action='store_true',
        dest='text_only',
        help='[Admin] Only run text translation (skip figures)'
    )
    parser.add_argument(
        '--figures-only',
        action='store_true',
        dest='figures_only',
        help='[Admin] Only run figure translation (skip text)'
    )
    parser.add_argument(
        '--include-failed',
        action='store_true',
        dest='include_failed',
        help='Include previously failed papers for retry'
    )

    args = parser.parse_args()

    # Validate mutual exclusivity
    if args.text_only and args.figures_only:
        parser.error("Cannot use both --text-only and --figures-only")

    # Run orchestrator
    stats = run_orchestrator(
        scope=args.scope,
        target=args.target,
        force=args.force,
        dry_run=args.dry_run,
        workers=args.workers,
        text_only=args.text_only,
        figures_only=args.figures_only,
        include_failed=args.include_failed,
    )

    # Exit with error code if any failures
    if stats.failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
