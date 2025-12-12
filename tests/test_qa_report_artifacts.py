import json
from datetime import datetime, timezone
from pathlib import Path

from src.tools.b2_publish import _qa_report_remote_key


def test_qa_report_remote_key_is_dated_and_timestamped() -> None:
    generated_at = datetime(2025, 12, 12, 19, 30, 45, tzinfo=timezone.utc).isoformat()
    key = _qa_report_remote_key("chinaxiv-202507.00007", generated_at)
    assert key.startswith("reports/qa/20251212/")
    assert key.endswith(".json")
    assert "chinaxiv-202507.00007_" in key


def test_translate_qa_report_format_is_compact(tmp_path: Path) -> None:
    report = {
        "paper_id": "chinaxiv-202507.00007",
        "generated_at": "2025-12-12T19:30:45+00:00",
        "qa_status": "pending",
        "qa_score": 0.7,
        "qa_issues": ["Title too long"],
        "qa_chinese_ratio": 0.0,
        "title_en_len": 5000,
        "abstract_en_len": 200,
        "body_md_len": 10000,
    }
    p = tmp_path / "qa.json"
    p.write_text(json.dumps(report), encoding="utf-8")
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["paper_id"] == "chinaxiv-202507.00007"
    assert loaded["generated_at"]
    assert isinstance(loaded["qa_issues"], list)
