#!/usr/bin/env python3
"""
Upload generated English PDFs to B2 and update manifest.

Scans data/english_pdfs/ for PDF files, uploads to B2, and updates
the english_pdfs/manifest.json with paper IDs and URLs.

Usage:
    # Upload all PDFs in data/english_pdfs/
    python scripts/upload_english_pdfs.py

    # Dry run
    python scripts/upload_english_pdfs.py --dry-run

    # Upload specific PDFs
    python scripts/upload_english_pdfs.py --paper-ids chinaxiv-202201.00001 chinaxiv-202201.00002
"""
import argparse
import contextlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import boto3
    from botocore.exceptions import ClientError
    from dotenv import load_dotenv
except ImportError as e:
    print(f"ERROR: Missing dependencies: {e}")
    print("Run: pip install boto3 python-dotenv")
    sys.exit(1)

load_dotenv()

# Constants
B2_BUCKET = os.environ.get("BACKBLAZE_BUCKET", "chinaxiv")
B2_PDF_PREFIX = "english_pdfs/"
B2_PUBLIC_URL_BASE = "https://f004.backblazeb2.com/file/chinaxiv"


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


def get_existing_manifest(s3) -> dict:
    """Download existing manifest from B2."""
    try:
        response = s3.get_object(
            Bucket=B2_BUCKET,
            Key=f"{B2_PDF_PREFIX}manifest.json"
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            log("No existing manifest found, creating new one")
            return {"papers": {}, "last_updated": None}
        raise


def upload_pdf(s3, pdf_path: Path, paper_id: str) -> str:
    """
    Upload a PDF to B2.

    Returns the public URL on success.
    """
    key = f"{B2_PDF_PREFIX}{paper_id}.pdf"

    s3.upload_file(
        str(pdf_path),
        B2_BUCKET,
        key,
        ExtraArgs={
            "ContentType": "application/pdf",
            "ContentDisposition": f'attachment; filename="{paper_id}.pdf"',
        }
    )

    return f"{B2_PUBLIC_URL_BASE}/{key}"


def upload_manifest(s3, manifest: dict):
    """Upload manifest to B2."""
    manifest["last_updated"] = datetime.now(timezone.utc).isoformat()

    s3.put_object(
        Bucket=B2_BUCKET,
        Key=f"{B2_PDF_PREFIX}manifest.json",
        Body=json.dumps(manifest, indent=2),
        ContentType="application/json",
    )


def main():
    parser = argparse.ArgumentParser(description="Upload English PDFs to B2")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/english_pdfs"),
        help="Directory containing PDFs to upload (default: data/english_pdfs)"
    )
    parser.add_argument(
        "--paper-ids",
        nargs="+",
        help="Upload only specific paper IDs"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded without uploading"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-upload PDFs even if already in manifest"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Upload English PDFs to B2")
    print("=" * 60)
    print(f"Input: {args.input_dir}")
    print(f"Bucket: {B2_BUCKET}")
    print(f"Prefix: {B2_PDF_PREFIX}")
    print()

    # Find PDF files
    if not args.input_dir.exists():
        log(f"ERROR: Input directory does not exist: {args.input_dir}")
        sys.exit(1)

    pdf_files = list(args.input_dir.glob("*.pdf"))
    log(f"Found {len(pdf_files)} PDF files locally")

    if not pdf_files:
        log("No PDFs to upload")
        return

    # Create S3 client
    s3 = get_s3_client()

    # Get existing manifest
    log("Downloading existing manifest...")
    manifest = get_existing_manifest(s3)
    existing_count = len(manifest.get("papers", {}))
    log(f"Existing manifest has {existing_count} papers")

    # Filter files to upload
    to_upload = []
    for pdf_path in pdf_files:
        paper_id = pdf_path.stem

        # Filter by specific IDs if provided
        if args.paper_ids and paper_id not in args.paper_ids:
            continue

        # Skip if already in manifest (unless --force)
        if not args.force and paper_id in manifest.get("papers", {}):
            continue

        to_upload.append((pdf_path, paper_id))

    log(f"PDFs to upload: {len(to_upload)}")
    print()

    if args.dry_run:
        print("DRY RUN - Would upload:")
        for pdf_path, paper_id in to_upload[:20]:
            size_kb = pdf_path.stat().st_size / 1024
            print(f"  - {paper_id}.pdf ({size_kb:.1f} KB)")
        if len(to_upload) > 20:
            print(f"  ... and {len(to_upload) - 20} more")
        return

    if not to_upload:
        log("Nothing to upload")
        return

    # Upload PDFs
    success_count = 0
    fail_count = 0
    meta_files_to_cleanup = []

    for i, (pdf_path, paper_id) in enumerate(to_upload, 1):
        size_kb = pdf_path.stat().st_size / 1024
        log(f"[{i}/{len(to_upload)}] Uploading {paper_id}.pdf ({size_kb:.1f} KB)...")

        # Read metadata file if it exists (generated alongside PDF)
        meta_path = pdf_path.with_suffix(".meta.json")
        has_figures = False
        figure_count = 0
        generated_at = datetime.now(timezone.utc).isoformat()

        if meta_path.exists():
            try:
                with open(meta_path) as f:
                    meta_data = json.load(f)
                    has_figures = meta_data.get("has_figures", False)
                    figure_count = meta_data.get("figure_count", 0)
                    generated_at = meta_data.get("generated_at", generated_at)
                meta_files_to_cleanup.append(meta_path)
            except Exception as e:
                log(f"  Warning: Could not read metadata: {e}")

        try:
            url = upload_pdf(s3, pdf_path, paper_id)
            manifest["papers"][paper_id] = {
                "url": url,
                "generated_at": generated_at,
                "size_bytes": pdf_path.stat().st_size,
                "has_figures": has_figures,
                "figure_count": figure_count,
            }
            success_count += 1
        except Exception as e:
            log(f"  ✗ Failed: {e}")
            fail_count += 1

    # Upload updated manifest
    if success_count > 0:
        log("Uploading updated manifest...")
        upload_manifest(s3, manifest)
        log(f"✓ Manifest updated ({len(manifest['papers'])} papers total)")

    # Clean up metadata files after successful upload
    if meta_files_to_cleanup:
        for meta_path in meta_files_to_cleanup:
            with contextlib.suppress(OSError):
                meta_path.unlink()
        log(f"Cleaned up {len(meta_files_to_cleanup)} metadata files")

    print()
    print("=" * 60)
    print(f"Done! Uploaded {success_count} PDFs, {fail_count} failures")
    print(f"Manifest: {B2_PUBLIC_URL_BASE}/{B2_PDF_PREFIX}manifest.json")


if __name__ == "__main__":
    main()
