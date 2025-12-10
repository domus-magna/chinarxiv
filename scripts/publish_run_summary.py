#!/usr/bin/env python3
"""Aggregate pipeline + hydration stats, upload to B2, and post Discord summary."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.alerts import AlertManager


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Required summary missing: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _upload_to_b2(local_path: Path) -> str | None:
    required = [
        "BACKBLAZE_KEY_ID",
        "BACKBLAZE_APPLICATION_KEY",
        "BACKBLAZE_S3_ENDPOINT",
        "BACKBLAZE_BUCKET",
    ]
    if any(not os.getenv(name) for name in required):
        return None
    prefix = os.getenv("BACKBLAZE_PREFIX", "")
    dest_root = f"s3://{os.getenv('BACKBLAZE_BUCKET')}/{prefix}"
    run_id = os.getenv("GITHUB_RUN_ID", datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    remote_key = f"reports/run-summary/{run_id}.json"
    cmd = [
        "aws",
        "s3",
        "cp",
        str(local_path),
        f"{dest_root}{remote_key}",
        "--endpoint-url",
        os.environ["BACKBLAZE_S3_ENDPOINT"],
        "--only-show-errors",
    ]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        return None
    return remote_key


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish nightly run summary to B2 and Discord.")
    parser.add_argument(
        "--pipeline-summary",
        default="reports/pipeline_summary.json",
        help="Pipeline summary JSON path (default: reports/pipeline_summary.json)",
    )
    parser.add_argument(
        "--b2-summary",
        default="reports/b2_publish_summary.json",
        help="B2 publish summary JSON path (default: reports/b2_publish_summary.json)",
    )
    parser.add_argument(
        "--hydration-summary",
        default="reports/hydration_summary.json",
        help="Hydration summary JSON path (default: reports/hydration_summary.json)",
    )
    parser.add_argument(
        "--output",
        default="reports/run_summary.json",
        help="Output summary file path (default: reports/run_summary.json)",
    )
    args = parser.parse_args()

    if os.getenv("EMIT_RUN_SUMMARY", "true").lower() != "true":
        print("Run summary disabled via EMIT_RUN_SUMMARY")
        return 0

    pipeline_summary = _load_json(Path(args.pipeline_summary))
    b2_summary = _load_json(Path(args.b2_summary))
    hydration_summary = {}
    hydration_path = Path(args.hydration_summary)
    if hydration_path.exists():
        with open(hydration_path, "r", encoding="utf-8") as fh:
            hydration_summary = json.load(fh)

    aggregate = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "git_sha": os.getenv("GITHUB_SHA"),
        "pipeline": pipeline_summary,
        "b2_publish": b2_summary,
        "hydration": hydration_summary,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False), encoding="utf-8")

    remote_key = _upload_to_b2(output_path)

    alerts = AlertManager()
    if alerts.enabled:
        attempted = pipeline_summary.get("attempted", 0)
        successes = pipeline_summary.get("successes", 0)
        qa_passed = pipeline_summary.get("qa_passed", 0)
        validated = b2_summary.get("validated_uploaded", 0)
        hydrated = hydration_summary.get("hydrated_count")
        hydrated_str = str(hydrated) if hydrated is not None else "n/a"

        message = (
            f"Attempted: {attempted} | Successes: {successes} | QA passed: {qa_passed}\n"
            f"Validated uploads: {validated} | Hydrated count: {hydrated_str}"
        )
        alerts.alert(
            level="info",
            title="Nightly run summary",
            message=message,
            immediate=True,
        )

    if remote_key:
        print(f"Run summary uploaded to {remote_key}")
    else:
        print("Run summary stored locally (B2 upload skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
