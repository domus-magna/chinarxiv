#!/usr/bin/env python3
"""
Add english_pdf_url column to papers table.

This migration adds a column to store the B2 URL for English PDFs.

Usage:
    # Check what would be done (dry run)
    python scripts/add_english_pdf_column.py --dry-run

    # Run migration
    DATABASE_URL="postgresql://..." python scripts/add_english_pdf_column.py

    # On Railway
    railway run python scripts/add_english_pdf_column.py
"""
import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    import psycopg2
except ImportError:
    logger.error("Missing psycopg2. Run: pip install psycopg2-binary")
    sys.exit(1)


def run_migration(conn, dry_run: bool = False):
    """Add english_pdf_url column and index."""
    cursor = conn.cursor()

    # Check if column already exists
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'papers' AND column_name = 'english_pdf_url';
    """)
    exists = cursor.fetchone() is not None

    if exists:
        logger.info("Column english_pdf_url already exists - nothing to do")
        return

    if dry_run:
        logger.info("DRY RUN: Would add column english_pdf_url TEXT to papers table")
        logger.info("DRY RUN: Would create partial index idx_papers_english_pdf")
        return

    logger.info("Adding english_pdf_url column...")
    cursor.execute("""
        ALTER TABLE papers ADD COLUMN IF NOT EXISTS english_pdf_url TEXT;
    """)

    logger.info("Creating partial index on english_pdf_url...")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_english_pdf
        ON papers(english_pdf_url)
        WHERE english_pdf_url IS NOT NULL;
    """)

    conn.commit()
    logger.info("Migration complete!")


def verify_migration(conn):
    """Verify the column was added correctly."""
    cursor = conn.cursor()

    cursor.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'papers' AND column_name = 'english_pdf_url';
    """)
    row = cursor.fetchone()

    if row:
        logger.info(f"Verified: Column {row[0]} exists with type {row[1]}")
        return True
    else:
        logger.error("Verification failed: Column not found")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Add english_pdf_url column to papers table")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually modify the database")
    args = parser.parse_args()

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable is required")
        sys.exit(1)

    print("=" * 60)
    print("Add english_pdf_url Column Migration")
    print("=" * 60)

    try:
        conn = psycopg2.connect(database_url)
        run_migration(conn, dry_run=args.dry_run)

        if not args.dry_run:
            verify_migration(conn)

        conn.close()
        logger.info("Done!")
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
