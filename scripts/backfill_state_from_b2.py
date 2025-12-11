#!/usr/bin/env python3
"""
Backfill processing status columns from B2 storage.

This script scans B2 to determine what artifacts exist for each paper,
then updates the PostgreSQL database to reflect the current state.

Run this AFTER applying the 001_add_processing_status migration.

Usage:
    python scripts/backfill_state_from_b2.py
    python scripts/backfill_state_from_b2.py --dry-run
    python scripts/backfill_state_from_b2.py --limit 100

Environment:
    DATABASE_URL: PostgreSQL connection string (required)
    AWS_ACCESS_KEY_ID / BACKBLAZE_KEY_ID: B2 access key
    AWS_SECRET_ACCESS_KEY / BACKBLAZE_APPLICATION_KEY: B2 secret key
    BACKBLAZE_S3_ENDPOINT: B2 S3 endpoint
    BACKBLAZE_BUCKET: B2 bucket name
    BACKBLAZE_PREFIX: B2 key prefix (optional)
"""

import os
import sys
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import boto3
from botocore.config import Config
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_b2_client():
    """Create B2 S3 client."""
    key_id = os.environ.get('AWS_ACCESS_KEY_ID') or os.environ.get('BACKBLAZE_KEY_ID')
    secret = os.environ.get('AWS_SECRET_ACCESS_KEY') or os.environ.get('BACKBLAZE_APPLICATION_KEY')
    endpoint = os.environ.get('BACKBLAZE_S3_ENDPOINT')

    if not all([key_id, secret, endpoint]):
        logger.error("B2 credentials not set. Need: BACKBLAZE_KEY_ID, BACKBLAZE_APPLICATION_KEY, BACKBLAZE_S3_ENDPOINT")
        sys.exit(1)

    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(signature_version='s3v4', region_name='us-west-004')
    )


def list_b2_prefixes(s3_client, bucket: str, prefix: str, folder: str) -> set:
    """List all paper IDs that have files in a B2 folder."""
    paper_ids = set()
    full_prefix = f"{prefix}{folder}/" if prefix else f"{folder}/"

    paginator = s3_client.get_paginator('list_objects_v2')

    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=full_prefix):
            for obj in page.get('Contents', []):
                # Extract paper ID from key
                # e.g., "pdfs/chinaxiv-202401.00001.pdf" -> "chinaxiv-202401.00001"
                key = obj['Key']
                filename = key.split('/')[-1]

                # Handle different file patterns
                if filename.endswith('.pdf'):
                    paper_id = filename[:-4]  # Remove .pdf
                elif filename.endswith('.json'):
                    paper_id = filename[:-5]  # Remove .json
                else:
                    continue

                # Validate paper ID format
                if paper_id.startswith('chinaxiv-'):
                    paper_ids.add(paper_id)

    except Exception as e:
        logger.error(f"Error listing B2 {folder}: {e}")

    return paper_ids


def list_figure_papers(s3_client, bucket: str, prefix: str) -> set:
    """List paper IDs that have translated figures in B2."""
    paper_ids = set()
    full_prefix = f"{prefix}figures/" if prefix else "figures/"

    paginator = s3_client.get_paginator('list_objects_v2')

    try:
        # List at the "figures/{paper_id}/" level
        for page in paginator.paginate(Bucket=bucket, Prefix=full_prefix, Delimiter='/'):
            for common_prefix in page.get('CommonPrefixes', []):
                # e.g., "figures/chinaxiv-202401.00001/" -> "chinaxiv-202401.00001"
                folder = common_prefix['Prefix'].rstrip('/').split('/')[-1]
                if folder.startswith('chinaxiv-'):
                    paper_ids.add(folder)

    except Exception as e:
        logger.error(f"Error listing B2 figures: {e}")

    return paper_ids


