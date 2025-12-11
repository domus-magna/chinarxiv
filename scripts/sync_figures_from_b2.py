#!/usr/bin/env python3
"""
Sync figure_urls column from B2 manifest.

Downloads the figures/manifest.json from B2 and updates the PostgreSQL
figure_urls column for matching papers.

Usage:
    # Dry run (show what would be updated)
    python scripts/sync_figures_from_b2.py --dry-run

    # Actually update the database
    python scripts/sync_figures_from_b2.py

    # On Railway (use public proxy)
    DATABASE_URL="postgresql://postgres:...@metro.proxy.rlwy.net:52123/railway" \
      python scripts/sync_figures_from_b2.py
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import boto3
    import psycopg2
    from botocore.exceptions import ClientError
    from dotenv import load_dotenv
except ImportError as e:
    print(f"ERROR: Missing dependencies: {e}")
    print("Run: pip install boto3 psycopg2-binary python-dotenv")
    sys.exit(1)

load_dotenv()

# Constants
B2_BUCKET = os.environ.get("BACKBLAZE_BUCKET", "chinaxiv")


def log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


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


def get_figures_manifest(s3) -> dict:
    """Download figures manifest from B2."""
    try:
        response = s3.get_object(
            Bucket=B2_BUCKET,
            Key="figures/manifest.json"
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            log("WARNING: No manifest found in B2 (figures/manifest.json)")
            return {"papers": {}}
        raise


def update_figure_urls(figure_data: dict, dry_run: bool = False) -> int:
    """
    Update figure_urls column for papers with figures in B2.

    Args:
        figure_data: Dict mapping paper_id to figure data (list of figure objects)
        dry_run: If True, don't actually update the database

    Returns:
        Number of rows updated
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    if not figure_data:
        log("No papers with figures found in manifest")
        return 0

    if dry_run:
        log(f"DRY RUN: Would update {len(figure_data)} papers")
        for pid in sorted(figure_data.keys())[:10]:
            figures = figure_data[pid]
            fig_count = len(figures)
            log(f"  - {pid}: {fig_count} figure(s)")
        if len(figure_data) > 10:
            log(f"  ... and {len(figure_data) - 10} more")
        return 0

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            # Check if column exists
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'papers' AND column_name = 'figure_urls';
            """)
            if not cur.fetchone():
                log("ERROR: Column figure_urls does not exist")
                log("Run: python scripts/migrate_to_postgres.py")
                return 0

            # Update papers one by one (safe, clear progress)
            updated = 0
            for paper_id, figures in figure_data.items():
                # Convert figures list to JSON string
                figure_urls_json = json.dumps(figures)
                cur.execute(
                    """
                    UPDATE papers
                    SET figure_urls = %s
                    WHERE id = %s AND (figure_urls IS NULL OR figure_urls != %s)
                    """,
                    (figure_urls_json, paper_id, figure_urls_json),
                )
                if cur.rowcount > 0:
                    updated += 1

            conn.commit()
            log(f"Updated {updated} papers (set figure_urls)")
            return updated
    finally:
        conn.close()


def get_db_stats():
    """Get database statistics for summary."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return None

    try:
        conn = psycopg2.connect(database_url)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM papers")
            total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM papers WHERE figure_urls IS NOT NULL")
            with_figures = cur.fetchone()[0]

        conn.close()
        return {"total": total, "with_figures": with_figures}
    except Exception:
        return None


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sync figure_urls from B2 to PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually update DB")
    args = parser.parse_args()

    print("=" * 60)
    print("Sync Figure URLs from B2")
    print("=" * 60)
    print(f"Bucket: {B2_BUCKET}")
    print()

    # Get manifest from B2
    log("Downloading figures manifest from B2...")
    s3 = get_s3_client()
    manifest = get_figures_manifest(s3)

    papers = manifest.get("papers", {})
    log(f"Found {len(papers)} papers with figures in manifest")

    if not papers:
        log("Nothing to sync")
        return

    # Extract figure data (list of figure objects for each paper)
    figure_data = {}
    for pid, info in papers.items():
        figures = info.get("figures", [])
        if figures:
            figure_data[pid] = figures

    log(f"Papers with at least one figure: {len(figure_data)}")

    # Update database
    if args.dry_run:
        log("DRY RUN MODE - no changes will be made")
    updated = update_figure_urls(figure_data, dry_run=args.dry_run)

    # Show summary
    stats = get_db_stats()
    print()
    print("=" * 60)
    print(f"Done! {updated} papers updated")
    if stats:
        print(f"Database: {stats['with_figures']}/{stats['total']} papers have figure_urls")


if __name__ == "__main__":
    main()
