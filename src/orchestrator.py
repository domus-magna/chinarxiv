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
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
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

# Cached schema feature flags (set on first use)
_papers_has_license_column: Optional[bool] = None


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
    - Failed papers older than 7 days (auto-retry for transient failures)
    - Failed papers of any age (if include_failed=True)

    Args:
        text_only: Only find papers needing text translation
        figures_only: Only find papers needing figure translation
        include_failed: Include all failed papers (not just old ones)
    """
    cursor = conn.cursor()

    if text_only:
        # Papers that have no text translation OR no English PDF.
        #
        # "text_only" is used operationally as "skip figures" to conserve spend,
        # but we still want English PDFs for papers whose text is already done.
        cursor.execute("""
            SELECT id FROM papers
            WHERE (
                (text_status != 'complete' AND text_status != 'skipped')
                OR (pdf_status != 'complete' AND pdf_status != 'skipped')
            )
            ORDER BY id
        """)
    elif figures_only:
        # Papers that have text but no figures
        cursor.execute("""
            SELECT id FROM papers
            WHERE text_status = 'complete'
              AND figures_status != 'complete'
              AND figures_status != 'skipped'
            ORDER BY id
        """)
    else:
        # Base query: pending or zombie papers
        # Plus: auto-retry failed papers after 7 days (transient failures like
        # API rate limits, network issues should heal automatically)
        if include_failed:
            # Include ALL failed papers (for explicit retry)
            cursor.execute("""
                SELECT id FROM papers
                WHERE processing_status = 'pending'
                   OR (processing_status = 'processing'
                       AND processing_started_at < NOW() - INTERVAL '4 hours')
                   OR processing_status = 'failed'
                ORDER BY id
            """)
        else:
            # Auto-retry failed papers older than 7 days
            cursor.execute("""
                SELECT id FROM papers
                WHERE processing_status = 'pending'
                   OR (processing_status = 'processing'
                       AND processing_started_at < NOW() - INTERVAL '4 hours')
                   OR (processing_status = 'failed'
                       AND processing_started_at < NOW() - INTERVAL '7 days')
                ORDER BY id
            """)

    return [row['id'] for row in cursor.fetchall()]


def get_paper_status(conn, paper_id: str) -> dict:
    """Get current processing status for a paper."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, processing_status, text_status, figures_status, pdf_status,
               has_chinese_pdf, has_english_pdf, processing_started_at, processing_error,
               text_completed_at, figures_completed_at, pdf_completed_at,
               pdf_url, source_url
        FROM papers
        WHERE id = %s
    """, (paper_id,))
    row = cursor.fetchone()
    return dict(row) if row else {}


def acquire_paper_lock(conn, paper_id: str) -> bool:
    """
    Try to acquire exclusive lock on a paper for processing.

    Uses UPDATE with RETURNING to atomically claim the paper.
    Succeeds if the paper is pending OR is a zombie OR has incomplete stages.

    Note: processing_status='complete' is not a reliable indicator that all
    stages are complete (e.g., text done but English PDF still pending). We use
    per-stage statuses as the source of truth for whether work remains.

    Returns:
        True if lock acquired, False if paper is already being processed
    """
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE papers
        SET processing_status = 'processing',
            processing_started_at = NOW(),
            processing_error = NULL
        WHERE id = %s
          AND (
            -- If currently processing, only allow zombie recovery.
            (
              processing_status = 'processing'
              AND processing_started_at < NOW() - INTERVAL '4 hours'
            )
            OR
            -- Otherwise, allow claiming if the paper is pending/failed OR has incomplete stages.
            (
              processing_status != 'processing'
              AND (
                processing_status = 'pending'
                OR processing_status = 'failed'
                OR (text_status != 'complete' AND text_status != 'skipped')
                OR (pdf_status != 'complete' AND pdf_status != 'skipped')
                OR (figures_status != 'complete' AND figures_status != 'skipped')
              )
            )
          )
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


def reconcile_qa_status(conn) -> int:
    """
    Fix papers where text is complete but qa_status isn't 'pass'.

    This can happen when:
    - Text translation saved successfully but qa_status update failed
    - Old translation runs before qa_status was properly tracked
    - Database inconsistencies from partial failures

    Only sets qa_status='pass' for papers that have actual translated content.

    Returns:
        Number of papers reconciled
    """
    cursor = conn.cursor()

    # Find papers with text complete but qa_status not 'pass'
    # AND that have actual translated content (title_en is not empty)
    cursor.execute("""
        UPDATE papers
        SET qa_status = 'pass'
        WHERE text_status = 'complete'
          AND qa_status != 'pass'
          AND title_en IS NOT NULL
          AND title_en != ''
          AND abstract_en IS NOT NULL
          AND abstract_en != ''
        RETURNING id
    """)

    reconciled = cursor.fetchall()
    conn.commit()

    if reconciled:
        count = len(reconciled)
        log(f"Reconciled qa_status for {count} papers")
        return count

    return 0


# ============================================================================
# Discovery Functions (Paper Harvesting)
# ============================================================================

def run_discover(month: str, dry_run: bool = False) -> list[str]:
    """
    Discover papers for a month from ChinaXiv and import to DB.

    This scrapes ChinaXiv for all papers in a given month, inserts new papers
    into the database, and uploads records to B2 for archival.

    Args:
        month: Month to discover (YYYYMM format, e.g., "202501")
        dry_run: If True, don't write to DB or B2

    Returns:
        List of newly discovered paper IDs (papers that weren't in DB before)
    """
    from .harvest_chinaxiv_optimized import OptimizedChinaXivScraper

    log(f"Discovering papers for {month}...")

    # Check credentials
    api_key = os.environ.get('BRIGHTDATA_API_KEY')
    zone = os.environ.get('BRIGHTDATA_ZONE')

    if not api_key or not zone:
        raise RuntimeError(
            "Discovery requires BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE"
        )

    # Initialize scraper
    scraper = OptimizedChinaXivScraper(api_key, zone, rate_limit=0.5)

    # Phase 1: Get max ID from homepage or binary search
    log("  Phase 1: Finding max paper ID...")
    homepage_maxes = scraper.extract_homepage_max_ids()

    if month in homepage_maxes:
        max_id = homepage_maxes[month]
        log(f"    Found max ID from homepage: {month}.{max_id:05d}")
    else:
        log("    Not on homepage, using binary search...")
        max_id = scraper.find_max_id_binary_search(month)
        log(f"    Found max ID via search: {month}.{max_id:05d}")

    if max_id == 0:
        log(f"  No papers found for {month}")
        return []

    # Phase 2: Scrape all papers in the month
    log(f"  Phase 2: Scraping papers 1-{max_id}...")
    records = scraper.scrape_month_optimized(month, max_id)
    log(f"    Scraped {len(records)} papers")

    if dry_run:
        log(f"  [DRY RUN] Would import {len(records)} papers to DB")
        return [r['id'] for r in records]

    # Phase 3: Import to database
    log("  Phase 3: Importing to database...")
    conn = get_db_connection()
    new_ids = []
    existing_count = 0

    try:
        for record in records:
            if insert_paper_if_new(conn, record):
                new_ids.append(record['id'])
            else:
                existing_count += 1
        conn.commit()
        log(f"    Imported {len(new_ids)} new papers ({existing_count} already existed)")
    finally:
        conn.close()

    # Phase 4: Upload records to B2 for archival
    log("  Phase 4: Uploading records to B2...")
    upload_records_to_b2(month, records)

    log(f"Discovery complete for {month}: {len(new_ids)} new papers")
    return new_ids


def insert_paper_if_new(conn, record: dict) -> bool:
    """
    Insert paper to DB if it doesn't already exist.

    Args:
        conn: Database connection
        record: Paper metadata dict from scraper (has 'title', 'abstract', 'creators', 'subjects')

    Returns:
        True if paper was inserted (new), False if already existed

    Note:
        The scraper returns Chinese metadata. We store this in _cn columns (source of truth).
        The _en columns are left NULL until translation runs.
        Subjects are stored in both subjects_cn (JSONB) and paper_subjects table.
    """
    import json

    cursor = conn.cursor()

    # Check if paper already exists
    cursor.execute("SELECT 1 FROM papers WHERE id = %s", (record['id'],))
    if cursor.fetchone():
        return False  # Already exists

    # Convert creators list to JSONB format
    creators = record.get('creators', [])
    if isinstance(creators, list):
        creators_json = json.dumps(creators)
    else:
        creators_json = json.dumps([])

    # Convert subjects list to JSONB format
    subjects = record.get('subjects', [])
    if isinstance(subjects, list):
        subjects_json = json.dumps(subjects)
    else:
        subjects_json = json.dumps([])

    # Compute license metadata (may scrape landing page if raw not present).
    # Unknown licenses are treated as allowed downstream.
    try:
        from .licenses import decide_derivatives_allowed

        record = decide_derivatives_allowed(record)
    except Exception as e:
        log(f"    WARNING: License detection failed for {record.get('id')}: {e}")
        record["license"] = {"raw": "", "label": None, "derivatives_allowed": None, "badge": None}

    license_json = json.dumps(record.get("license")) if record.get("license") else None

    # Detect license column once per process to avoid per-insert exceptions.
    global _papers_has_license_column
    if _papers_has_license_column is None:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'papers' AND column_name = 'license'
            """
        )
        _papers_has_license_column = cursor.fetchone() is not None

    # Insert new paper with Chinese metadata in _cn columns.
    # _en columns are left NULL - populated by translation.
    cols = [
        "id",
        "title_cn",
        "abstract_cn",
        "creators_cn",
        "subjects_cn",
        "title_en",
        "abstract_en",
        "creators_en",
        "date",
        "source_url",
        "pdf_url",
    ]
    vals = [
        record["id"],
        record.get("title", ""),
        record.get("abstract", ""),
        creators_json,
        subjects_json,
        None,
        None,
        None,
        record.get("date"),
        record.get("source_url"),
        record.get("pdf_url"),
    ]

    if _papers_has_license_column:
        cols.append("license")
        vals.append(license_json)

    cols.extend(["processing_status", "text_status", "figures_status", "pdf_status"])
    vals.extend(["pending", "pending", "pending", "pending"])

    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql = f"INSERT INTO papers ({', '.join(cols)}) VALUES ({placeholders})"
    cursor.execute(insert_sql, vals)

    # Also insert subjects into paper_subjects table (for queries)
    if subjects:
        for subject in subjects:
            cursor.execute("""
                INSERT INTO paper_subjects (paper_id, subject)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (record['id'], subject))

    return True


def upload_records_to_b2(month: str, records: list[dict]) -> bool:
    """
    Upload harvested records to B2 for archival.

    Args:
        month: Month string (YYYYMM)
        records: List of paper metadata dicts

    Returns:
        True if upload succeeded, False otherwise
    """
    import json
    from pathlib import Path
    from .tools.b2_publish import _get_b2_config, _aws_cp

    endpoint, bucket, prefix = _get_b2_config()
    if not endpoint or not bucket:
        log("    WARNING: B2 not configured, skipping records upload")
        return False

    # Write to temp file
    tmp = Path(f"/tmp/chinaxiv_{month}.json")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')

    # Upload to B2
    dest_root = f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}/"
    remote_key = f"records/chinaxiv_{month}.json"
    success = _aws_cp(str(tmp), f"{dest_root}{remote_key}", endpoint)

    if success:
        log(f"    Uploaded records to B2: {remote_key}")
    else:
        log("    WARNING: Failed to upload records to B2")

    # Cleanup temp file
    import contextlib
    with contextlib.suppress(Exception):
        tmp.unlink()

    return success


# ============================================================================
# Pipeline Stage Functions
# ============================================================================

def download_pdf_from_b2(paper_id: str, local_path: str) -> bool:
    """
    Download a PDF from B2 storage.

    Args:
        paper_id: Paper identifier
        local_path: Local path to save the PDF

    Returns:
        True if download succeeded, False otherwise
    """
    import boto3
    from botocore.exceptions import ClientError

    bucket = os.environ.get('BACKBLAZE_BUCKET')
    endpoint = os.environ.get('BACKBLAZE_S3_ENDPOINT')
    key_id = os.environ.get('BACKBLAZE_KEY_ID') or os.environ.get('AWS_ACCESS_KEY_ID')
    secret = os.environ.get('BACKBLAZE_APPLICATION_KEY') or os.environ.get('AWS_SECRET_ACCESS_KEY')

    if not all([bucket, endpoint, key_id, secret]):
        return False

    try:
        s3 = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
        )

        # Try to download from pdfs/ directory
        key = f"pdfs/{paper_id}.pdf"
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        s3.download_file(bucket, key, local_path)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        raise
    except Exception:
        return False


def run_harvest(paper_id: str, dry_run: bool = False) -> bool:
    """
    Harvest/download PDF for a paper.

    Requires paper to exist in DB with pdf_url. Tries B2 first,
    then falls back to downloading from source.

    Args:
        paper_id: Paper identifier
        dry_run: If True, skip actual download

    Returns:
        True if PDF is available, False otherwise
    """
    from .pdf_pipeline import download_pdf

    log(f"  Harvesting PDF for {paper_id}...")

    # Check if PDF already exists locally
    pdf_path = f"data/pdfs/{paper_id}.pdf"
    if os.path.exists(pdf_path):
        log("    PDF already exists locally")
        return True

    if dry_run:
        log("    [DRY RUN] Would download PDF")
        return True

    # Try to download from B2 first (fastest, no API costs)
    try:
        if download_pdf_from_b2(paper_id, pdf_path):
            log("    Downloaded PDF from B2")
            return True
    except Exception as e:
        log(f"    B2 download failed: {e}")

    # Get pdf_url from database
    conn = get_db_connection()
    try:
        status = get_paper_status(conn, paper_id)
        if not status:
            log(f"    ERROR: Paper {paper_id} not in database")
            return False

        pdf_url = status.get('pdf_url')
        source_url = status.get('source_url')

        if not pdf_url:
            log(f"    ERROR: Paper {paper_id} has no pdf_url in database")
            return False

        # Download from source
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        if download_pdf(pdf_url, pdf_path, referer=source_url):
            log("    Downloaded PDF from source")
            return True

        log("    PDF download failed")
        return False
    finally:
        conn.close()


def run_text_translation(paper_id: str, dry_run: bool = False) -> bool:
    """
    Run text translation for a paper.

    Returns:
        True if translation succeeded, False otherwise
    """
    from .translate import translate_paper

    log(f"  Translating text for {paper_id}...")

    try:
        # translate_paper returns paper_id on success (saves to DB + local file)
        result = translate_paper(paper_id, dry_run=dry_run)
        if result:
            # Verify local backup was written (optional check)
            local_path = f"data/translated/{paper_id}.json"
            if os.path.exists(local_path):
                log(f"    Translation saved: {local_path}")
            else:
                log(f"    Translation saved to database for {paper_id}")
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

    Loads the translation JSON, fetches figure manifest from B2,
    and generates the PDF using pandoc/xelatex.

    Returns:
        True if PDF generation succeeded, False otherwise
    """
    import json
    from pathlib import Path

    log(f"  Generating English PDF for {paper_id}...")

    if dry_run:
        log("    [DRY RUN] Would generate English PDF")
        return True

    # Helper: download translation JSON from B2 if not present locally.
    # We keep this local to avoid introducing a new module.
    def _download_translation_json_from_b2(paper_id: str, dest: Path) -> bool:
        try:
            import boto3
            from botocore.exceptions import ClientError
        except Exception as e:
            log(f"    B2 download unavailable (missing deps): {e}")
            return False

        endpoint = os.environ.get("BACKBLAZE_S3_ENDPOINT") or os.environ.get("B2_S3_ENDPOINT")
        bucket = os.environ.get("BACKBLAZE_BUCKET") or os.environ.get("B2_BUCKET")
        prefix = os.environ.get("BACKBLAZE_PREFIX") or os.environ.get("B2_PREFIX") or ""

        key_id = (
            os.environ.get("BACKBLAZE_KEY_ID")
            or os.environ.get("B2_KEY_ID")
            or os.environ.get("AWS_ACCESS_KEY_ID")
        )
        secret = (
            os.environ.get("BACKBLAZE_APPLICATION_KEY")
            or os.environ.get("B2_APPLICATION_KEY")
            or os.environ.get("AWS_SECRET_ACCESS_KEY")
        )

        if not endpoint or not bucket or not key_id or not secret:
            return False

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
        )

        remote_key = f"{prefix}validated/translations/{paper_id}.json"
        try:
            obj = s3.get_object(Bucket=bucket, Key=remote_key)
            raw = obj["Body"].read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            log(f"    Downloaded translation JSON from B2: {remote_key}")
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                return False
            log(f"    Warning: B2 download failed: {e}")
            return False
        except Exception as e:
            log(f"    Warning: B2 download failed: {e}")
            return False

    # Import the actual function
    try:
        from scripts.generate_english_pdfs import (
            generate_pdf_for_paper,
            check_pdf_tools,
            get_s3_client,
            get_figure_manifest,
        )
    except ImportError as e:
        log(f"    PDF generation not available: {e}")
        return False

    # Check PDF tools (pandoc + xelatex)
    pdf_engine = check_pdf_tools()
    if not pdf_engine:
        log("    PDF tools not available (need pandoc + xelatex)")
        return False

    # Load the translation JSON
    translation_path = Path(f"data/translated/{paper_id}.json")
    if not translation_path.exists():
        # Many papers already have text complete in Postgres/B2 but no local JSON.
        # For PDF generation we need the per-paper translation JSON; fetch it from
        # B2 validated translations when possible.
        if not _download_translation_json_from_b2(paper_id, translation_path):
            log(f"    Translation not found: {translation_path}")
            return False

    try:
        with open(translation_path, 'r', encoding='utf-8') as f:
            paper = json.load(f)
    except Exception as e:
        log(f"    Failed to load translation: {e}")
        return False

    # Get figure manifest from B2
    try:
        s3 = get_s3_client()
        figure_manifest = get_figure_manifest(s3) if s3 else {}
    except Exception as e:
        log(f"    Warning: Could not fetch figure manifest: {e}")
        figure_manifest = {}

    # Ensure output directory exists
    output_dir = Path("data/english_pdfs")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        success, figure_count = generate_pdf_for_paper(
            paper=paper,
            figure_manifest=figure_manifest,
            output_dir=output_dir,
            pdf_engine=pdf_engine,
        )
        if success:
            log(f"    English PDF generated ({figure_count} figures)")
            return True
        return False
    except Exception as e:
        log(f"    PDF generation failed: {e}")
        raise


