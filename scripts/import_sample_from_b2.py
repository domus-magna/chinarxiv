#!/usr/bin/env python3
"""
Import sample papers from B2 to PostgreSQL for testing.

This script downloads 20 sample papers from B2 and imports them into
the local PostgreSQL database for testing purposes.

Usage:
    export DATABASE_URL="postgresql://postgres:password@localhost:5432/chinaxiv_dev"
    python scripts/import_sample_from_b2.py
"""

import json
import os
import sys
import boto3
from botocore.exceptions import ClientError
import psycopg2
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_s3_client():
    """Create S3 client configured for Backblaze B2."""
    return boto3.client(
        's3',
        endpoint_url=os.environ['BACKBLAZE_S3_ENDPOINT'],
        aws_access_key_id=os.environ['BACKBLAZE_KEY_ID'],
        aws_secret_access_key=os.environ['BACKBLAZE_APPLICATION_KEY']
    )


def list_b2_papers(s3_client, limit=20):
    """List available papers in B2 validated translations."""
    logger.info(f"Listing first {limit} papers from B2...")

    try:
        response = s3_client.list_objects_v2(
            Bucket='chinaxiv',
            Prefix='validated/translations/',
            MaxKeys=limit
        )

        paper_ids = []
        for obj in response.get('Contents', []):
            key = obj['Key']
            # Extract paper ID from key like "validated/translations/chinaxiv-202201.00001.json"
            if key.endswith('.json'):
                filename = key.split('/')[-1]
                paper_id = filename.replace('.json', '')
                paper_ids.append(paper_id)

        logger.info(f"Found {len(paper_ids)} papers")
        return paper_ids

    except ClientError as e:
        logger.error(f"Failed to list papers from B2: {e}")
        return []


def download_paper_from_b2(s3_client, paper_id, local_dir):
    """Download a single paper JSON from B2."""
    local_path = local_dir / f"{paper_id}.json"

    if local_path.exists():
        logger.info(f"  {paper_id}: Already downloaded, skipping")
        return local_path

    key = f"validated/translations/{paper_id}.json"

    try:
        s3_client.download_file('chinaxiv', key, str(local_path))
        logger.info(f"  {paper_id}: Downloaded")
        return local_path
    except ClientError as e:
        logger.error(f"  {paper_id}: Download failed - {e}")
        return None


def import_paper_to_postgres(paper_json_path, db_conn):
    """Import a single paper from JSON into PostgreSQL."""
    with open(paper_json_path) as f:
        paper = json.load(f)

    cursor = db_conn.cursor()

    try:
        # Insert paper
        # NOTE: These are from validated/translations/ so they're already translated
        cursor.execute("""
            INSERT INTO papers (
                id, title_en, abstract_en, creators_en, date,
                has_figures, has_full_text, qa_status,
                source_url, pdf_url, created_at,
                text_status, body_md
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            paper['id'],
            paper.get('title_en', ''),
            paper.get('abstract_en', ''),
            json.dumps(paper.get('creators_en', [])),
            paper.get('date'),
            paper.get('has_figures', False),
            paper.get('has_full_text', False),
            'pass',  # All validated papers have qa_status='pass'
            paper.get('source_url'),
            paper.get('pdf_url'),
            'complete',  # Validated papers are already translated
            paper.get('body_md', '')  # Include translated body content
        ))

        # Insert subjects (use English subjects)
        for subject in paper.get('subjects_en', []):
            cursor.execute("""
                INSERT INTO paper_subjects (paper_id, subject)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (paper['id'], subject))

        db_conn.commit()
        logger.info(f"  {paper['id']}: Imported to PostgreSQL")
        return True

    except Exception as e:
        logger.error(f"  {paper['id']}: Import failed - {e}")
        db_conn.rollback()
        return False


def refresh_materialized_view(db_conn):
    """Refresh the category_counts materialized view."""
    logger.info("Refreshing materialized view...")
    cursor = db_conn.cursor()
    cursor.execute("REFRESH MATERIALIZED VIEW category_counts;")
    db_conn.commit()
    logger.info("Materialized view refreshed")


def main():
    # Check environment variables
    required_vars = ['DATABASE_URL', 'BACKBLAZE_S3_ENDPOINT', 'BACKBLAZE_KEY_ID', 'BACKBLAZE_APPLICATION_KEY']
    missing = [var for var in required_vars if not os.environ.get(var)]
    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)}")
        logger.error("Make sure to source .env or export required variables")
        sys.exit(1)

    # Create local directory for downloads
    local_dir = Path('data/translations/sample')
    local_dir.mkdir(parents=True, exist_ok=True)

    # Create S3 client
    logger.info("Creating S3 client for Backblaze B2...")
    s3_client = get_s3_client()

    # Connect to PostgreSQL
    logger.info("Connecting to PostgreSQL...")
    db_conn = psycopg2.connect(os.environ['DATABASE_URL'])
    logger.info("Connected to PostgreSQL")

    try:
        # List papers from B2
        paper_ids = list_b2_papers(s3_client, limit=20)

        if not paper_ids:
            logger.error("No papers found in B2")
            sys.exit(1)

        # Download and import each paper
        logger.info(f"Downloading and importing {len(paper_ids)} papers...")
        success_count = 0

        for paper_id in paper_ids:
            # Download
            local_path = download_paper_from_b2(s3_client, paper_id, local_dir)
            if not local_path:
                continue

            # Import
            if import_paper_to_postgres(local_path, db_conn):
                success_count += 1

        logger.info(f"\n✅ Import complete: {success_count}/{len(paper_ids)} papers imported")

        # Refresh materialized view
        if success_count > 0:
            refresh_materialized_view(db_conn)

        # Show summary
        cursor = db_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM papers;")
        total_papers = cursor.fetchone()[0]
        logger.info(f"Total papers in database: {total_papers}")

    finally:
        db_conn.close()
        logger.info("Database connection closed")


if __name__ == '__main__':
    main()
