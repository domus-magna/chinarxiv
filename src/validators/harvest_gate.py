from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path

import requests
from bs4 import BeautifulSoup

from src.config import get_config
from src.reporting import build_markdown_report, save_validation_report


REQUIRED_FIELDS = [
    "id",
    "title",
    "abstract",
    "creators",
    "subjects",
    "date",
    "source_url",
    "pdf_url",
]
USER_AGENT = "chinarxiv-harvest-gate/1.0"
logger = logging.getLogger(__name__)


@dataclass
class HarvestGateSummary:
    total: int
    schema_pass: int
    pdf_ok: int
    dup_ids: int
    pass_threshold_met: bool


def _find_latest_records() -> Optional[str]:
    files = sorted(glob.glob(os.path.join("data", "records", "*.json")))
    return files[-1] if files else None


def _load_records(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        logger.exception("Failed to load records from %s", path)
        return []


def _check_schema(rec: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []

    for k in REQUIRED_FIELDS:
        if k not in rec or rec[k] in (None, ""):
            errors.append(f"Missing field: {k}")

    rid = rec.get("id")
    if not isinstance(rid, str) or not re.match(r"^chinaxiv-\d{6}\.\d{5}$", rid or ""):
        errors.append("Invalid id format")

    title = rec.get("title")
    if not isinstance(title, str):
        errors.append("Title must be string")
    else:
        if len(title.strip()) < 5:
            errors.append("Title too short or empty")

    abstract = rec.get("abstract")
    if not isinstance(abstract, str) or len(abstract.strip()) < 50:
        errors.append("Abstract too short")

    creators = rec.get("creators")
    if not isinstance(creators, list) or not creators:
        errors.append("Creators must be non-empty list")
    else:
        # Ensure at least one non-empty creator name
        if not any(isinstance(c, str) and c.strip() for c in creators):
            errors.append("Creators list has no actual names")

    subjects = rec.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        errors.append("Subjects must be non-empty list")
    else:
        if not any(isinstance(s, str) and s.strip() for s in subjects):
            errors.append("Subjects list has no actual values")

    if not isinstance(rec.get("date"), str):
        errors.append("Date must be string")

    pdf_url = rec.get("pdf_url")
    if not isinstance(pdf_url, str):
        errors.append("Invalid pdf_url")
    elif not pdf_url.startswith("http"):
        local_pdf = Path(pdf_url)
        if not local_pdf.exists():
            errors.append("Invalid pdf_url")

    source_url = rec.get("source_url")
    if not isinstance(source_url, str) or not source_url.startswith("http"):
        errors.append("Invalid source_url")

    return (len(errors) == 0, errors)


def _discover_pdf_url(source_url: str) -> Optional[str]:
    try:
        resp = requests.get(source_url, timeout=30, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        link = soup.find("a", href=re.compile(r"\.(pdf|PDF)(?:$|\?)"))
        if link and link.get("href"):
            href = link["href"]
            if href.startswith("http"):
                return href
            return requests.compat.urljoin(source_url, href)
    except Exception as exc:
        logger.warning("Failed to resolve PDF via source page %s: %s", source_url, exc)
        return None
    return None


def _check_pdf_access(url: str, timeout: int = 30) -> bool:
    local_path: Optional[Path] = None
    if url.startswith("file://"):
        local_path = Path(url[7:])
    elif not url.lower().startswith("http"):
        local_path = Path(url)

    if local_path:
        try:
            with open(local_path, "rb") as f:
                head = f.read(5)
            return bool(head.startswith(b"%PDF-"))
        except Exception as exc:
            logger.warning("Failed to read local PDF %s: %s", local_path, exc)
            return False

    try:
        with requests.get(
            url,
            stream=True,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            if "pdf" in content_type:
                return True
            chunk = next(resp.iter_content(chunk_size=1024), b"")
            return chunk.startswith(b"%PDF-")
    except StopIteration:
        return False
    except Exception as exc:
        logger.warning("Failed to access PDF %s: %s", url, exc)
        return False


def _resolve_pdf(rec: Dict[str, Any]) -> Tuple[Optional[str], List[str], bool]:
    issues: List[str] = []

    # Prefer pre-downloaded local PDF if available
    local_pdf_path = rec.get("pdf_local_path")
    if local_pdf_path:
        local_path = Path(local_pdf_path)
        if local_path.exists() and local_path.stat().st_size > 0:
            return str(local_path), issues, True
        issues.append(f"Local PDF missing or too small: {local_pdf_path}")

    pdf_url = rec.get("pdf_url")
    source_url = rec.get("source_url")
    if pdf_url and _check_pdf_access(pdf_url):
        return pdf_url, issues, True
    if source_url:
        resolved = _discover_pdf_url(source_url)
        if resolved and _check_pdf_access(resolved):
            return resolved, issues, True
        issues.append("Could not resolve PDF via source page")
    if pdf_url:
        issues.append(f"PDF inaccessible: {pdf_url}")
    else:
        issues.append("Missing pdf_url")
    return None, issues, False


def run_harvest_gate(
    records_path: Optional[str] = None, out_dir: str = "reports"
) -> HarvestGateSummary:
    records_path = records_path or _find_latest_records()
    if not records_path:
        summary = {
            "records_path": None,
            "total": 0,
            "schema_pass": 0,
            "pdf_ok": 0,
            "dup_ids": 0,
            "schema_rate": 0.0,
            "pdf_rate": 0.0,
            "pass": False,
            "reasons": ["no_records_found"],
        }
        detail = {"summary": summary, "results": {}}
        markdown = build_markdown_report(
            "Harvest Report",
            [
                ("Total", 0),
                ("Schema pass", "0 (0.0%)"),
                ("PDF OK", "0 (0.0% of schema-pass)"),
                ("Duplicate IDs", 0),
                ("Status", "FAIL"),
            ],
            summary["reasons"],
        )
        save_validation_report(out_dir, "harvest_report", detail, markdown, summary)
        return HarvestGateSummary(
            total=0, schema_pass=0, pdf_ok=0, dup_ids=0, pass_threshold_met=False
        )

    recs = _load_records(records_path)
    total = len(recs)
    schema_pass = 0
    pdf_ok = 0
    seen_ids: set[str] = set()
    dup_ids = 0
    per_rec: Dict[str, Any] = {}

    for idx, rec in enumerate(recs):
        rid = rec.get("id") or f"idx_{idx}"
        schema_ok, schema_errors = _check_schema(rec)
        resolved_url, pdf_issues, pdf_ok_flag = _resolve_pdf(rec)
        if schema_ok:
            schema_pass += 1
        if pdf_ok_flag:
            pdf_ok += 1
        if rid in seen_ids:
            dup_ids += 1
            pdf_issues.append("Duplicate ID")
        else:
            seen_ids.add(rid)
        per_rec[rid] = {
            "schema": schema_ok,
            "schema_errors": schema_errors,
            "pdf": {
                "pdf_ok": pdf_ok_flag,
                "resolved_url": resolved_url,
                "issues": pdf_issues,
            },
        }

    thresholds = get_config().get("validation_thresholds", {}).get("harvest", {})
    min_schema_rate = float(thresholds.get("min_schema_rate", 95.0))
    min_pdf_rate = float(thresholds.get("min_pdf_rate", 98.0))

    # Thresholds: schema >= configured %, pdf_ok >= configured % of schema_ok
    schema_rate = (schema_pass / total) * 100 if total else 0.0
    pdf_rate = (pdf_ok / schema_pass * 100) if schema_pass else 0.0
    pass_threshold = (
        (schema_rate >= min_schema_rate)
        and (pdf_rate >= min_pdf_rate)
        and (dup_ids == 0)
    )

    reasons: List[str] = []
    if schema_rate < min_schema_rate:
        reasons.append("schema_rate_below_threshold")
    if pdf_rate < min_pdf_rate:
        reasons.append("pdf_rate_below_threshold")
    if dup_ids:
        reasons.append("duplicate_ids_detected")

    summary = {
        "records_path": records_path,
        "total": total,
        "schema_pass": schema_pass,
        "pdf_ok": pdf_ok,
        "dup_ids": dup_ids,
        "schema_rate": round(schema_rate, 2),
        "pdf_rate": round(pdf_rate, 2),
        "pass": pass_threshold,
        "reasons": reasons,
        "thresholds": {
            "min_schema_rate": min_schema_rate,
            "min_pdf_rate": min_pdf_rate,
        },
    }
    detail = {
        "summary": summary,
        "results": per_rec,
    }
    markdown = build_markdown_report(
        "Harvest Report",
        [
            ("Total", total),
            ("Schema pass", f"{schema_pass} ({summary['schema_rate']}%)"),
            ("PDF OK", f"{pdf_ok} ({summary['pdf_rate']}% of schema-pass)"),
            ("Duplicate IDs", dup_ids),
            ("Status", "PASS" if pass_threshold else "FAIL"),
        ],
        reasons,
    )
    save_validation_report(out_dir, "harvest_report", detail, markdown, summary)

    return HarvestGateSummary(
        total=total,
        schema_pass=schema_pass,
        pdf_ok=pdf_ok,
        dup_ids=dup_ids,
        pass_threshold_met=pass_threshold,
    )


if __name__ == "__main__":
    summary = run_harvest_gate()
    print(
        json.dumps(
            {
                "total": summary.total,
                "schema_pass": summary.schema_pass,
                "pdf_ok": summary.pdf_ok,
                "dup_ids": summary.dup_ids,
                "pass": summary.pass_threshold_met,
            }
        )
    )
    should_fail = (summary.total == 0) or not summary.pass_threshold_met
    if should_fail:
        sys.stderr.write(
            "Harvest gate failed: no records processed or thresholds not met.\n"
        )
        sys.exit(1)