def run_post_processing(paper_id: str, dry_run: bool = False) -> bool:
    """
    Run post-processing: upload all outputs to B2.

    Uploads:
    - Translation JSON (validated or flagged)
    - English PDF (if exists)
    - Figures (if directory exists)
    - Paper pointer JSON

    Returns:
        True if post-processing succeeded, False otherwise
    """
    from .tools.b2_publish import (
        upload_translation,
        upload_english_pdf,
        upload_figures,
        upload_flagged,
        upload_qa_report,
        create_paper_pointer,
    )

    log(f"  Post-processing {paper_id}...")

    if dry_run:
        log("    [DRY RUN] Would upload to B2")
        return True

    uploaded_count = 0

    # 1. Upload translation JSON (validated or flagged)
    translation_path = f"data/translated/{paper_id}.json"
    flagged_path = f"data/flagged/{paper_id}.json"

    if os.path.exists(translation_path):
        if upload_translation(paper_id, translation_path):
            log("    Uploaded translation to B2")
            uploaded_count += 1
        else:
            log("    WARNING: Translation upload failed")
    elif os.path.exists(flagged_path):
        if upload_flagged(paper_id, flagged_path):
            log("    Uploaded flagged translation to B2")
            uploaded_count += 1
        else:
            log("    WARNING: Flagged upload failed")

    # 2. Upload English PDF (if exists)
    english_pdf_path = f"data/english_pdfs/{paper_id}.pdf"
    if os.path.exists(english_pdf_path):
        if upload_english_pdf(paper_id, english_pdf_path):
            log("    Uploaded English PDF to B2")
            uploaded_count += 1
        else:
            log("    WARNING: English PDF upload failed")

    # 3. Upload figures (if directory exists)
    figures_dir = f"data/figures/{paper_id}"
    if os.path.isdir(figures_dir):
        fig_count = upload_figures(paper_id, figures_dir)
        if fig_count > 0:
            log(f"    Uploaded {fig_count} figures to B2")
            uploaded_count += fig_count
        else:
            log("    No figures uploaded (empty or failed)")

    # 4. Create pointer JSON
    if create_paper_pointer(paper_id):
        log("    Created paper pointer in B2")
    else:
        log("    WARNING: Pointer creation failed")

    # 5. Upload QA report (if present)
    qa_report_path = f"reports/qa_results/{paper_id}.json"
    if os.path.exists(qa_report_path):
        remote_key = upload_qa_report(paper_id, qa_report_path)
        if remote_key:
            log(f"    Uploaded QA report to B2: {remote_key}")
            uploaded_count += 1
        else:
            log("    WARNING: QA report upload failed")

    # POST stage succeeds if we uploaded something OR there was nothing to upload
    # (empty uploaded_count with no files is fine - translation may be DB-only)
    # Fail if we had files but couldn't upload them
    has_local_files = (
        os.path.exists(f"data/translated/{paper_id}.json") or
        os.path.exists(f"data/flagged/{paper_id}.json") or
        os.path.exists(f"data/english_pdfs/{paper_id}.pdf") or
        os.path.isdir(f"data/figures/{paper_id}")
    )

    if has_local_files and uploaded_count == 0:
        log("    ERROR: Had files to upload but uploaded_count=0")
        return False

    return True


