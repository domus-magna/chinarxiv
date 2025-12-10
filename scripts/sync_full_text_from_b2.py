#!/usr/bin/env python3
"""
Sync full text content (has_full_text and body_md) from B2 translations to PostgreSQL.

This script:
1. Scans validated translations in B2
2. For each translation with _has_full_body=True, updates the database:
   - Sets has_full_text = TRUE
   - Populates body_md with the markdown content

Usage:
    # Dry run (no changes)
    python scripts/sync_full_text_from_b2.py --dry-run

    # Sync to production
    DATABASE_URL="postgresql://..." python scripts/sync_full_text_from_b2.py

Environment variables:
    DATABASE_URL - PostgreSQL connection string
    BACKBLAZE_KEY_ID - B2 access key
    BACKBLAZE_APPLICATION_KEY - B2 secret key
    BACKBLAZE_S3_ENDPOINT - B2 S3 endpoint
    BACKBLAZE_BUCKET - B2 bucket name (default: chinaxiv)
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

import boto3
import psycopg2

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def get_s3_client():
    """Create B2 S3 client."""
    return boto3.client(
        's3',
        endpoint_url=os.environ.get('BACKBLAZE_S3_ENDPOINT'),
        aws_access_key_id=os.environ.get('BACKBLAZE_KEY_ID'),
        aws_secret_access_key=os.environ.get('BACKBLAZE_APPLICATION_KEY')
    )


def get_db_connection():
    """Create PostgreSQL connection."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    return psycopg2.connect(database_url)


def ensure_body_md_column(conn):
    """Add body_md column if it doesn't exist."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'papers' AND column_name = 'body_md'
    """)
    if not cursor.fetchone():
        logger.info("Adding body_md column to papers table...")
        cursor.execute("ALTER TABLE papers ADD COLUMN body_md TEXT;")
        conn.commit()
        logger.info("Column added successfully")
    else:
        logger.info("body_md column already exists")


def get_bucket():
    """Get B2 bucket name from environment."""
    return os.environ.get('BACKBLAZE_BUCKET', 'chinaxiv')


def list_translations(s3):
    """List all translation files in B2."""
    paginator = s3.get_paginator('list_objects_v2')
    translations = []
    bucket = get_bucket()

    for page in paginator.paginate(Bucket=bucket, Prefix='validated/translations/'):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.endswith('.json'):
                paper_id = key.split('/')[-1].replace('.json', '')
                translations.append((paper_id, key))

    return translations


def get_translation(s3, key):
    """Download and parse a translation JSON from B2."""
    bucket = get_bucket()
    obj = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj['Body'].read().decode('utf-8'))


def sync_paper(conn, paper_id, has_full_text, body_md, dry_run=False):
    """Update a paper's full text fields in the database."""
    if dry_run:
        logger.info(f"  Would update {paper_id}: has_full_text={has_full_text}, body_md={len(body_md) if body_md else 0} chars")
        return True

    cursor = conn.cursor()
    cursor.execute("""
        UPDATE papers
        SET has_full_text = %s, body_md = %s
        WHERE id = %s
    """, (has_full_text, body_md, paper_id))

    if cursor.rowcount == 0:
        logger.warning(f"  {paper_id}: Not found in database (skipped)")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(description='Sync full text from B2 to PostgreSQL')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    parser.add_argument('--limit', type=int, help='Limit number of papers to process')
    args = parser.parse_args()

    # Validate environment
    required_vars = ['BACKBLAZE_KEY_ID', 'BACKBLAZE_APPLICATION_KEY', 'BACKBLAZE_S3_ENDPOINT']
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    if not args.dry_run and not os.environ.get('DATABASE_URL'):
        logger.error("DATABASE_URL is required (use --dry-run to preview without database)")
        sys.exit(1)

    logger.info("Starting full text sync from B2...")
    if args.dry_run:
        logger.info("DRY RUN - no changes will be made")

    s3 = get_s3_client()

    # Get database connection (only if not dry run)
    conn = None
    if not args.dry_run:
        conn = get_db_connection()
        ensure_body_md_column(conn)

    # List all translations
    logger.info("Listing translations in B2...")
    translations = list_translations(s3)
    logger.info(f"Found {len(translations)} translations")

    if args.limit:
        translations = translations[:args.limit]
        logger.info(f"Limited to {args.limit} papers")

    # Process each translation
    updated = 0
    skipped = 0
    with_full_text = 0

    for i, (paper_id, key) in enumerate(translations, 1):
        if i % 50 == 0:
            logger.info(f"Progress: {i}/{len(translations)} ({i*100//len(translations)}%)")

        try:
            data = get_translation(s3, key)

            # Check if has full text
            has_full_body = data.get('_has_full_body', False)
            body_md = data.get('body_md', '')

            # Also check body_md length as fallback
            if not has_full_body and body_md and len(body_md) > 500:
                has_full_body = True

            if has_full_body:
                with_full_text += 1
                if sync_paper(conn, paper_id, True, body_md, dry_run=args.dry_run):
                    updated += 1
            else:
                # Still sync to set has_full_text=False explicitly
                if sync_paper(conn, paper_id, False, '', dry_run=args.dry_run):
                    skipped += 1

        except Exception as e:
            logger.error(f"  {paper_id}: Error - {e}")

    # Commit if not dry run
    if conn and not args.dry_run:
        conn.commit()

    # Summary
    logger.info("")
    logger.info("=" * 50)
    logger.info(f"Sync complete!")
    logger.info(f"  Total translations: {len(translations)}")
    logger.info(f"  With full text: {with_full_text}")
    logger.info(f"  Updated: {updated}")
    logger.info(f"  Skipped (abstract only): {skipped}")

    # Show DB stats if connected
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), COUNT(CASE WHEN has_full_text THEN 1 END), COUNT(body_md) FROM papers")
        total, full_text_count, body_md_count = cursor.fetchone()
        logger.info("")
        logger.info(f"Database stats:")
        logger.info(f"  Total papers: {total}")
        logger.info(f"  With has_full_text=TRUE: {full_text_count}")
        logger.info(f"  With body_md: {body_md_count}")
        conn.close()


if __name__ == '__main__':
    main()
