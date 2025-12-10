import json
import shutil
from pathlib import Path
import subprocess

import pytest

from src import pdf_pipeline
from src.validators.harvest_gate import run_harvest_gate
from src.validators.ocr_gate import run_ocr_gate
from src.validators.translation_gate import run_translation_gate

REPO_ROOT = Path(__file__).resolve().parents[2]
OCR_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "ocr"


@pytest.fixture()
def fixture_pdf() -> Path:
    return (OCR_FIXTURE_DIR / "native_text.pdf").resolve()


def _make_record(record_id: str, pdf_path: Path) -> dict:
    return {
        "id": record_id,
        "title": "Sample Research Article on Fast OCR Validation",
        "abstract": "This abstract contains enough English content to satisfy the harvest gate length checks "
        "while exercising the end-to-end pipeline smoke test.",
        "creators": ["Doe, Jane"],
        "subjects": ["cs.CL"],
        "date": "2025-01-01",
        "source_url": "https://example.org/paper",
        "pdf_url": str(pdf_path),
    }


def test_pipeline_smoke_passes_all_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_pdf: Path
) -> None:
    """
    Run a single-record end-to-end smoke test covering harvest, OCR, and translation gates.
    """
    workspace = tmp_path
    monkeypatch.chdir(workspace)

    data_dir = workspace / "data"
    records_dir = data_dir / "records"
    records_dir.mkdir(parents=True)
    record_id = "chinaxiv-250101.00001"
    record = _make_record(record_id, fixture_pdf)
    records_path = records_dir / "202501.json"
    records_path.write_text(json.dumps([record]), encoding="utf-8")

    reports_dir = workspace / "reports"
    pdf_output_dir = data_dir / "pdfs"

    def fake_download(url: str, output_path: str) -> bool:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture_pdf, output_path)
        return True

    def fake_extract(path: str) -> list[str]:
        # Provide >1500 chars so the OCR heuristic marks the document as native text.
        return [" ".join(["lorem ipsum dolor sit amet"] * 120)]

    monkeypatch.setattr(pdf_pipeline, "download_pdf", fake_download)
    monkeypatch.setattr(pdf_pipeline, "extract_from_pdf", fake_extract)

    result = pdf_pipeline.process_paper(
        record_id, record["pdf_url"], pdf_dir=str(pdf_output_dir)
    )
    assert result is not None
    assert result["pdf_path"].endswith(".pdf")
    assert result["paragraphs"], "Expected extracted paragraphs for native PDF"

    ocr_report_path = reports_dir / "ocr_report.json"
    assert (
        ocr_report_path.exists()
    ), "process_paper should mirror results into reports/ocr_report.json"
    ocr_report = json.loads(ocr_report_path.read_text(encoding="utf-8"))
    entry = ocr_report[record_id]
    assert entry["need_ocr"] is False
    assert entry["ran_ocr"] is False
    assert entry["quality_ok"] is True

    harvest_summary = run_harvest_gate(
        records_path=str(records_path), out_dir=str(reports_dir)
    )
    assert (
        harvest_summary.pass_threshold_met
    ), "Harvest gate should pass for a valid single record"

    translated_dir = data_dir / "translated"
    translated_dir.mkdir(parents=True)
    # Synthesis mode uses body_md (markdown string) instead of body_en (list)
    body_md = """## Introduction

The pipeline smoke test ensures the translation gate accepts clean text. This section describes the methodology used in our research. We employed standard techniques from the field.

## Methods

Our approach follows established best practices. The implementation uses Python and associated libraries. Testing was conducted on standard hardware configurations.

## Results

The experimental results demonstrate successful validation. All quality metrics met the required thresholds. Performance was consistent across multiple runs."""
    translation_payload = {
        "id": record_id,
        "title_en": "Sample Research Article on Fast OCR Validation",
        "abstract_en": (
            "This English abstract is intentionally verbose to meet the QA length requirements while "
            "remaining free of Chinese characters or punctuation."
        ),
        "body_md": body_md,
        "_synthesis_mode": True,
    }
    (translated_dir / f"{record_id}.json").write_text(
        json.dumps(translation_payload), encoding="utf-8"
    )

    translation_summary = run_translation_gate(
        output_path=str(reports_dir / "translation_report.json")
    )
    assert translation_summary.total == 1
    assert translation_summary.flagged == 0
    assert translation_summary.passed == 1

    ocr_summary = run_ocr_gate(report_dir=str(reports_dir))
    assert ocr_summary.pass_threshold_met
    assert ocr_summary.flagged == 0


def test_process_paper_records_ocr_improvement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Ensure process_paper records OCR improvements when the OCR tools are available.
    """
    workspace = tmp_path
    monkeypatch.chdir(workspace)

    fixture_pdf = (OCR_FIXTURE_DIR / "scanned_text.pdf").resolve()
    data_dir = workspace / "data"
    data_dir.mkdir(parents=True)

    record_id = "chinaxiv-250101.00002"
    pdf_output_dir = data_dir / "pdfs"

    def fake_download(url: str, output_path: str) -> bool:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture_pdf, output_path)
        return True

    call_counter = {"calls": 0}

    def fake_extract(path: str) -> list[str]:
        call_counter["calls"] += 1
        if "ocr" in Path(path).parts:
            return [" ".join(["improved text output"] * 120)]
        return ["brief"]

    def fake_which(binary: str) -> str:
        return f"/usr/bin/{binary}"

    def fake_run(cmd, check, stdout, stderr):
        # Mirror the input PDF to the OCR output path so subsequent extraction runs.
        shutil.copyfile(fixture_pdf, cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pdf_pipeline, "download_pdf", fake_download)
    monkeypatch.setattr(pdf_pipeline, "extract_from_pdf", fake_extract)
    monkeypatch.setattr(pdf_pipeline.shutil, "which", fake_which)
    monkeypatch.setattr(pdf_pipeline.subprocess, "run", fake_run)

    result = pdf_pipeline.process_paper(
        record_id, str(fixture_pdf), pdf_dir=str(pdf_output_dir)
    )
    assert result is not None
    assert call_counter["calls"] >= 2  # Baseline extraction + post-OCR extraction

    report_path = Path("reports/ocr_report.json")
    record = json.loads(report_path.read_text(encoding="utf-8"))[record_id]
    assert record["need_ocr"] is True
    assert record["ran_ocr"] is True
    assert record["improved"] is True
    assert record["quality_ok"] is True
    assert record["post_ocr_chars"] > record["pre_ocr_chars"]