def scan_b2_state(s3_client, bucket: str, prefix: str) -> dict:
    """Scan B2 to determine what exists for each paper.

    Raises:
        RuntimeError: If any B2 scan fails (to prevent incorrect status updates)
    """
    logger.info("Scanning B2 storage...")

    # Scan each folder in parallel
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(list_b2_prefixes, s3_client, bucket, prefix, 'pdfs'): 'chinese_pdfs',
            executor.submit(list_b2_prefixes, s3_client, bucket, prefix, 'validated/translations'): 'text_translations',
            executor.submit(list_b2_prefixes, s3_client, bucket, prefix, 'english_pdfs'): 'english_pdfs',
            executor.submit(list_figure_papers, s3_client, bucket, prefix): 'figures',
        }

        results = {}
        errors = []
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
                logger.info(f"  Found {len(results[key])} papers with {key}")
            except Exception as e:
                logger.error(f"  FATAL: Error scanning {key}: {e}")
                errors.append(f"{key}: {e}")

        # Fail-fast if any scan failed to prevent incorrect status updates
        if errors:
            raise RuntimeError(
                f"B2 scan failed for: {', '.join(errors)}. "
                "Cannot proceed with partial data - would incorrectly mark translated papers as pending."
            )

    return results


def backfill_database(conn, b2_state: dict, dry_run: bool = False, limit: Optional[int] = None):
    """Update database with processing status from B2 state."""
    cursor = conn.cursor()

    # Get all paper IDs and current status from database
    if limit:
        cursor.execute("""
            SELECT id, text_status, figures_status, pdf_status
            FROM papers ORDER BY id LIMIT %s
        """, (limit,))
    else:
        cursor.execute("""
            SELECT id, text_status, figures_status, pdf_status
            FROM papers ORDER BY id
        """)

    db_papers = cursor.fetchall()
    logger.info(f"Processing {len(db_papers)} papers from database")

    # Prepare updates
    updates = []
    for row in db_papers:
        paper_id = row['id']
        current_text_status = row.get('text_status')
        current_figures_status = row.get('figures_status')
        current_pdf_status = row.get('pdf_status')

        has_chinese_pdf = paper_id in b2_state['chinese_pdfs']
        has_text = paper_id in b2_state['text_translations']
        has_figures = paper_id in b2_state['figures']
        has_english_pdf = paper_id in b2_state['english_pdfs']

        # Determine status based on what exists in B2
        # BUT preserve 'failed' status if no B2 translation (don't reset to pending)
        if has_text:
            text_status = 'complete'
        elif current_text_status == 'failed':
            text_status = 'failed'  # Preserve failed status
        else:
            text_status = 'pending'

        if has_figures:
            figures_status = 'complete'
        elif current_figures_status == 'failed':
            figures_status = 'failed'  # Preserve failed status
        else:
            figures_status = 'pending'

        if has_english_pdf:
            pdf_status = 'complete'
        elif current_pdf_status == 'failed':
            pdf_status = 'failed'  # Preserve failed status
        else:
            pdf_status = 'pending'

        # Overall processing status
        if has_text and has_figures and has_english_pdf:
            processing_status = 'complete'
        elif has_text or has_figures or has_english_pdf:
            processing_status = 'pending'  # Partially complete
        else:
            processing_status = 'pending'

        updates.append((
            paper_id,
            processing_status,
            text_status,
            figures_status,
            pdf_status,
            has_chinese_pdf,
            has_english_pdf
        ))

    if dry_run:
        # Show sample of updates
        logger.info("Dry run - would update the following (first 10):")
        for u in updates[:10]:
            logger.info(f"  {u[0]}: processing={u[1]}, text={u[2]}, figures={u[3]}, pdf={u[4]}")

        # Summary stats
        complete = sum(1 for u in updates if u[1] == 'complete')
        has_text = sum(1 for u in updates if u[2] == 'complete')
        has_figs = sum(1 for u in updates if u[3] == 'complete')
        has_pdf = sum(1 for u in updates if u[4] == 'complete')
        logger.info("\nSummary:")
        logger.info(f"  Fully complete: {complete}")
        logger.info(f"  Has text translation: {has_text}")
        logger.info(f"  Has figures: {has_figs}")
        logger.info(f"  Has English PDF: {has_pdf}")
        return

    # Batch update using execute_values
    logger.info("Updating database...")

    # Create temp table for bulk update
    cursor.execute("""
        CREATE TEMP TABLE paper_status_updates (
            paper_id TEXT PRIMARY KEY,
            processing_status VARCHAR(20),
            text_status VARCHAR(20),
            figures_status VARCHAR(20),
            pdf_status VARCHAR(20),
            has_chinese_pdf BOOLEAN,
            has_english_pdf BOOLEAN
        )
    """)

    # Insert updates into temp table
    execute_values(
        cursor,
        """
        INSERT INTO paper_status_updates
            (paper_id, processing_status, text_status, figures_status, pdf_status, has_chinese_pdf, has_english_pdf)
        VALUES %s
        """,
        updates,
        page_size=1000
    )

    # Update papers table from temp table
    # IMPORTANT: Preserve existing timestamps for idempotency
    # Only set timestamp to NOW() if it was NULL (new completion)
    cursor.execute("""
        UPDATE papers p
        SET
            processing_status = u.processing_status,
            text_status = u.text_status,
            figures_status = u.figures_status,
            pdf_status = u.pdf_status,
            has_chinese_pdf = u.has_chinese_pdf,
            has_english_pdf = u.has_english_pdf,
            text_completed_at = CASE
                WHEN u.text_status = 'complete' AND p.text_completed_at IS NULL THEN NOW()
                WHEN u.text_status = 'complete' THEN p.text_completed_at
                ELSE NULL
            END,
            figures_completed_at = CASE
                WHEN u.figures_status = 'complete' AND p.figures_completed_at IS NULL THEN NOW()
                WHEN u.figures_status = 'complete' THEN p.figures_completed_at
                ELSE NULL
            END,
            pdf_completed_at = CASE
                WHEN u.pdf_status = 'complete' AND p.pdf_completed_at IS NULL THEN NOW()
                WHEN u.pdf_status = 'complete' THEN p.pdf_completed_at
                ELSE NULL
            END
        FROM paper_status_updates u
        WHERE p.id = u.paper_id
    """)

    updated = cursor.rowcount
    conn.commit()

    logger.info(f"Updated {updated} papers")

    # Summary stats
    cursor.execute("""
        SELECT
            COUNT(*) FILTER (WHERE processing_status = 'complete') as complete,
            COUNT(*) FILTER (WHERE text_status = 'complete') as has_text,
            COUNT(*) FILTER (WHERE figures_status = 'complete') as has_figures,
            COUNT(*) FILTER (WHERE pdf_status = 'complete') as has_pdf,
            COUNT(*) as total
        FROM papers
    """)
    stats = cursor.fetchone()
    logger.info("\nDatabase state after backfill:")
    logger.info(f"  Fully complete: {stats['complete']}/{stats['total']}")
    logger.info(f"  Has text translation: {stats['has_text']}")
    logger.info(f"  Has figures: {stats['has_figures']}")
    logger.info(f"  Has English PDF: {stats['has_pdf']}")


def main():
    parser = argparse.ArgumentParser(description='Backfill processing status from B2')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('--limit', type=int,
                        help='Limit number of papers to process')
    args = parser.parse_args()

    # Check for DATABASE_URL
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable not set")
        sys.exit(1)

    # Check for B2 credentials
    bucket = os.environ.get('BACKBLAZE_BUCKET')
    prefix = os.environ.get('BACKBLAZE_PREFIX', '')

    if not bucket:
        logger.error("BACKBLAZE_BUCKET environment variable not set")
        sys.exit(1)

    # Connect to PostgreSQL
    try:
        conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        logger.info("Connected to PostgreSQL")
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

    # Verify migration was applied
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'papers' AND column_name = 'processing_status'")
        if not cursor.fetchone():
            logger.error("Migration 001_add_processing_status not applied. Run scripts/migrations/apply.py first")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error checking schema: {e}")
        sys.exit(1)

    # Create B2 client and scan storage
    s3_client = get_b2_client()
    b2_state = scan_b2_state(s3_client, bucket, prefix)

    # Backfill database
    backfill_database(conn, b2_state, dry_run=args.dry_run, limit=args.limit)

    conn.close()
    logger.info("Done!")


if __name__ == '__main__':
    main()
