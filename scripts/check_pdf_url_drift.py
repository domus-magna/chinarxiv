#!/usr/bin/env python3
"""
Check for drift between B2 PDF storage and database english_pdf_url column.

Detects papers where:
- PDF exists in B2 but english_pdf_url is NULL in database

This is a monitoring/health check script. Exit code:
- 0: No drift detected
- 1: Drift detected (papers have PDFs but no URL)
- 2: Error running check

Usage:
    python scripts/check_pdf_url_drift.py
    python scripts/check_pdf_url_drift.py --fix  # Also fix the drift
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import boto3
    import psycopg2
    from dotenv import load_dotenv
except ImportError as e:
    print(f"ERROR: Missing dependencies: {e}")
    sys.exit(2)

load_dotenv()

B2_BUCKET = os.environ.get("BACKBLAZE_BUCKET", "chinaxiv")
B2_PDF_PREFIX = "english_pdfs/"
B2_PUBLIC_URL_BASE = "https://f004.backblazeb2.com/file/chinaxiv"


def get_s3_client():
    """Create S3 client for B2."""
    endpoint = os.environ.get("BACKBLAZE_S3_ENDPOINT")
    key_id = os.environ.get("BACKBLAZE_KEY_ID")
    app_key = os.environ.get("BACKBLAZE_APPLICATION_KEY")

    if not all([endpoint, key_id, app_key]):
        return None

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
    )


def get_db_connection():
    """Get PostgreSQL connection."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return None
    return psycopg2.connect(database_url)


def is_valid_paper_id(paper_id: str) -> bool:
    """Check if string matches expected paper ID format (chinaxiv-YYYYMM.NNNNN)."""
    if not paper_id.startswith("chinaxiv-"):
        return False
    # Basic validation: chinaxiv-YYYYMM.NNNNN (e.g., chinaxiv-202201.00001)
    parts = paper_id.replace("chinaxiv-", "").split(".")
    if len(parts) != 2:
        return False
    month_part, num_part = parts
    return len(month_part) == 6 and month_part.isdigit() and num_part.isdigit()


def list_b2_pdfs(s3) -> set:
    """List all paper IDs with PDFs in B2."""
    paper_ids = set()
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=B2_BUCKET, Prefix=B2_PDF_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".pdf"):
                # Extract paper ID from key like "english_pdfs/chinaxiv-202201.00001.pdf"
                filename = key.split("/")[-1]
                paper_id = filename.replace(".pdf", "")
                # Only include valid paper IDs (skip test files, backups, etc.)
                if is_valid_paper_id(paper_id):
                    paper_ids.add(paper_id)

    return paper_ids


def get_papers_missing_url(conn) -> set:
    """Get paper IDs where english_pdf_url is NULL."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM papers
        WHERE english_pdf_url IS NULL OR english_pdf_url = ''
    """)
    return {row[0] for row in cursor.fetchall()}


def fix_drift(conn, paper_ids: list):
    """Update english_pdf_url for papers with drift."""
    cursor = conn.cursor()
    fixed = 0

    for paper_id in paper_ids:
        url = f"{B2_PUBLIC_URL_BASE}/{B2_PDF_PREFIX}{paper_id}.pdf"
        cursor.execute(
            "UPDATE papers SET english_pdf_url = %s WHERE id = %s",
            (url, paper_id)
        )
        if cursor.rowcount > 0:
            fixed += 1

    conn.commit()
    return fixed


def main():
    parser = argparse.ArgumentParser(description="Check for PDF URL drift")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Fix drift by updating database URLs"
    )
    args = parser.parse_args()

    print("Checking for PDF URL drift...")
    print()

    # Connect to B2
    s3 = get_s3_client()
    if not s3:
        print("ERROR: Could not connect to B2 (missing credentials)")
        sys.exit(2)

    # Connect to database
    conn = get_db_connection()
    if not conn:
        print("ERROR: Could not connect to database (missing DATABASE_URL)")
        sys.exit(2)

    try:
        # Get PDFs in B2
        print("Scanning B2 for PDFs...")
        b2_pdfs = list_b2_pdfs(s3)
        print(f"  Found {len(b2_pdfs)} PDFs in B2")

        # Get papers missing URL
        print("Checking database for missing URLs...")
        missing_url = get_papers_missing_url(conn)
        print(f"  Found {len(missing_url)} papers with NULL english_pdf_url")

        # Find drift: PDFs in B2 but no URL in database
        drift = b2_pdfs & missing_url
        print()

        if not drift:
            print("✓ No drift detected")
            return 0

        print(f"⚠ DRIFT DETECTED: {len(drift)} papers have PDFs but no URL")
        print()

        # Show first 10 examples
        for paper_id in sorted(drift)[:10]:
            print(f"  - {paper_id}")
        if len(drift) > 10:
            print(f"  ... and {len(drift) - 10} more")

        if args.fix:
            print()
            print("Fixing drift...")
            fixed = fix_drift(conn, list(drift))
            print(f"✓ Fixed {fixed} papers")
            return 0

        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
