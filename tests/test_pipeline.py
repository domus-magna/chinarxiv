import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from src.pipeline import run_cli as pipeline_run


def _chdir_tmp():
    tmp = tempfile.mkdtemp(prefix="chinaxiv_pipeline_test_")
    cwd = os.getcwd()
    os.chdir(tmp)
    return tmp, cwd


def _restore_tmp(tmp: str, cwd: str):
    os.chdir(cwd)
    shutil.rmtree(tmp, ignore_errors=True)


def _load_summary() -> dict:
    path = Path("reports/pipeline_summary.json")
    assert path.exists(), "pipeline summary missing"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_pipeline_dry_run_skip_selection(monkeypatch):
    tmp, cwd = _chdir_tmp()
    try:
        # Prepare minimal selected item with adequate abstract length to pass QA
        os.makedirs("data", exist_ok=True)
        selected = [
            {
                "id": "test-1",
                "title": "A Study on Machine Learning Applications in Physics",
                "abstract": "This paper presents a comprehensive study on machine learning applications in particle physics. We demonstrate how neural networks can be used to improve beam correction accuracy.",
                "license": {"raw": "", "derivatives_allowed": True},
                "source_url": "",
            }
        ]
        with open("data/selected.json", "w", encoding="utf-8") as f:
            json.dump(selected, f, ensure_ascii=False)

        # Stub os.system calls for render/index/pdf to avoid spawning module subprocesses
        import os as _os
        def _fake_system(cmd: str) -> int:
            # Simulate successful render/index/pdf and create minimal site output
            if "src.render" in cmd or "src.search_index" in cmd or "src.make_pdf" in cmd:
                Path("site").mkdir(parents=True, exist_ok=True)
                (Path("site")/"index.html").write_text("<html></html>", encoding="utf-8")
                return 0
            # Allow select_and_fetch if called (should not be for skip-selection)
            if "src.select_and_fetch" in cmd:
                return 0
            return 0
        monkeypatch.setattr(_os, "system", _fake_system)

        # Invoke pipeline with skip-selection and dry-run so no external calls happen
        import sys
        sys.argv = [
            "pipeline",
            "--skip-selection",
            "--workers",
            "2",
            "--dry-run",
        ]
        pipeline_run()

        # Expect translation artifact and site output
        assert Path("data/translated/test-1.json").exists()
        assert Path("site/index.html").exists()

        summary = _load_summary()
        assert summary["successes"] == 1
        assert summary["attempted"] == 1
    finally:
        _restore_tmp(tmp, cwd)


def test_pipeline_records_merge_and_limit(monkeypatch):
    tmp, cwd = _chdir_tmp()
    try:
        # Prepare two records files with adequate content to pass QA
        os.makedirs("data/records", exist_ok=True)
        rec_a = [
            {
                "id": "test-1",
                "title": "A Study on Machine Learning Applications in Physics",
                "abstract": "This paper presents a comprehensive study on machine learning applications in particle physics. We demonstrate how neural networks can improve beam correction accuracy significantly.",
                "license": {"raw": ""},
            },
        ]
        rec_b = [
            {
                "id": "test-2",
                "title": "Advanced Deep Learning Methods for Scientific Computing",
                "abstract": "This research explores advanced deep learning methods for scientific computing. Our approach demonstrates significant improvements in computational efficiency and accuracy across multiple domains.",
                "license": {"raw": ""},
            },
        ]
        with open("data/records/a.json", "w", encoding="utf-8") as f:
            json.dump(rec_a, f, ensure_ascii=False)
        with open("data/records/b.json", "w", encoding="utf-8") as f:
            json.dump(rec_b, f, ensure_ascii=False)

        # Stub os.system to short-circuit render/index/pdf
        import os as _os
        def _fake_system(cmd: str) -> int:
            if "src.render" in cmd or "src.search_index" in cmd or "src.make_pdf" in cmd:
                Path("site").mkdir(parents=True, exist_ok=True)
                (Path("site")/"index.html").write_text("<html></html>", encoding="utf-8")
                return 0
            if "src.select_and_fetch" in cmd:
                Path("data").mkdir(parents=True, exist_ok=True)
                (Path("data") / "selected.json").write_text(
                    json.dumps([rec_a[0]], ensure_ascii=False), encoding="utf-8"
                )
                return 0
            return 0
        monkeypatch.setattr(_os, "system", _fake_system)

        # Run pipeline with explicit records merge and limit 1
        import sys
        sys.argv = [
            "pipeline",
            "--records",
            "data/records/a.json,data/records/b.json",
            "--limit",
            "1",
            "--workers",
            "1",
            "--dry-run",
        ]
        pipeline_run()

        # Should have at least one translated file
        out_dir = Path("data/translated")
        files = list(out_dir.glob("*.json")) if out_dir.exists() else []
        assert len(files) == 1
        assert Path("site/index.html").exists()
    finally:
        _restore_tmp(tmp, cwd)


