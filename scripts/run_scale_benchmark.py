#!/usr/bin/env python3

"""
Run scale benchmarks for the harvest validation gate.

The script can benchmark an existing records file or generate a synthetic dataset
that mimics the 3,411-paper workload referenced in Issue #24. Results are written
to reports/scale_benchmark/benchmark_result.json by default.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.validators.harvest_gate import run_harvest_gate  # noqa: E402


def _make_record(record_id: str, pdf_path: Path) -> dict:
    return {
        "id": record_id,
        "title": f"Synthetic record {record_id}",
        "abstract": (
            "This abstract provides sufficient length to satisfy harvest gate validation checks "
            "while enabling scale benchmarks without network requests."
        ),
        "creators": ["Benchmark, Test"],
        "subjects": ["cs.CL"],
        "date": "2025-01-01",
        "source_url": "https://example.org/benchmark",
        "pdf_url": str(pdf_path),
    }


def _generate_synthetic_dataset(count: int, pdf_fixture: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for idx in range(count):
        record_id = f"chinaxiv-250100.{idx:05d}"
        records.append(_make_record(record_id, pdf_fixture))
    records_path = output_dir / "synthetic_records.json"
    records_path.write_text(json.dumps(records), encoding="utf-8")
    return records_path


def benchmark_harvest_gate(records_path: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    summary = run_harvest_gate(records_path=str(records_path), out_dir=str(out_dir))
    duration = time.perf_counter() - start
    metrics = {
        "records_path": str(records_path),
        "total_records": summary.total,
        "schema_pass": summary.schema_pass,
        "pdf_ok": summary.pdf_ok,
        "dup_ids": summary.dup_ids,
        "pass_threshold_met": summary.pass_threshold_met,
        "duration_seconds": round(duration, 2),
    }
    result_path = out_dir / "benchmark_result.json"
    result_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark harvest gate at scale.")
    parser.add_argument("--records-path", type=Path, help="Existing records JSON file.")
    parser.add_argument(
        "--generate-synthetic",
        type=int,
        help="Generate a synthetic dataset with the given number of records (e.g., 3411).",
    )
    parser.add_argument(
        "--pdf-fixture",
        type=Path,
        default=Path("tests/fixtures/ocr/native_text.pdf"),
        help="PDF fixture to reference when generating synthetic datasets.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/scale_benchmark"),
        help="Directory to store benchmark artifacts.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.records_path and not args.generate_synthetic:
        raise SystemExit("Provide --records-path or --generate-synthetic.")

    records_path: Optional[Path] = args.records_path

    if args.generate_synthetic:
        pdf_fixture = args.pdf_fixture.resolve()
        if not pdf_fixture.exists():
            raise SystemExit(f"PDF fixture not found: {pdf_fixture}")
        synthetic_dir = args.output_dir / "synthetic"
        records_path = _generate_synthetic_dataset(args.generate_synthetic, pdf_fixture, synthetic_dir)
        print(f"Generated synthetic dataset with {args.generate_synthetic} records at {records_path}")

    assert records_path is not None
    records_path = records_path.resolve()
    if not records_path.exists():
        raise SystemExit(f"Records file not found: {records_path}")

    metrics = benchmark_harvest_gate(records_path, args.output_dir)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
