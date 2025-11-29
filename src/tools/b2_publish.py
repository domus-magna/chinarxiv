"""
Publish post-QA outputs to Backblaze B2 via awscli and generate CSV manifests and pointers.

Assumptions:
- Validated translations are saved under data/translated/*.json
- Flagged translations (diagnostics) are saved under data/flagged/*.json
- PDFs live under data/pdfs/{paper_id}.pdf (archival; default ON at workflow level)
- Cost and token logs may be in data/costs/<YYYY-MM-DD>.json (from cost_tracker)

Inputs via env:
- AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
- B2_S3_ENDPOINT, B2_BUCKET, B2_PREFIX
- SELECT_KEY (B2 selection key used), RECORDS_KEYS (comma-separated B2 keys)
- GITHUB_RUN_ID, GITHUB_SHA, RUN_STARTED_AT (ISO UTC)

This script:
1) Uploads validated translations, flagged translations, and PDFs to B2
2) Builds/updates CSV manifests (validated and flagged) under indexes/*
3) Writes per-paper pointer JSON under indexes/validated/by-paper/{paper_id}.json
4) Appends a row to indexes/runs/YYYYMMDD.csv with run summary
5) If any B2 ops are skipped/fail, buffers a Discord alert via b2_alerts (15-min throttle)
"""

from __future__ import annotations

import csv
import glob
import json
import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def _run(cmd: str) -> Tuple[int, str]:
    p = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    out, _ = p.communicate()
    return p.returncode, out or ""


def _aws_cp(local: str, remote: str, endpoint: str) -> bool:
    code, out = _run(
        f"aws s3 cp {shlex.quote(local)} {shlex.quote(remote)} --endpoint-url {shlex.quote(endpoint)} --only-show-errors"
    )
    return code == 0


def _aws_cp_maybe(remote: str, local: str, endpoint: str) -> bool:
    code, out = _run(
        f"aws s3 cp {shlex.quote(remote)} {shlex.quote(local)} --endpoint-url {shlex.quote(endpoint)} --only-show-errors"
    )
    return code == 0


def _alert(msg: str) -> None:
    _run(f"python -m src.tools.b2_alerts add {shlex.quote(msg)}")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _date_ymd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _load_costs_for_today() -> Dict[str, Dict]:
    day = datetime.now(timezone.utc).date().isoformat()
    path = Path("data/costs") / f"{day}.json"
    costs: Dict[str, Dict] = {}
    if path.exists():
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(items, list):
                for it in items:
                    pid = it.get("id")
                    if pid:
                        costs[pid] = it
        except Exception:
            pass
    return costs


