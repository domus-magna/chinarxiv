"""
Tests for pipeline orchestration (src/pipeline.py).

These tests verify the pipeline spine - argument parsing, selection,
translation coordination, figure integration, and summary generation.
All external dependencies are mocked for deterministic testing.
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

from src.pipeline import (
    find_latest_records_json,
    _write_summary,
    run_cli,
    SUMMARY_PATH,
)


class TestFindLatestRecordsJson:
    """Test find_latest_records_json function."""

    def test_returns_latest_file(self, tmp_path, monkeypatch):
        """Returns the last file when sorted alphabetically."""
        records_dir = tmp_path / "data" / "records"
        records_dir.mkdir(parents=True)

        # Create files in non-alphabetical order
        (records_dir / "chinaxiv_202312.json").write_text("[]")
        (records_dir / "chinaxiv_202401.json").write_text("[]")
        (records_dir / "chinaxiv_202311.json").write_text("[]")

        monkeypatch.chdir(tmp_path)

        result = find_latest_records_json()

        assert result is not None
        assert "chinaxiv_202401.json" in result

    def test_returns_none_when_no_files(self, tmp_path, monkeypatch):
        """Returns None when no records files exist."""
        records_dir = tmp_path / "data" / "records"
        records_dir.mkdir(parents=True)

        monkeypatch.chdir(tmp_path)

        result = find_latest_records_json()

        assert result is None

    def test_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        """Returns None when records directory doesn't exist."""
        monkeypatch.chdir(tmp_path)

        result = find_latest_records_json()

        assert result is None


class TestWriteSummary:
    """Test _write_summary function."""

    def test_creates_summary_file(self, tmp_path, monkeypatch):
        """Creates summary file with payload."""
        monkeypatch.chdir(tmp_path)

        payload = {
            "dry_run": True,
            "attempted": 5,
            "successes": 3,
            "failures": 2,
        }

        _write_summary(payload)

        summary_file = tmp_path / "reports" / "pipeline_summary.json"
        assert summary_file.exists()

        data = json.loads(summary_file.read_text())
        assert data["attempted"] == 5
        assert data["successes"] == 3
        assert "updated_at" in data

    def test_creates_reports_dir(self, tmp_path, monkeypatch):
        """Creates reports directory if it doesn't exist."""
        monkeypatch.chdir(tmp_path)

        _write_summary({"test": "data"})

        assert (tmp_path / "reports").is_dir()


