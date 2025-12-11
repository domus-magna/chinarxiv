#!/usr/bin/env python3
"""
Backfill Chinese metadata columns from existing data.

This script populates the _cn columns (title_cn, abstract_cn, creators_cn, subjects_cn)
for existing papers in the database.

Two data sources:
1. Papers with text_status='pending' - Chinese is in _en columns (migrate)
2. B2 records - Chinese metadata for completed papers (download and populate)

Usage:
    # Backfill from both sources
    python scripts/backfill_chinese.py

    # Only migrate pending papers (no B2 download)
    python scripts/backfill_chinese.py --pending-only

    # Only backfill from B2 records
    python scripts/backfill_chinese.py --b2-only

    # Dry run (show what would be done)
    python scripts/backfill_chinese.py --dry-run

Environment:
    DATABASE_URL: PostgreSQL connection string (required)
    BACKBLAZE_*: B2 credentials (for B2 backfill)
"""

import argparse
import json
import os
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor


def get_db_connection():
    """Get PostgreSQL connection."""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def migrate_pending_papers(conn, dry_run: bool = False) -> int:
    """
    Migrate Chinese from _en columns to _cn columns for pending papers.

    Papers with text_status='pending' have Chinese metadata in _en columns
    (legacy behavior). Copy to _cn columns and clear _en columns.

    Returns:
        Number of papers migrated
    """
    cursor = conn.cursor()

    # Find papers with Chinese in _en columns
    cursor.execute("""
        SELECT id, title_en, abstract_en, creators_en
        FROM papers
        WHERE text_status = 'pending'
          AND title_cn IS NULL
          AND title_en IS NOT NULL
          AND title_en != ''
    """)

    papers = cursor.fetchall()
    print(f"Found {len(papers)} pending papers to migrate")

    if dry_run:
        print("[DRY RUN] Would migrate these papers:")
        for p in papers[:5]:
            print(f"  - {p['id']}: {p['title_en'][:50]}...")
        if len(papers) > 5:
            print(f"  ... and {len(papers) - 5} more")
        return len(papers)

    migrated = 0
    for paper in papers:
        try:
            # First, get subjects from paper_subjects table to copy to subjects_cn
            cursor.execute("""
                SELECT subject FROM paper_subjects WHERE paper_id = %s
            """, (paper['id'],))
            subjects = [row['subject'] for row in cursor.fetchall()]
            subjects_json = json.dumps(subjects) if subjects else None

            cursor.execute("""
                UPDATE papers SET
                    title_cn = title_en,
                    abstract_cn = abstract_en,
                    creators_cn = creators_en,
                    subjects_cn = %s,
                    title_en = NULL,
                    abstract_en = NULL,
                    creators_en = NULL
                WHERE id = %s
            """, (subjects_json, paper['id']))
            migrated += 1

            if migrated % 100 == 0:
                conn.commit()
                print(f"  Migrated {migrated}/{len(papers)} papers...")

        except Exception as e:
            conn.rollback()
            print(f"  ERROR migrating {paper['id']}: {e}")

    conn.commit()
    print(f"Migrated {migrated} papers")
    return migrated


def download_b2_records(month: str) -> Optional[List[Dict]]:
    """
    Download records file from B2 for a month.

    Args:
        month: Month string (YYYYMM)

    Returns:
        List of paper records, or None if download failed
    """
    import boto3
    from botocore.exceptions import ClientError

    bucket = os.environ.get('BACKBLAZE_BUCKET')
    endpoint = os.environ.get('BACKBLAZE_S3_ENDPOINT')
    key_id = os.environ.get('BACKBLAZE_KEY_ID') or os.environ.get('AWS_ACCESS_KEY_ID')
    secret = os.environ.get('BACKBLAZE_APPLICATION_KEY') or os.environ.get('AWS_SECRET_ACCESS_KEY')

    if not all([bucket, endpoint, key_id, secret]):
        print("  WARNING: B2 credentials not configured")
        return None

    try:
        s3 = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
        )

        key = f"records/chinaxiv_{month}.json"
        response = s3.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)

    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            print(f"  Records file not found: {key}")
            return None
        raise
    except Exception as e:
        print(f"  ERROR downloading records: {e}")
        return None


