import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.prepare_gate_fixtures import generate_scanned_pdf
from src.tools.prepare_ocr_report import populate_ocr_report


def _ocr_tools_compatible() -> bool:
    """Check if OCR tools are available AND compatible."""
    if shutil.which("ocrmypdf") is None or shutil.which("tesseract") is None:
        return False
    # Test that ocrmypdf actually works (not just --version)
    # ocrmypdf 16.8.0 has a runtime bug with some pikepdf versions
    # (AttributeError: 'pikepdf._core.Pdf' object has no attribute 'check')
    import tempfile
    from PIL import Image
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal test PDF
            test_pdf = Path(tmpdir) / "test.pdf"
            out_pdf = Path(tmpdir) / "out.pdf"
            img = Image.new("RGB", (100, 100), "white")
            img.save(test_pdf, "PDF")
            # Try to run OCR
            result = subprocess.run(
                ["ocrmypdf", "--skip-text", test_pdf, out_pdf],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(
    not _ocr_tools_compatible(),
    reason="OCR toolchain not available or incompatible",
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
