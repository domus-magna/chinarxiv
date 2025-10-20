from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.validators.harvest_gate import run_harvest_gate

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests/fixtures/harvest"


def test_harvest_gate_fails_without_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    summary = run_harvest_gate()
    assert summary.total == 0
    assert not summary.pass_threshold_met


def test_harvest_gate_passes_with_fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdf_dest = tmp_path / "sample.pdf"
    shutil.copy(FIXTURE_DIR / "sample.pdf", pdf_dest)

    records = json.loads((FIXTURE_DIR / "records_sample.json").read_text(encoding="utf-8"))
    records[0]["pdf_url"] = str(pdf_dest)

    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    summary = run_harvest_gate(records_path=str(records_path), out_dir="reports")
    assert summary.total == 1
    assert summary.schema_pass == 1
    assert summary.pdf_ok == 1
    assert summary.pass_threshold_met


def test_harvest_gate_prefers_local_pdf(tmp_path, monkeypatch):
    """
    Ensure the gate marks PDFs as accessible when a pre-downloaded path is provided,
    even if the remote URL would fail.
    """
    monkeypatch.chdir(tmp_path)
    pdf_dest = tmp_path / "sample.pdf"
    shutil.copy(FIXTURE_DIR / "sample.pdf", pdf_dest)

    records = json.loads((FIXTURE_DIR / "records_sample.json").read_text(encoding="utf-8"))
    records[0]["pdf_url"] = "https://chinaxiv.org/user/download.htm?uuid=missing"
    records[0]["pdf_local_path"] = str(pdf_dest)

    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    summary = run_harvest_gate(records_path=str(records_path), out_dir="reports")
    assert summary.total == 1
    assert summary.schema_pass == 1
    assert summary.pdf_ok == 1
    assert summary.pass_threshold_met