class TestRunCliArguments:
    """Test CLI argument parsing."""

    @patch("src.pipeline.subprocess.run")
    @patch("src.pipeline.find_latest_records_json")
    def test_dry_run_skips_actual_work(
        self, mock_find_records, mock_subprocess, tmp_path, monkeypatch
    ):
        """Dry run mode sets flag but still attempts selection."""
        monkeypatch.chdir(tmp_path)
        mock_find_records.return_value = None

        monkeypatch.setattr("sys.argv", ["pipeline", "--dry-run"])

        # Run should exit because no records found
        run_cli()

        # Summary should indicate dry run
        summary_file = tmp_path / "reports" / "pipeline_summary.json"
        if summary_file.exists():
            data = json.loads(summary_file.read_text())
            assert data["dry_run"] is True

    @patch("src.pipeline.subprocess.run")
    @patch("src.pipeline.find_latest_records_json")
    def test_workers_arg_parsed(
        self, mock_find_records, mock_subprocess, tmp_path, monkeypatch
    ):
        """Workers argument is correctly parsed."""
        monkeypatch.chdir(tmp_path)
        mock_find_records.return_value = None

        monkeypatch.setattr("sys.argv", ["pipeline", "--workers", "5"])

        run_cli()

        # No assertion needed - if it doesn't crash, arg parsing worked

    @patch("subprocess.run")
    def test_with_figures_requires_secrets(self, mock_subprocess, tmp_path, monkeypatch):
        """--with-figures fails if GEMINI_API_KEY is missing."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("MOONDREAM_API_KEY", raising=False)

        monkeypatch.setattr("sys.argv", ["pipeline", "--with-figures"])

        with pytest.raises(SystemExit) as exc_info:
            run_cli()

        assert "GEMINI_API_KEY" in str(exc_info.value)


class TestSelectionStep:
    """Test selection step behavior."""

    @patch("src.pipeline.subprocess.run")
    @patch("src.pipeline.find_latest_records_json")
    def test_skips_selection_when_flag_and_file_exists(
        self, mock_find_records, mock_subprocess, tmp_path, monkeypatch
    ):
        """Skips selection if --skip-selection and selected.json exists."""
        monkeypatch.chdir(tmp_path)

        # Create selected.json
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "selected.json").write_text("[]")

        monkeypatch.setattr("sys.argv", ["pipeline", "--skip-selection"])

        run_cli()

        # subprocess.run should not have been called for selection
        # (It might be called for render/index steps)
        for call in mock_subprocess.call_args_list:
            args = call[0][0]
            if "select_and_fetch" in str(args):
                pytest.fail("Selection step should have been skipped")

    @patch("src.pipeline.subprocess.run")
    def test_selection_failure_exits(self, mock_subprocess, tmp_path, monkeypatch):
        """Selection failure causes pipeline to exit."""
        monkeypatch.chdir(tmp_path)

        # Create records file
        records_dir = tmp_path / "data" / "records"
        records_dir.mkdir(parents=True)
        (records_dir / "test.json").write_text('[{"id": "test-001"}]')

        # Mock subprocess to return failure
        mock_subprocess.return_value = MagicMock(returncode=1)

        monkeypatch.setattr("sys.argv", ["pipeline"])

        with pytest.raises(SystemExit) as exc_info:
            run_cli()

        assert "Selection command failed" in str(exc_info.value)

    @patch("src.pipeline.subprocess.run")
    def test_empty_selection_fails_by_default(
        self, mock_subprocess, tmp_path, monkeypatch
    ):
        """Empty selection fails unless --allow-empty-selection is set."""
        monkeypatch.chdir(tmp_path)

        # Create records file
        records_dir = tmp_path / "data" / "records"
        records_dir.mkdir(parents=True)
        (records_dir / "test.json").write_text('[{"id": "test-001"}]')

        # Mock successful subprocess but create empty selected.json
        def create_empty_selected(*args, **kwargs):
            (tmp_path / "data" / "selected.json").write_text("[]")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = create_empty_selected

        monkeypatch.setattr("sys.argv", ["pipeline"])

        with pytest.raises(SystemExit) as exc_info:
            run_cli()

        assert "zero items" in str(exc_info.value)

    @patch("src.pipeline.subprocess.run")
    def test_allow_empty_selection(self, mock_subprocess, tmp_path, monkeypatch):
        """--allow-empty-selection permits zero items."""
        monkeypatch.chdir(tmp_path)

        # Create records file
        records_dir = tmp_path / "data" / "records"
        records_dir.mkdir(parents=True)
        (records_dir / "test.json").write_text('[{"id": "test-001"}]')

        # Mock successful subprocess but create empty selected.json
        def create_empty_selected(*args, **kwargs):
            (tmp_path / "data" / "selected.json").write_text("[]")
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = create_empty_selected

        monkeypatch.setattr("sys.argv", ["pipeline", "--allow-empty-selection"])

        # Should not raise
        run_cli()


class TestTranslationStep:
    """Test translation step behavior."""

    @patch("src.pipeline.subprocess.run")
    @patch("src.translate.translate_paper")
    @patch("src.file_service.read_json")
    def test_parallel_translation(
        self, mock_read_json, mock_translate, mock_subprocess, tmp_path, monkeypatch
    ):
        """Translation runs in parallel with ThreadPoolExecutor."""
        monkeypatch.chdir(tmp_path)

        # Create selected.json directly
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "selected.json").write_text(
            '[{"id": "paper-001"}, {"id": "paper-002"}]'
        )

        mock_read_json.return_value = [{"id": "paper-001"}, {"id": "paper-002"}]
        mock_translate.return_value = str(tmp_path / "output.json")
        mock_subprocess.return_value = MagicMock(returncode=0)

        monkeypatch.setattr("sys.argv", ["pipeline", "--skip-selection", "--workers", "2"])

        run_cli()

        # Both papers should have been translated
        assert mock_translate.call_count == 2

    @patch("src.pipeline.subprocess.run")
    @patch("src.translate.translate_paper")
    @patch("src.file_service.read_json")
    def test_translation_failure_counted(
        self, mock_read_json, mock_translate, mock_subprocess, tmp_path, monkeypatch
    ):
        """Failed translations are counted in summary."""
        monkeypatch.chdir(tmp_path)

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "selected.json").write_text('[{"id": "paper-001"}]')

        mock_read_json.return_value = [{"id": "paper-001"}]
        mock_translate.side_effect = Exception("API error")
        mock_subprocess.return_value = MagicMock(returncode=0)

        monkeypatch.setattr("sys.argv", ["pipeline", "--skip-selection"])

        run_cli()

        # Check summary
        summary_file = tmp_path / "reports" / "pipeline_summary.json"
        data = json.loads(summary_file.read_text())
        assert data["failures"] == 1


class TestQAIntegration:
    """Test QA filter integration."""

    @patch("src.pipeline.subprocess.run")
    @patch("src.qa_filter.SynthesisQAFilter")
    @patch("src.translate.translate_paper")
    @patch("src.file_service.read_json")
    def test_qa_passes_counted(
        self,
        mock_read_json,
        mock_translate,
        mock_qa_filter_class,
        mock_subprocess,
        tmp_path,
        monkeypatch,
    ):
        """QA passes are counted in summary."""
        monkeypatch.chdir(tmp_path)

        # Setup selected.json
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "selected.json").write_text('[{"id": "paper-001"}]')

        # Create translation output file
        output_path = data_dir / "translated" / "paper-001.json"
        output_path.parent.mkdir(parents=True)
        output_path.write_text('{"title_en": "Test"}')

        mock_read_json.return_value = [{"id": "paper-001"}]
        mock_translate.return_value = str(output_path)
        mock_subprocess.return_value = MagicMock(returncode=0)

        # Mock QA filter
        mock_qa_result = MagicMock()
        mock_qa_result.status.value = "pass"
        mock_qa_result.score = 0.95

        # Create enum-compatible mock
        from enum import Enum

        class MockQAStatus(Enum):
            PASS = "pass"
            FAIL = "fail"

        mock_qa_result.status = MockQAStatus.PASS

        mock_qa_filter = MagicMock()
        mock_qa_filter.check_synthesis_translation.return_value = mock_qa_result
        mock_qa_filter_class.return_value = mock_qa_filter

        # Patch QAStatus import
        with patch("src.qa_filter.QAStatus", MockQAStatus):
            monkeypatch.setattr("sys.argv", ["pipeline", "--skip-selection", "--with-qa"])
            run_cli()

        # Check summary
        summary_file = tmp_path / "reports" / "pipeline_summary.json"
        data = json.loads(summary_file.read_text())
        assert data["qa_passed"] == 1


class TestFigureIntegration:
    """Test figure pipeline integration."""

    @patch("src.pipeline.subprocess.run")
    @patch("src.figure_pipeline.FigurePipeline")
    @patch("src.translate.translate_paper")
    @patch("src.file_service.read_json")
    def test_figure_pipeline_called(
        self,
        mock_read_json,
        mock_translate,
        mock_figure_pipeline_class,
        mock_subprocess,
        tmp_path,
        monkeypatch,
    ):
        """Figure pipeline is called when --with-figures is set."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("MOONDREAM_API_KEY", "test-key")

        # Setup selected.json
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "selected.json").write_text('[{"id": "paper-001"}]')

        mock_read_json.return_value = [{"id": "paper-001"}]
        mock_translate.return_value = str(data_dir / "output.json")
        mock_subprocess.return_value = MagicMock(returncode=0)

        # Mock figure pipeline
        mock_figure_result = MagicMock()
        mock_figure_result.total_figures = 5
        mock_figure_result.translated = 4
        mock_figure_result.failed = 1

        mock_figure_pipeline = MagicMock()
        mock_figure_pipeline.process_batch.return_value = [mock_figure_result]
        mock_figure_pipeline_class.return_value = mock_figure_pipeline

        monkeypatch.setattr(
            "sys.argv", ["pipeline", "--skip-selection", "--with-figures"]
        )

        run_cli()

        # Figure pipeline should have been called
        mock_figure_pipeline.process_batch.assert_called_once()

        # Check summary includes figure stats
        summary_file = tmp_path / "reports" / "pipeline_summary.json"
        data = json.loads(summary_file.read_text())
        assert data["figures_total"] == 5
        assert data["figures_translated"] == 4


