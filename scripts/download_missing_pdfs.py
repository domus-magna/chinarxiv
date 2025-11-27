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
import json
import os
from pathlib import Path
from typing import Iterable, List, Tuple

import sys
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pdf_pipeline import download_pdf
from src.file_service import ensure_dir
from src.logging_utils import log


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


def download_missing(files: List[Path], limit: int | None = None) -> Tuple[int, int, int]:
    downloaded = 0
    skipped = 0
    failures = 0
    ensure_dir(str(PDF_DIR))

    for rec in iter_records(files):
        rid = rec.get("id") or ""
        pdf_url = (rec.get("pdf_url") or "").strip()
        if not rid or not pdf_url:
            skipped += 1
            continue
        dst = PDF_DIR / f"{rid}.pdf"
        if dst.exists():
            skipped += 1
            continue
        # Try with referer and stable session id (paper id) for Unlocker cookies
        referer = (rec.get("source_url") or "").strip() or None
        ok = download_pdf(pdf_url, str(dst), referer=referer, session_id=rid)
        if ok:
            downloaded += 1
            log(f"Downloaded {rid} -> {dst}")
        else:
            failures += 1
            # remove any partial
            if dst.exists():
                try:
                    dst.unlink()
                except Exception:
                    pass
        if limit and downloaded >= limit:
            break
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
    args = ap.parse_args()

    months = [m.strip() for m in (args.months or "").split(",") if m.strip()] or None
    files = discover_record_files(months)
    if not files:
        log("No records files found; nothing to do")
        return 0

    log(f"Scanning {len(files)} records files for missing PDFs…")
    dl, sk, fail = download_missing(files, limit=(args.limit or None))
    log("")
    log(f"Summary: downloaded={dl}, skipped_existing_or_missing_url={sk}, failures={fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
