"""
Populate reports/ocr_report.json by running pdf_pipeline.process_paper on a small set
of records. Used by CI gate workflows to ensure OCR validation has fresh telemetry.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Iterable, List, Optional

from ..pdf_pipeline import process_paper


DEFAULT_RECORDS_DIR = Path("data/records")
DEFAULT_REPORT_PATH = Path("reports/ocr_report.json")


def _find_latest_records(records_dir: Path) -> Optional[Path]:
    candidates: List[Path] = sorted(records_dir.glob("*.json"))
    return candidates[-1] if candidates else None


def _load_records(path: Path) -> List[dict]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def populate_ocr_report(
    records_path: Optional[Path] = None,
    limit: int = 5,
    pdf_dir: Path = Path("data/pdfs"),
    report_path: Path = DEFAULT_REPORT_PATH,
) -> int:
    """
    Run process_paper for a handful of records so pdf_pipeline emits OCR telemetry.

    Returns the number of records processed.
    """
    if records_path is None:
        records_path = _find_latest_records(DEFAULT_RECORDS_DIR)
        if records_path is None:
            return 0

    records = _load_records(records_path)
    if not records:
        return 0

    processed = 0
    attempts = 0
    failures: List[str] = []
    errors: List[str] = []
    pdf_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    slice_records = records if limit <= 0 else records[:limit]

    for rec in slice_records:
        paper_id = rec.get("id")
        pdf_url = rec.get("pdf_url")
        if not paper_id or not pdf_url:
            continue
        attempts += 1
        if not str(pdf_url).lower().startswith("http"):
            local_pdf = Path(str(pdf_url))
            if local_pdf.exists():
                target_pdf = pdf_dir / f"{paper_id}.pdf"
                target_pdf.parent.mkdir(parents=True, exist_ok=True)
                if not target_pdf.exists():
                    try:
                        shutil.copy(local_pdf, target_pdf)
                    except Exception:
                        failures.append(paper_id)
                        continue
        try:
            result = process_paper(paper_id, pdf_url, pdf_dir=str(pdf_dir))
            if result:
                processed += 1
            else:
                failures.append(paper_id)
        except Exception as exc:
            errors.append(f"{paper_id}: {exc}")
            continue

    if processed == 0:
        if attempts == 0:
            print("prepare_ocr_report: no eligible records found to process.", file=sys.stderr)
        else:
            msg = "prepare_ocr_report: OCR telemetry generation failed"
            details = []
            if failures:
                details.append(f"{len(failures)} records returned no text")
            if errors:
                details.append(f"{len(errors)} errors raised")
            if details:
                msg += f" ({', '.join(details)})"
            print(msg, file=sys.stderr)
            if failures:
                print(f"  Failures: {', '.join(failures)}", file=sys.stderr)
            if errors:
                print("  Errors:", file=sys.stderr)
                for err in errors:
                    print(f"    - {err}", file=sys.stderr)
    return processed


def main(args: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Populate OCR report before running gate validation.")
    parser.add_argument("--records", type=Path, help="Specific records JSON file to process.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of records to process (0 = all).")
    parser.add_argument("--pdf-dir", type=Path, default=Path("data/pdfs"), help="Directory where PDFs are stored.")
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="OCR report path (defaults to reports/ocr_report.json).",
    )
    parsed = parser.parse_args(args=args)

    processed = populate_ocr_report(
        records_path=parsed.records,
        limit=parsed.limit,
        pdf_dir=parsed.pdf_dir,
        report_path=parsed.report,
    )

    if processed == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
