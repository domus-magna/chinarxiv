#!/usr/bin/env python3
"""
Sync english_pdf_url column from B2 manifest.

Downloads the english_pdfs/manifest.json from B2 and updates the PostgreSQL
english_pdf_url column for matching papers.

Usage:
    # Dry run (show what would be updated)
    python scripts/sync_english_pdfs_from_b2.py --dry-run

    # Actually update the database
    python scripts/sync_english_pdfs_from_b2.py

    # On Railway (use public proxy)
    DATABASE_URL="postgresql://postgres:...@metro.proxy.rlwy.net:52123/railway" \
      python scripts/sync_english_pdfs_from_b2.py
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


def get_pdf_manifest(s3) -> dict:
    """Download English PDF manifest from B2."""
    try:
        response = s3.get_object(
            Bucket=B2_BUCKET,
            Key="english_pdfs/manifest.json"
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            log("WARNING: No manifest found in B2 (english_pdfs/manifest.json)")
            return {"papers": {}}
        raise


def update_english_pdf_urls(pdf_urls: dict, dry_run: bool = False) -> int:
    """
    Update english_pdf_url column for papers with PDFs in B2.

    Args:
        pdf_urls: Dict mapping paper_id to URL
        dry_run: If True, don't actually update the database

    Returns:
        Number of rows updated
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    if not pdf_urls:
        log("No papers with PDFs found in manifest")
        return 0

    if dry_run:
        log(f"DRY RUN: Would update {len(pdf_urls)} papers")
        for pid in sorted(pdf_urls.keys())[:10]:
            log(f"  - {pid}: {pdf_urls[pid][:60]}...")
        if len(pdf_urls) > 10:
            log(f"  ... and {len(pdf_urls) - 10} more")
        return 0

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            # Check if column exists
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'papers' AND column_name = 'english_pdf_url';
            """)
            if not cur.fetchone():
                log("ERROR: Column english_pdf_url does not exist")
                log("Run: python scripts/add_english_pdf_column.py")
                return 0

            # Update papers one by one (safe, clear progress)
            updated = 0
            for paper_id, url in pdf_urls.items():
                cur.execute(
                    """
                    UPDATE papers
                    SET english_pdf_url = %s
                    WHERE id = %s AND (english_pdf_url IS NULL OR english_pdf_url != %s)
                    """,
                    (url, paper_id, url),
                )
                if cur.rowcount > 0:
                    updated += 1

            conn.commit()
            log(f"Updated {updated} papers (set english_pdf_url)")
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

            cur.execute("SELECT COUNT(*) FROM papers WHERE english_pdf_url IS NOT NULL")
            with_pdf = cur.fetchone()[0]

        conn.close()
        return {"total": total, "with_pdf": with_pdf}
    except Exception:
        return None


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sync english_pdf_url from B2 to PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually update DB")
    args = parser.parse_args()

    print("=" * 60)
    print("Sync English PDF URLs from B2")
    print("=" * 60)
    print(f"Bucket: {B2_BUCKET}")
    print()

    # Get manifest from B2
    log("Downloading PDF manifest from B2...")
    s3 = get_s3_client()
    manifest = get_pdf_manifest(s3)

    papers = manifest.get("papers", {})
    log(f"Found {len(papers)} papers with PDFs in manifest")

    if not papers:
        log("Nothing to sync")
        return

    # Extract URLs
    pdf_urls = {pid: info["url"] for pid, info in papers.items()}

    # Update database
    if args.dry_run:
        log("DRY RUN MODE - no changes will be made")
    updated = update_english_pdf_urls(pdf_urls, dry_run=args.dry_run)

    # Show summary
    stats = get_db_stats()
    print()
    print("=" * 60)
    print(f"Done! {updated} papers updated")
    if stats:
        print(f"Database: {stats['with_pdf']}/{stats['total']} papers have english_pdf_url")


if __name__ == "__main__":
    main()