def test_pipeline_qa_summary_counts(monkeypatch):
    tmp, cwd = _chdir_tmp()
    try:
        os.makedirs("data", exist_ok=True)
        selected = [
            {"id": "qa-pass", "title": "A", "abstract": "B", "license": {"raw": ""}},
            {"id": "qa-flag", "title": "C", "abstract": "D", "license": {"raw": ""}},
        ]
        with open("data/selected.json", "w", encoding="utf-8") as f:
            json.dump(selected, f, ensure_ascii=False)

        # Create translation artifacts without hitting external services
        from src import pipeline as pipeline_mod
        from src.services.translation_service import TranslationService
        from src import qa_filter
        from types import SimpleNamespace

        def fake_translate_paper(self, paper_id: str, dry_run: bool = False, with_full_text: bool = True) -> str:
            out_dir = Path("data/translated")
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{paper_id}.json"
            payload = {
                "id": paper_id,
                "title_en": f"{paper_id} title",
                "abstract_en": f"{paper_id} abstract",
                "body_en": ["paragraph"],
            }
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            return str(path)

        def fake_filter(translation, *, save_passed: bool, save_flagged: bool):
            status = "pass" if translation["id"] == "qa-pass" else "flag_chinese"
            qa_passed = status == "pass"
            result = SimpleNamespace(
                status=SimpleNamespace(value=status),
                score=0.99 if qa_passed else 0.2,
                issues=[] if qa_passed else ["contains Chinese"],
                chinese_chars=[],
                chinese_ratio=0.0,
                flagged_fields=[],
            )
            return qa_passed, result

        messages: list[str] = []

        def fake_log(message: str) -> None:
            messages.append(message)

        # Stub os.system to avoid invoking external modules
        import os as _os

        def fake_system(cmd: str) -> int:
            if "src.render" in cmd or "src.search_index" in cmd or "src.make_pdf" in cmd:
                Path("site").mkdir(parents=True, exist_ok=True)
                (Path("site") / "index.html").write_text("<html></html>", encoding="utf-8")
            return 0

        monkeypatch.setattr(_os, "system", fake_system)
        monkeypatch.setattr(TranslationService, "translate_paper", fake_translate_paper, raising=False)
        monkeypatch.setattr(qa_filter, "filter_translation_file", fake_filter)
        monkeypatch.setattr(pipeline_mod, "log", fake_log)

        import sys

        old_argv = sys.argv[:]
        sys.argv = [
            "pipeline",
            "--skip-selection",
            "--workers",
            "1",
            "--with-qa",
        ]
        try:
            pipeline_run()
        finally:
            sys.argv = old_argv

        joined = "\n".join(messages)
        assert "Passed: 1" in joined
        assert "Flagged: 1" in joined

        summary = _load_summary()
        assert summary["qa_passed"] == 1
        assert summary["qa_flagged"] == 1
    finally:
        _restore_tmp(tmp, cwd)


def test_pipeline_selection_missing_output(monkeypatch):
    tmp, cwd = _chdir_tmp()
    try:
        os.makedirs("data/records", exist_ok=True)
        records = [{"id": "x1", "title": "T", "abstract": "A", "license": {"raw": ""}}]
        with open("data/records/a.json", "w", encoding="utf-8") as f:
            json.dump(records, f)

        import os as _os

        def fake_system(cmd: str) -> int:
            # Pretend select_and_fetch succeeded but do not create output
            return 0

        monkeypatch.setattr(_os, "system", fake_system)

        import sys

        sys.argv = [
            "pipeline",
            "--records",
            "data/records/a.json",
            "--limit",
            "1",
            "--dry-run",
        ]

        with pytest.raises(SystemExit):
            pipeline_run()

        summary = _load_summary()
        assert summary["selection_status"] in {"missing-output", "empty", "unreadable"}
    finally:
        _restore_tmp(tmp, cwd)


def test_pipeline_allow_empty_selection(monkeypatch):
    tmp, cwd = _chdir_tmp()
    try:
        os.makedirs("data/records", exist_ok=True)
        records = [{"id": "x1", "title": "T", "abstract": "A", "license": {"raw": ""}}]
        with open("data/records/a.json", "w", encoding="utf-8") as f:
            json.dump(records, f)

        import os as _os

        def fake_system(cmd: str) -> int:
            if "src.select_and_fetch" in cmd:
                Path("data").mkdir(parents=True, exist_ok=True)
                (Path("data") / "selected.json").write_text("[]", encoding="utf-8")
            return 0

        monkeypatch.setattr(_os, "system", fake_system)

        import sys

        sys.argv = [
            "pipeline",
            "--records",
            "data/records/a.json",
            "--allow-empty-selection",
            "--dry-run",
        ]
        pipeline_run()

        summary = _load_summary()
        assert summary["attempted"] == 0
        assert summary["selection_status"] == "complete"
    finally:
        _restore_tmp(tmp, cwd)