def main() -> int:
    # Validate env
    missing = [
        n
        for n in [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "B2_S3_ENDPOINT",
            "B2_BUCKET",
        ]
        if not _env(n)
    ]
    if missing:
        _alert(f"B2 publish skipped: missing env {', '.join(missing)}")
        # Ensure alert is flushed even on failure
        _run("python -m src.tools.b2_alerts flush")
        return 2

    endpoint = _env("B2_S3_ENDPOINT")
    bucket = _env("B2_BUCKET")
    prefix = _env("B2_PREFIX", "") or ""
    dest_root = f"s3://{bucket}/{prefix}"

    select_key = _env("SELECT_KEY", "")
    records_keys = [
        k.strip() for k in (_env("RECORDS_KEYS", "") or "").split(",") if k.strip()
    ]
    run_id = _env("GITHUB_RUN_ID", "")
    git_sha = _env("GITHUB_SHA", "")
    run_started = _env("RUN_STARTED_AT", datetime.now(timezone.utc).isoformat())
    day = _today()

    # Discover files
    validated_files = sorted(glob.glob("data/translated/*.json"))
    flagged_files = sorted(glob.glob("data/flagged/*.json"))

    # PDFs (optional): upload any present
    pdf_files = {}
    for path in glob.glob("data/pdfs/*.pdf"):
        pid = Path(path).stem
        pdf_files[pid] = path

    costs = _load_costs_for_today()

    validated_rows: List[List[str]] = []
    flagged_rows: List[List[str]] = []
    validated_ok = 0
    flagged_count = 0
    pdf_uploaded = 0
    failure_count = 0

    # Ensure awscli exists
    code, out = _run("aws --version")
    if code != 0:
        _alert("awscli missing; cannot publish to B2")
        _run("python -m src.tools.b2_alerts flush")
        return 2

    # Upload validated translations and pointers
    for vf in validated_files:
        pid = Path(vf).stem
        validated_key = f"validated/translations/{pid}.json"
        if not _aws_cp(vf, f"{dest_root}{validated_key}", endpoint):
            _alert(f"upload failed: {validated_key}")
            failure_count += 1
            continue
        validated_ok += 1

        # pointer JSON
        pointer = {
            "paper_id": pid,
            "validated_key": validated_key,
            "selection_key": select_key,
            "run_id": run_id,
            "git_sha": git_sha,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = Path("/tmp") / f"pointer-{pid}.json"
        tmp.write_text(
            json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _aws_cp(str(tmp), f"{dest_root}indexes/validated/by-paper/{pid}.json", endpoint)

        # pdf if present
        pdf_key = ""
        if pid in pdf_files:
            if _aws_cp(pdf_files[pid], f"{dest_root}pdfs/{pid}.pdf", endpoint):
                pdf_uploaded += 1
                pdf_key = f"pdfs/{pid}.pdf"
            else:
                _alert(f"pdf upload failed: pdfs/{pid}.pdf")
                failure_count += 1

        # cost row
        c = costs.get(pid, {})
        in_toks = str(c.get("in_tokens", 0))
        out_toks = str(c.get("out_tokens", 0))
        cost_usd = str(c.get("cost_estimate_usd", 0.0))
        validated_rows.append(
            [
                pid,
                ";".join(records_keys) if records_keys else "",
                select_key,
                validated_key,
                pdf_key,
                c.get("model", ""),
                in_toks,
                out_toks,
                cost_usd,
                run_id,
                git_sha,
                datetime.now(timezone.utc).isoformat(),
            ]
        )

    # Upload flagged translations
    for ff in flagged_files:
        pid = Path(ff).stem
        flagged_key = f"flagged/translations/{pid}.json"
        if not _aws_cp(ff, f"{dest_root}{flagged_key}", endpoint):
            _alert(f"upload failed: {flagged_key}")
            failure_count += 1
            continue
        flagged_count += 1
        c = costs.get(pid, {})
        flagged_rows.append(
            [
                pid,
                ";".join(records_keys) if records_keys else "",
                select_key,
                "qa_flagged",
                "",
                c.get("model", ""),
                str(c.get("in_tokens", 0)),
                str(c.get("out_tokens", 0)),
                str(c.get("cost_estimate_usd", 0.0)),
                run_id,
                git_sha,
                datetime.now(timezone.utc).isoformat(),
            ]
        )

    # Helper to read, append, and re-upload CSV
    def _append_csv(s3_key: str, header: List[str], rows: List[List[str]]) -> None:
        nonlocal failure_count
        if not rows:
            return
        local = Path("/tmp") / Path(s3_key).name
        # Try download existing
        existed = _aws_cp_maybe(f"{dest_root}{s3_key}", str(local), endpoint)
        # Append
        mode = "a" if existed else "w"
        with open(local, mode, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not existed:
                w.writerow(header)
            w.writerows(rows)
        if not _aws_cp(str(local), f"{dest_root}{s3_key}", endpoint):
            _alert(f"csv upload failed: {s3_key}")
            failure_count += 1

    # Update manifests
    _append_csv(
        f"indexes/validated/manifest-{day}.csv",
        [
            "paper_id",
            "source_records_key",
            "selection_key",
            "validated_key",
            "pdf_key",
            "model_slug",
            "in_tokens",
            "out_tokens",
            "cost_usd",
            "run_id",
            "git_sha",
            "validated_at",
        ],
        validated_rows,
    )
    _append_csv(
        f"indexes/flagged/manifest-{day}.csv",
        [
            "paper_id",
            "source_records_key",
            "selection_key",
            "status",
            "pdf_key",
            "model_slug",
            "in_tokens",
            "out_tokens",
            "cost_usd",
            "run_id",
            "git_sha",
            "flagged_at",
        ],
        flagged_rows,
    )

    # Run summary
    summary_key = f"indexes/runs/{day}.csv"
    summary_row = [
        run_id,
        os.getenv("GITHUB_REF", ""),
        git_sha,
        run_started,
        datetime.now(timezone.utc).isoformat(),
        select_key,
        ";".join(records_keys) if records_keys else "",
        str(len(validated_files) + len(flagged_files)),
        str(len(validated_files) + len(flagged_files)),
        str(validated_ok),
        str(flagged_count),
        str(pdf_uploaded),
        # Approx total cost: sum of known items
        str(
            sum(float(r[8]) for r in validated_rows)
            + sum(float(r[8]) for r in flagged_rows)
        ),
    ]
    _append_csv(
        summary_key,
        [
            "run_id",
            "ref",
            "git_sha",
            "started_at",
            "completed_at",
            "selection_key",
            "records_keys",
            "selected_count",
            "attempted",
            "validated_ok",
            "flagged_count",
            "pdfs_uploaded",
            "total_cost_usd",
        ],
        [summary_row],
    )

    # Persist summary for downstream parity + reporting
    summary_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "git_sha": git_sha,
        "validated_uploaded": validated_ok,
        "flagged_uploaded": flagged_count,
        "pdf_uploaded": pdf_uploaded,
        "validated_manifest_key": f"indexes/validated/manifest-{day}.csv",
        "flagged_manifest_key": f"indexes/flagged/manifest-{day}.csv",
        "summary_key": summary_key,
        "selection_key": select_key,
        "records_keys": records_keys,
    }
    summary_path = Path("reports") / "b2_publish_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Final flush of alerts if any
    # Decide exit code based on failures and toggle
    total_items = len(validated_files) + len(flagged_files)
    fail_on_error = _env("B2_FAIL_ON_ERROR", "true").lower() == "true"

    # Always flush alerts before exit
    _run("python -m src.tools.b2_alerts flush")

    if fail_on_error and total_items > 0 and failure_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
