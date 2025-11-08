from glob import glob

from src.translate import translate_record


def test_cost_log_written(tmp_path, monkeypatch):
    # Write cost logs under tmp data
    monkeypatch.chdir(tmp_path)
    rec = {
        "id": "X1",
        "title": "机器学习",
        "abstract": "这是一个摘要。",
        "license": {"raw": "CC BY"},
        "oai_identifier": "oai:chinaxiv.org:2025-0001",
    }
    out = translate_record(rec, model="deepseek/deepseek-v3.2-exp", glossary=[], dry_run=True)
    assert out["title_en"]
    logs = glob("data/costs/*.json")
    assert logs, "Expected a daily cost log file to be created"

