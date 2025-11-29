from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.config import get_config
from src.reporting import build_markdown_report, save_validation_report

logger = logging.getLogger(__name__)


@dataclass
class OCRGateSummary:
    flagged: int
    improved: int
    pass_rate: float
    pass_threshold_met: bool
    missing_execution: int = 0
    unimproved: int = 0
    reasons: List[str] = field(default_factory=list)


def run_ocr_gate(report_dir: str = "reports") -> OCRGateSummary:
    report_path = os.path.join(report_dir, "ocr_report.json")

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception:
        logger.exception("Failed to load OCR report from %s", report_path)
        records = {}

    detection_missing = not bool(records)

    thresholds = get_config().get("validation_thresholds", {}).get("ocr", {})
    min_char_gain = int(thresholds.get("min_char_gain", 500))
    min_multiplier = float(thresholds.get("min_multiplier", 5.0))
    min_pass_rate = float(thresholds.get("min_pass_rate", 98.0))
    min_alpha_ratio = float(thresholds.get("min_alpha_ratio", 0.0))
    max_most_common_ratio = float(thresholds.get("max_most_common_ratio", 1.0))

    flagged = sum(1 for v in records.values() if v.get("need_ocr"))
    improved = 0
    results: Dict[str, Any] = {}
    missing_exec_ids = []
    unimproved_ids = []

    quality_issue_ids: List[str] = []

    for pid, meta in records.items():
        need = bool(meta.get("need_ocr"))
        pre = int(meta.get("pre_ocr_chars") or 0)
        post = int(meta.get("post_ocr_chars") or 0)
        ran = bool(meta.get("ran_ocr"))
        alpha = float(meta.get("post_alpha_ratio") or 0.0)
        most_common = float(meta.get("post_most_common_ratio") or 1.0)
        char_gain = post - pre
        ratio_gain = (post / pre) if pre > 0 else (float("inf") if post > 0 else 0.0)
        char_gain_ok = (char_gain >= min_char_gain) or (
            pre > 0 and ratio_gain >= min_multiplier
        )
        quality_ok = (alpha >= min_alpha_ratio) and (
            most_common <= max_most_common_ratio
        )
        improved_flag = ran and char_gain_ok and quality_ok

        if need and not ran:
            missing_exec_ids.append(pid)
        if need and ran and not quality_ok:
            quality_issue_ids.append(pid)
        if need and ran and not improved_flag:
            unimproved_ids.append(pid)
        if need and improved_flag:
            improved += 1
        results[pid] = {
            "need": need,
            "ran": ran,
            "pre": pre,
            "post": post,
            "improved": improved_flag,
            "char_gain_ok": char_gain_ok,
            "quality_ok": quality_ok,
            "alpha_ratio": alpha,
            "most_common_ratio": most_common,
        }

    # Deduplicate in case the same ID was captured via multiple branches.
    missing_exec_ids = sorted(set(missing_exec_ids))
    unimproved_ids = sorted(set(unimproved_ids))
    quality_issue_ids = sorted(set(quality_issue_ids))

    pass_rate = (improved / max(flagged, 1)) * 100.0 if flagged else 100.0
    pass_ok = (
        not detection_missing
        and not missing_exec_ids
        and not unimproved_ids
        and (pass_rate >= min_pass_rate or not flagged)
    )  # Threshold from plan

    summary = {
        "flagged": flagged,
        "improved": improved,
        "pass_rate": round(pass_rate, 2),
        "pass": pass_ok,
        "missing_execution": len(missing_exec_ids),
        "unimproved": len(unimproved_ids),
        "reasons": [],
        "thresholds": {
            "min_char_gain": min_char_gain,
            "min_multiplier": min_multiplier,
            "min_pass_rate": min_pass_rate,
            "min_alpha_ratio": min_alpha_ratio,
            "max_most_common_ratio": max_most_common_ratio,
        },
    }
    if detection_missing:
        summary["reasons"].append("detection_report_missing_or_empty")
    if missing_exec_ids:
        summary["reasons"].append("missing_execution_records")
    if unimproved_ids:
        summary["reasons"].append("insufficient_improvement")
    if quality_issue_ids:
        summary["reasons"].append("quality_threshold_not_met")

    payload = {
        "summary": summary,
        "results": results,
        "missing_execution_ids": missing_exec_ids,
        "unimproved_ids": unimproved_ids,
        "quality_issue_ids": quality_issue_ids,
    }
    markdown = build_markdown_report(
        "OCR Gate Report",
        [
            ("Flagged", flagged),
            ("Improved", improved),
            ("Missing execution records", len(missing_exec_ids)),
            ("Unimproved OCR runs", len(unimproved_ids)),
            ("Quality issues", len(quality_issue_ids)),
            ("Pass rate", f"{summary['pass_rate']}%"),
            ("Status", "PASS" if pass_ok else "FAIL"),
        ],
        summary["reasons"],
    )
    save_validation_report(report_dir, "ocr_gate_report", payload, markdown, summary)

    return OCRGateSummary(
        flagged=flagged,
        improved=improved,
        pass_rate=pass_rate,
        pass_threshold_met=pass_ok,
        missing_execution=len(missing_exec_ids),
        unimproved=len(unimproved_ids),
        reasons=summary["reasons"],
    )


if __name__ == "__main__":
    s = run_ocr_gate()
    print(
        json.dumps(
            {
                "flagged": s.flagged,
                "improved": s.improved,
                "pass_rate": s.pass_rate,
                "pass": s.pass_threshold_met,
                "missing_execution": s.missing_execution,
                "unimproved": s.unimproved,
                "reasons": s.reasons,
            }
        )
    )
    if not s.pass_threshold_met:
        raise SystemExit(1)
