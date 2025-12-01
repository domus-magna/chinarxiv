#!/usr/bin/env python3
"""
B2 Storage Status Script

Checks Backblaze B2 for:
- Text translations (validated)
- PDFs
- Figures (translated)
- Last activity date

Usage:
    python scripts/b2_status.py
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import boto3
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: Missing dependencies. Run: pip install boto3 python-dotenv")
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


def count_objects(s3, bucket: str, prefix: str) -> tuple[int, datetime | None]:
    """Count objects under prefix and find most recent."""
    count = 0
    last_modified = None
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            count += 1
            if last_modified is None or obj["LastModified"] > last_modified:
                last_modified = obj["LastModified"]

    return count, last_modified


def count_papers_with_figures(s3, bucket: str) -> int:
    """Count unique paper IDs that have translated figures."""
    paper_ids = set()
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix="figures/"):
        for obj in page.get("Contents", []):
            # Path: figures/chinaxiv-YYYYMM.NNNNN/translated/fig_N.png
            parts = obj["Key"].split("/")
            if len(parts) >= 3 and parts[2] == "translated":
                paper_ids.add(parts[1])

    return len(paper_ids)


def main():
    bucket = os.environ.get("BACKBLAZE_BUCKET", "chinaxiv")
    s3 = get_s3_client()

    print("=" * 60)
    print("ChinaRxiv B2 Storage Status")
    print("=" * 60)
    print(f"Bucket: {bucket}")
    print(f"Checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Text translations
    trans_count, trans_last = count_objects(s3, bucket, "validated/translations/")
    print(f"Text Translations (validated):")
    print(f"  Count:         {trans_count:,}")
    print(f"  Last activity: {trans_last.strftime('%Y-%m-%d %H:%M') if trans_last else 'N/A'}")
    print()

    # Flagged translations
    flagged_count, flagged_last = count_objects(s3, bucket, "flagged/translations/")
    print(f"Flagged Translations (need review):")
    print(f"  Count:         {flagged_count:,}")
    print(f"  Last activity: {flagged_last.strftime('%Y-%m-%d %H:%M') if flagged_last else 'N/A'}")
    print()

    # PDFs
    pdf_count, pdf_last = count_objects(s3, bucket, "pdfs/")
    print(f"Source PDFs:")
    print(f"  Count:         {pdf_count:,}")
    print(f"  Last activity: {pdf_last.strftime('%Y-%m-%d %H:%M') if pdf_last else 'N/A'}")
    print()

    # Figures
    fig_count, fig_last = count_objects(s3, bucket, "figures/")
    papers_with_figs = count_papers_with_figures(s3, bucket) if fig_count > 0 else 0
    print(f"Translated Figures:")
    print(f"  Total files:   {fig_count:,}")
    print(f"  Papers w/figs: {papers_with_figs:,}")
    print(f"  Last activity: {fig_last.strftime('%Y-%m-%d %H:%M') if fig_last else 'N/A'}")
    print()

    # Records
    rec_count, rec_last = count_objects(s3, bucket, "records/")
    print(f"Harvested Records:")
    print(f"  Count:         {rec_count:,}")
    print(f"  Last activity: {rec_last.strftime('%Y-%m-%d %H:%M') if rec_last else 'N/A'}")
    print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    overall_last = max(
        filter(None, [trans_last, pdf_last, fig_last, rec_last]),
        default=None,
    )
    print(f"Total text translations: {trans_count:,}")
    print(f"Papers with figures:     {papers_with_figs:,} / {trans_count:,}", end="")
    if trans_count > 0:
        print(f" ({100 * papers_with_figs / trans_count:.1f}%)")
    else:
        print()
    print(f"Last B2 activity:        {overall_last.strftime('%Y-%m-%d %H:%M') if overall_last else 'N/A'}")

    # Warnings
    print()
    if fig_count == 0:
        print("WARNING: No figures in B2! Figure pipeline may have failed.")
    if trans_count == 0:
        print("WARNING: No translations in B2! Check pipeline status.")


if __name__ == "__main__":
    main()