def backfill_from_b2(conn, months: List[str], dry_run: bool = False) -> int:
    """
    Backfill Chinese metadata from B2 records.

    Downloads records files from B2 and populates _cn columns for papers
    that are missing Chinese metadata.

    Args:
        conn: Database connection
        months: List of months to backfill (YYYYMM format)
        dry_run: If True, don't write to database

    Returns:
        Number of papers updated
    """
    cursor = conn.cursor()
    total_updated = 0

    for month in months:
        print(f"\nProcessing month {month}...")

        # Download records from B2
        records = download_b2_records(month)
        if not records:
            print(f"  Skipping {month} - no records")
            continue

        print(f"  Downloaded {len(records)} records from B2")

        # Find papers in DB that need Chinese metadata
        pattern = f'chinaxiv-{month}.%'
        cursor.execute("""
            SELECT id FROM papers
            WHERE id LIKE %s
              AND title_cn IS NULL
        """, (pattern,))
        papers_needing_cn = {row['id'] for row in cursor.fetchall()}

        print(f"  Found {len(papers_needing_cn)} papers needing Chinese metadata")

        if dry_run:
            matched = sum(1 for r in records if r['id'] in papers_needing_cn)
            print(f"  [DRY RUN] Would update {matched} papers")
            total_updated += matched
            continue

        # Update papers from B2 records
        updated = 0
        for record in records:
            paper_id = record.get('id')
            if paper_id not in papers_needing_cn:
                continue

            try:
                # Prepare JSONB values
                creators = record.get('creators', [])
                if isinstance(creators, str):
                    creators = json.loads(creators) if creators else []
                creators_json = json.dumps(creators) if creators else None

                subjects = record.get('subjects', [])
                if isinstance(subjects, str):
                    subjects = json.loads(subjects) if subjects else []
                subjects_json = json.dumps(subjects) if subjects else None

                cursor.execute("""
                    UPDATE papers SET
                        title_cn = %s,
                        abstract_cn = %s,
                        creators_cn = %s,
                        subjects_cn = %s
                    WHERE id = %s
                      AND title_cn IS NULL
                """, (
                    record.get('title', ''),
                    record.get('abstract', ''),
                    creators_json,
                    subjects_json,
                    paper_id,
                ))

                if cursor.rowcount > 0:
                    updated += 1

            except Exception as e:
                conn.rollback()
                print(f"    ERROR updating {paper_id}: {e}")

        conn.commit()
        print(f"  Updated {updated} papers")
        total_updated += updated

    return total_updated


def get_months_in_db(conn) -> List[str]:
    """Get list of unique months that have papers in the database."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT substring(id from 10 for 6) as month
        FROM papers
        WHERE id LIKE 'chinaxiv-%'
        ORDER BY month DESC
    """)
    return [row['month'] for row in cursor.fetchall()]


def main():
    parser = argparse.ArgumentParser(
        description='Backfill Chinese metadata columns'
    )
    parser.add_argument(
        '--pending-only',
        action='store_true',
        help='Only migrate pending papers (no B2 download)'
    )
    parser.add_argument(
        '--b2-only',
        action='store_true',
        help='Only backfill from B2 records (no pending migration)'
    )
    parser.add_argument(
        '--months',
        help='Comma-separated list of months to backfill (default: all in DB)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )

    args = parser.parse_args()

    if args.pending_only and args.b2_only:
        parser.error("Cannot use both --pending-only and --b2-only")

    conn = get_db_connection()
    total_migrated = 0
    total_backfilled = 0

    try:
        # Step 1: Migrate pending papers
        if not args.b2_only:
            print("=" * 50)
            print("STEP 1: Migrate pending papers")
            print("=" * 50)
            total_migrated = migrate_pending_papers(conn, dry_run=args.dry_run)

        # Step 2: Backfill from B2 records
        if not args.pending_only:
            print("\n" + "=" * 50)
            print("STEP 2: Backfill from B2 records")
            print("=" * 50)

            if args.months:
                months = [m.strip() for m in args.months.split(',')]
            else:
                months = get_months_in_db(conn)

            print(f"Processing {len(months)} months: {months[:5]}{'...' if len(months) > 5 else ''}")
            total_backfilled = backfill_from_b2(conn, months, dry_run=args.dry_run)

        # Summary
        print("\n" + "=" * 50)
        print("SUMMARY")
        print("=" * 50)
        print(f"Migrated (pending):  {total_migrated}")
        print(f"Backfilled (B2):     {total_backfilled}")
        print(f"Total updated:       {total_migrated + total_backfilled}")

        if args.dry_run:
            print("\n[DRY RUN] No changes were made")

    finally:
        conn.close()


if __name__ == '__main__':
    main()