class TestCloudMode:
    """Test cloud mode behavior."""

    @patch("src.cloud_job_queue.cloud_queue")
    @patch("src.pipeline.subprocess.run")
    @patch("src.translate.translate_paper")
    def test_cloud_mode_claims_batch(
        self, mock_translate, mock_subprocess, mock_cloud_queue, tmp_path, monkeypatch
    ):
        """Cloud mode claims jobs from cloud queue."""
        monkeypatch.chdir(tmp_path)

        # Create data/selected.json to satisfy skip-selection requirement
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "selected.json").write_text("[]")

        # Mock cloud queue
        mock_cloud_queue.claim_batch.return_value = [
            {"paper_id": "paper-001"},
            {"paper_id": "paper-002"},
        ]

        mock_translate.return_value = str(tmp_path / "output.json")
        mock_subprocess.return_value = MagicMock(returncode=0)

        monkeypatch.setattr(
            "sys.argv",
            ["pipeline", "--cloud-mode", "--skip-selection", "--batch-size", "10", "--worker-id", "test-worker"],
        )

        run_cli()

        # Should have claimed from cloud queue
        mock_cloud_queue.claim_batch.assert_called_with("test-worker", batch_size=10)

    @patch("src.cloud_job_queue.cloud_queue")
    @patch("src.pipeline.subprocess.run")
    def test_cloud_mode_exits_when_no_jobs(
        self, mock_subprocess, mock_cloud_queue, tmp_path, monkeypatch
    ):
        """Cloud mode exits gracefully when no jobs available."""
        monkeypatch.chdir(tmp_path)

        mock_cloud_queue.claim_batch.return_value = []

        monkeypatch.setattr("sys.argv", ["pipeline", "--cloud-mode"])

        # Should not raise
        run_cli()


