import json
import shutil
from pathlib import Path

import pytest

from scripts.prepare_gate_fixtures import generate_scanned_pdf
from src.tools.prepare_ocr_report import populate_ocr_report


@pytest.mark.skipif(
    shutil.which("ocrmypdf") is None or shutil.which("tesseract") is None,
    reason="OCR toolchain not available",
)
def test_populate_ocr_report_generates_improvement(tmp_path, monkeypatch):
    workspace = tmp_path
    monkeypatch.chdir(workspace)

    records_dir = workspace / "data" / "records"
    records_dir.mkdir(parents=True)

    pdf_path = workspace / "data" / "pdfs" / "sample.pdf"
    generate_scanned_pdf(pdf_path)

    records = [{"id": "test-paper", "pdf_url": str(pdf_path)}]
    records_file = records_dir / "test.json"
    records_file.write_text(json.dumps(records), encoding="utf-8")

    processed = populate_ocr_report(records_path=records_file, limit=1)
    assert processed == 1

    report_path = Path("reports/ocr_report.json")
    data = json.loads(report_path.read_text(encoding="utf-8"))
    entry = data["test-paper"]
    assert entry["improved"] is True
    assert entry["post_ocr_chars"] > entry["pre_ocr_chars"]
