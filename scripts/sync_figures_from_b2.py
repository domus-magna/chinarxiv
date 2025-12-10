#!/usr/bin/env python3
"""
Sync has_figures column from B2 storage.

Scans the B2 `figures/` directory to find all papers with translated figures,
then updates the PostgreSQL `has_figures` column for those papers.

Usage:
    # Dry run (show what would be updated)
    python scripts/sync_figures_from_b2.py --dry-run

    # Actually update the database
    python scripts/sync_figures_from_b2.py

    # On Railway
    railway run python scripts/sync_figures_from_b2.py
"""
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import boto3
    import psycopg2
    from dotenv import load_dotenv
except ImportError as e:
    print(f"ERROR: Missing dependencies: {e}")
    print("Run: pip install boto3 psycopg2-binary python-dotenv")
    sys.exit(1)

load_dotenv()


def get_s3_client():
    """Create S3 client for B2."""
    endpoint = os.environ.get("BACKBLAZE_S3_ENDPOINT")
    key_id = os.environ.get("BACKBLAZE_KEY_ID")
    app_key = os.environ.get("BACKBLAZE_APPLICATION_KEY")

    if not all([endpoint, key_id, app_key]):
        print("ERROR: Missing B2 credentials in .env")
        print("Required: BACKBLAZE_S3_ENDPOINT, BACKBLAZE_KEY_ID, BACKBLAZE_APPLICATION_KEY")
        sys.exit(1)

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
    )


def get_papers_with_figures(s3, bucket: str) -> set:
    """
    Get set of paper IDs that have translated figures in B2.

    Scans figures/{paper_id}/translated/ directory.
    """
    paper_ids = set()
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix="figures/"):
        for obj in page.get("Contents", []):
            # Path: figures/chinaxiv-YYYYMM.NNNNN/translated/fig_N.png
            parts = obj["Key"].split("/")
            if len(parts) >= 3 and parts[2] == "translated":
                paper_ids.add(parts[1])

    return paper_ids


def update_has_figures(paper_ids: set, dry_run: bool = False) -> int:
    """
    Update has_figures column for papers with translated figures.

    Args:
        paper_ids: Set of paper IDs to mark as having figures
        dry_run: If True, don't actually update the database

    Returns:
        Number of rows updated
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    if not paper_ids:
        print("No papers with figures found in B2")
        return 0

    if dry_run:
        print(f"DRY RUN: Would update {len(paper_ids)} papers")
        for pid in sorted(paper_ids)[:10]:
            print(f"  - {pid}")
        if len(paper_ids) > 10:
            print(f"  ... and {len(paper_ids) - 10} more")
        return 0

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            # Update all papers in a single query using ANY()
            cur.execute(
                """
                UPDATE papers
                SET has_figures = TRUE
                WHERE id = ANY(%s) AND has_figures = FALSE
                """,
                (list(paper_ids),),
            )
            updated = cur.rowcount
            conn.commit()
            print(f"Updated {updated} papers (set has_figures = TRUE)")
            return updated
    finally:
        conn.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sync has_figures from B2 to PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually update DB")
    args = parser.parse_args()

    bucket = os.environ.get("BACKBLAZE_BUCKET", "chinaxiv")

    print("=" * 60)
    print("Sync has_figures from B2")
    print("=" * 60)
    print(f"Bucket: {bucket}")
    print()

    # Get papers with figures from B2
    print("Scanning B2 for papers with translated figures...")
    s3 = get_s3_client()
    paper_ids = get_papers_with_figures(s3, bucket)
    print(f"Found {len(paper_ids)} papers with translated figures in B2")
    print()

    # Update database
    if args.dry_run:
        print("DRY RUN MODE - no changes will be made")
    updated = update_has_figures(paper_ids, dry_run=args.dry_run)

    print()
    print("=" * 60)
    print(f"Done! {updated} papers updated")


if __name__ == "__main__":
    main()
