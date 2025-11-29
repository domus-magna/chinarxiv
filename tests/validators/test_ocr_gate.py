from __future__ import annotations

import json
from pathlib import Path


from src.validators.ocr_gate import run_ocr_gate


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_gate_fails_when_detection_missing(tmp_path: Path) -> None:
    """No detection report should register as a hard failure."""
    summary = run_ocr_gate(report_dir=str(tmp_path))
    assert not summary.pass_threshold_met
    assert "detection_report_missing_or_empty" in summary.reasons


def test_gate_flags_missing_execution(tmp_path: Path) -> None:
    """Missing execution entries for flagged items should fail the gate."""
    report_dir = tmp_path
    report = {
        "paper-1": {
            "need_ocr": True,
            "pre_ocr_chars": 100,
            "ran_ocr": False,
            "post_ocr_chars": 100,
        },
        "paper-2": {
            "need_ocr": False,
            "pre_ocr_chars": 2000,
            "ran_ocr": False,
            "post_ocr_chars": 2000,
        },
    }
    write_json(report_dir / "ocr_report.json", report)

    summary = run_ocr_gate(report_dir=str(report_dir))
    assert not summary.pass_threshold_met
    assert summary.missing_execution == 1
    assert "missing_execution_records" in summary.reasons


def test_gate_passes_with_improvements(tmp_path: Path) -> None:
    """Gate should pass when all flagged items improved past the threshold."""
    report_dir = tmp_path
    report = {
        "paper-1": {
            "need_ocr": True,
            "pre_ocr_chars": 100,
            "ran_ocr": True,
            "post_ocr_chars": 1000,
        },
        "paper-2": {
            "need_ocr": False,
            "pre_ocr_chars": 2000,
            "ran_ocr": False,
            "post_ocr_chars": 2000,
        },
    }
    write_json(report_dir / "ocr_report.json", report)

    summary = run_ocr_gate(report_dir=str(report_dir))
    assert summary.pass_threshold_met
    assert summary.improved == 1
    assert summary.reasons == []
