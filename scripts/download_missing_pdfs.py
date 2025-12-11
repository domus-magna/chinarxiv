#!/usr/bin/env python3
"""
Download missing PDFs for harvested records.

Scans data/records/chinaxiv_*.json and downloads PDFs to data/pdfs/{paper_id}.pdf
for any record with a valid pdf_url that is not already present on disk.

Usage examples:
  python scripts/download_missing_pdfs.py                # all months discovered
  python scripts/download_missing_pdfs.py --months 202508,202509
  python scripts/download_missing_pdfs.py --limit 100    # stop after 100 downloads

Simplicity-first: sequential downloads with basic summary output.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pdf_pipeline import download_pdf  # noqa: E402
from src.file_service import ensure_dir  # noqa: E402
from src.logging_utils import log  # noqa: E402


RECORDS_DIR = Path("data/records")
PDF_DIR = Path("data/pdfs")


def month_from_id(paper_id: str) -> str | None:
    # e.g., chinaxiv-202510.00031 -> 202510
    try:
        return paper_id.split("-")[1].split(".")[0]
    except Exception:
        return None


def iter_records(files: Iterable[Path]) -> Iterable[dict]:
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for rec in data:
            if isinstance(rec, dict):
                yield rec


def discover_record_files(selected_months: List[str] | None) -> List[Path]:
    if not RECORDS_DIR.exists():
        return []
    files = sorted(RECORDS_DIR.glob("chinaxiv_*.json"))
    if not selected_months:
        return files
    sel = set(selected_months)
    out: List[Path] = []
    for f in files:
        m = f.stem.split("_")[-1]
        if m in sel:
            out.append(f)
    return out


def download_missing(
    files: List[Path],
    limit: int | None = None,
    allowed_ids: set[str] | None = None,
) -> Tuple[int, int, int]:
    import time

    downloaded = 0
    skipped = 0
    failures = 0
    found_in_records = 0
    ensure_dir(str(PDF_DIR))

    # First pass: count how many need downloading (for progress)
    to_download = []
    for rec in iter_records(files):
        rid = rec.get("id") or ""
        pdf_url = (rec.get("pdf_url") or "").strip()
        if not rid or not pdf_url:
            continue
        if allowed_ids is not None and rid not in allowed_ids:
            continue
        dst = PDF_DIR / f"{rid}.pdf"
        if dst.exists():
            continue
        to_download.append(rec)

    total = len(to_download)
    if limit:
        total = min(total, limit)

    log(f"📥 Starting download: {total} PDFs to fetch")
    start_time = time.time()

    for i, rec in enumerate(to_download):
        if limit and downloaded >= limit:
            break

        rid = rec.get("id") or ""
        pdf_url = (rec.get("pdf_url") or "").strip()

        if allowed_ids is not None:
            found_in_records += 1

        dst = PDF_DIR / f"{rid}.pdf"
        progress = i + 1

        log(f"[{progress}/{total}] Downloading {rid}...")

        # Try with referer and stable session id (paper id) for Unlocker cookies
        referer = (rec.get("source_url") or "").strip() or None
        ok = download_pdf(pdf_url, str(dst), referer=referer, session_id=rid)

        if ok:
            downloaded += 1
            # Get file size for logging
            try:
                size_mb = dst.stat().st_size / (1024 * 1024)
                log(f"[{progress}/{total}] ✓ Success ({size_mb:.1f} MB)")
            except Exception:
                log(f"[{progress}/{total}] ✓ Success")
        else:
            failures += 1
            log(f"[{progress}/{total}] ✗ Failed")
            # remove any partial
            if dst.exists():
                with contextlib.suppress(Exception):
                    dst.unlink()

    elapsed = time.time() - start_time
    log(f"✅ Download complete in {elapsed:.1f}s: {downloaded} succeeded, {failures} failed")

    # Log requested vs found if filtering
    if allowed_ids is not None:
        log(f"Found {found_in_records}/{len(allowed_ids)} requested IDs in records")
        if found_in_records < len(allowed_ids):
            log(f"⚠️  {len(allowed_ids) - found_in_records} IDs not found in records")

    return downloaded, skipped, failures


def main() -> int:
    ap = argparse.ArgumentParser(description="Download missing PDFs for harvested records")
    ap.add_argument(
        "--months",
        help="Comma-separated list of YYYYMM months (default: discover all under data/records)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N successful downloads (default: 0 = no limit)",
    )
    ap.add_argument(
        "--paper-ids",
        help="File containing paper IDs to limit downloads to (one per line)",
    )
    args = ap.parse_args()

    months = [m.strip() for m in (args.months or "").split(",") if m.strip()] or None
    files = discover_record_files(months)
    if not files:
        log("No records files found; nothing to do")
        return 0

    # Parse allowed paper IDs if specified
    allowed_ids = None
    if args.paper_ids:
        paper_ids_path = Path(args.paper_ids)
        if not paper_ids_path.exists():
            log(f"Paper IDs file not found: {args.paper_ids}")
            return 1
        with open(paper_ids_path) as f:
            allowed_ids = {line.strip() for line in f if line.strip()}
        log(f"Filtering to {len(allowed_ids)} requested paper IDs")

    log(f"Scanning {len(files)} records files for missing PDFs…")
    dl, sk, fail = download_missing(files, limit=(args.limit or None), allowed_ids=allowed_ids)
    log("")
    log(f"Summary: downloaded={dl}, skipped_existing_or_missing_url={sk}, failures={fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