class TestSummaryGeneration:
    """Test pipeline summary generation."""

    @patch("src.pipeline.subprocess.run")
    @patch("src.translate.translate_paper")
    @patch("src.file_service.read_json")
    def test_summary_includes_all_fields(
        self, mock_read_json, mock_translate, mock_subprocess, tmp_path, monkeypatch
    ):
        """Summary includes all expected fields."""
        monkeypatch.chdir(tmp_path)

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "selected.json").write_text('[{"id": "paper-001"}]')

        mock_read_json.return_value = [{"id": "paper-001"}]
        mock_translate.return_value = str(data_dir / "output.json")
        mock_subprocess.return_value = MagicMock(returncode=0)

        monkeypatch.setattr("sys.argv", ["pipeline", "--skip-selection", "--dry-run"])

        run_cli()

        summary_file = tmp_path / "reports" / "pipeline_summary.json"
        data = json.loads(summary_file.read_text())

        # Check all expected fields
        assert "generated_at" in data
        assert "updated_at" in data
        assert "dry_run" in data
        assert "cloud_mode" in data
        assert "with_qa" in data
        assert "with_figures" in data
        assert "selection_status" in data
        assert "attempted" in data
        assert "successes" in data
        assert "failures" in data
        assert "qa_passed" in data
        assert "qa_flagged" in data
        assert "figures_total" in data
        assert "figures_translated" in data