# ============================================================================
# Main Processing Logic
# ============================================================================

def process_paper(
    paper_id: str,
    stages: list[str],
    dry_run: bool = False,
    notify: Optional[Callable] = None
) -> ProcessingResult:
    """
    Process a single paper through the pipeline stages.

    Args:
        paper_id: Paper identifier
        stages: List of stages to run (harvest, text, figures, pdf, post)
        dry_run: If True, skip actual processing
        notify: Optional callback for alerts

    Returns:
        ProcessingResult with status and any errors
    """
    result = ProcessingResult(paper_id=paper_id, status='success')
    conn = None

    try:
        conn = get_db_connection()

        # Try to acquire lock
        if not acquire_paper_lock(conn, paper_id):
            log(f"SKIP {paper_id} - already being processed")
            result.status = 'skipped'
            return result

        # Get current status to check what's already done
        status = get_paper_status(conn, paper_id)

        # Run stages
        for stage in stages:
            try:
                if stage == 'harvest':
                    if not run_harvest(paper_id, dry_run=dry_run):
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
                    try:
                        if run_figure_translation(paper_id, dry_run=dry_run):
                            update_stage_status(conn, paper_id, 'figures', 'complete')
                            result.stages_completed.append('figures')
                        else:
                            # Figure translation is optional - don't fail the paper
                            update_stage_status(conn, paper_id, 'figures', 'failed')
                            log(f"    Figure translation failed for {paper_id} (non-blocking)")
                    except Exception as fig_e:
                        # Catch Gemini quota errors, etc. - don't block the paper
                        update_stage_status(conn, paper_id, 'figures', 'failed')
                        log(f"    Figure translation error: {fig_e} (non-blocking)")

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
                        # PDF generation should be retryable; do not mark as skipped.
                        update_stage_status(conn, paper_id, 'pdf', 'failed')
                        log(f"    PDF generation failed for {paper_id} (will retry)")

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

        # All stages completed
        mark_paper_complete(conn, paper_id)
        log(f"Completed {paper_id}: stages={result.stages_completed}")
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
        include_failed: Include all failed papers (not just old ones)

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

            # Helpful warning if no papers found for month
            if not papers:
                log("")
                log("=" * 60)
                log("WARNING: No papers found for this month!")
                log("")
                log("Papers must be imported to the database before processing.")
                log("The database is the source of truth for the pipeline.")
                log("")
                log("To import papers, run one of:")
                log(f"  python scripts/import_to_postgres.py --month {target}")
                log("  python -m src.harvest_chinaxiv_optimized --start YYYYMM --end YYYYMM")
                log("=" * 60)
                log("")

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
            log(f"Found {len(papers)} papers needing work")

        else:
            raise ValueError(f"Unknown scope: {scope}")

        # Filter by what needs doing (unless force)
        if force:
            log(f"Force mode: processing all {len(papers)} papers")
            return papers

        # Filter out already-complete papers
        work_queue = []
        for paper_id in papers:
            status = get_paper_status(conn, paper_id)

            if not status:
                # Paper not in DB - add to queue
                work_queue.append(paper_id)
                continue

            if status.get('processing_status') == 'complete':
                # Check if specific stages are requested and incomplete
                if text_only and (
                    status.get('text_status') != 'complete'
                    or status.get('pdf_status') != 'complete'
                ) or figures_only and status.get('figures_status') != 'complete':
                    work_queue.append(paper_id)
                # Otherwise skip complete papers
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
        scope: Scope type (month, list, file, smart-resume, discover)
        target: Target value for scope
        force: Force reprocessing of complete papers
        dry_run: Skip actual processing
        workers: Number of parallel workers
        text_only: Skip figure translation (runs text + English PDF)
        figures_only: Only run figure translation stage
        include_failed: Include all failed papers (not just old ones)

    Returns:
        OrchestratorStats with results
    """
    stats = OrchestratorStats()

    # Handle discover scope - DISCOVERY ONLY, no translation
    if scope == 'discover':
        if not target or len(target) != 6:
            raise ValueError("discover scope requires --target YYYYMM (e.g., 202501)")

        try:
            new_ids = run_discover(target, dry_run=dry_run)
            stats.total = len(new_ids)
            stats.success = len(new_ids)

            log("")
            log("=" * 50)
            log("DISCOVERY COMPLETE")
            log("=" * 50)
            log(f"Month:      {target}")
            log(f"New papers: {len(new_ids)}")
            log("")
            log("To translate these papers, run:")
            log(f"  python -m src.orchestrator --scope month --target {target}")
            log("")

            return stats

        except Exception as e:
            log(f"ERROR during discovery: {e}")
            stats.errors.append(str(e))
            return stats

    # Determine stages based on flags
    if text_only:
        # Skip figure translation to conserve spend, but still generate English PDFs.
        stages = ['harvest', 'text', 'pdf', 'post']
        log("Running TEXT+PDF mode (skip figures)")
    elif figures_only:
        stages = ['figures', 'post']
        log("Running FIGURES ONLY mode")
    else:
        stages = DEFAULT_STAGES
        log("Running FULL PIPELINE mode")

    # Reconcile any papers with inconsistent qa_status
    # (text complete but qa_status != 'pass')
    try:
        conn = get_db_connection()
        reconciled = reconcile_qa_status(conn)
        if reconciled > 0:
            log(f"Fixed {reconciled} papers with inconsistent qa_status")
        conn.close()
    except Exception as e:
        log(f"Warning: qa_status reconciliation failed: {e}")

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
        with contextlib.suppress(Exception):
            alert_critical("Pipeline Error", message, source="orchestrator")

    # Process papers
    log(f"Processing {len(work_queue)} papers with {workers} workers...")

    if workers == 1:
        # Sequential processing
        for paper_id in work_queue:
            result = process_paper(paper_id, stages, dry_run=dry_run, notify=notify)
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
                executor.submit(process_paper, pid, stages, dry_run, notify): pid
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
        choices=['month', 'list', 'file', 'smart-resume', 'discover'],
        help='What to process (discover = find new papers, others = translate existing)'
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
        help='[Admin] Skip figure translation (run text + English PDF)'
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
        help='Include all failed papers (not just old ones). By default, only '
             'failed papers older than 7 days are auto-retried.'
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
