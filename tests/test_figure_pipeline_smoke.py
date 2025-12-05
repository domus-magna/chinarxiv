"""
Smoke tests for figure pipeline (src/figure_pipeline/).

These tests verify the figure extraction, translation, and assembly pipeline
using mocked components. The goal is to test orchestration logic without
requiring actual PDF files, Gemini API, or B2 storage.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from src.figure_pipeline import (
    FigurePipeline,
    Figure,
    FigureProcessingResult,
    PipelineConfig,
    ProcessingStatus,
)
from src.figure_pipeline.models import FigureType, FigureLocation


class TestPipelineConfig:
    """Test PipelineConfig model."""

    def test_default_config(self):
        """Config has sensible defaults."""
        config = PipelineConfig()

        assert config.b2_bucket == "chinaxiv"
        assert config.max_figures_per_paper == 50
        assert config.skip_translation_if_no_chinese is True
        assert config.dry_run is False

    def test_config_with_api_keys(self):
        """Config accepts API keys."""
        config = PipelineConfig(
            gemini_api_key="test-gemini",
            moondream_api_key="test-moondream",
        )

        assert config.gemini_api_key == "test-gemini"
        assert config.moondream_api_key == "test-moondream"


class TestFigureModel:
    """Test Figure pydantic model."""

    def test_create_figure(self):
        """Create a basic figure."""
        fig = Figure(
            paper_id="paper-001",
            figure_number="1",
            figure_type=FigureType.FIGURE,
        )

        assert fig.paper_id == "paper-001"
        assert fig.figure_number == "1"
        assert fig.status == ProcessingStatus.PENDING

    def test_figure_with_location(self):
        """Figure with location info."""
        loc = FigureLocation(
            page_number=5,
            marker="[FIGURE:1]",
            section_title="Results",
        )
        fig = Figure(
            paper_id="paper-001",
            figure_number="1",
            figure_type=FigureType.FIGURE,
            location=loc,
        )

        assert fig.location.page_number == 5
        assert fig.location.marker == "[FIGURE:1]"


class TestFigureProcessingResult:
    """Test FigureProcessingResult model."""

    def test_empty_result(self):
        """Empty result has zero counts."""
        result = FigureProcessingResult(paper_id="paper-001")

        assert result.total_figures == 0
        assert result.success_rate == 0.0

    def test_success_rate_calculation(self):
        """Success rate is translated/total."""
        result = FigureProcessingResult(
            paper_id="paper-001",
            total_figures=10,
            translated=8,
        )

        assert result.success_rate == 0.8


class TestFigurePipelineInit:
    """Test FigurePipeline initialization."""

    def test_lazy_load_components(self):
        """Components are lazy-loaded."""
        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        # Internal components should be None initially
        assert pipeline._extractor is None
        assert pipeline._translator is None
        assert pipeline._validator is None
        assert pipeline._storage is None

    def test_default_config(self):
        """Pipeline uses default config if none provided."""
        pipeline = FigurePipeline()

        assert pipeline.config is not None
        assert pipeline.config.dry_run is False


class TestFindPdf:
    """Test PDF finding logic."""

    def test_find_pdf_validates_paper_id(self, tmp_path):
        """Rejects invalid paper IDs (path traversal prevention)."""
        config = PipelineConfig(pdf_dir=str(tmp_path))
        pipeline = FigurePipeline(config)

        # Path traversal attempt
        result = pipeline._find_pdf("../../../etc/passwd")
        assert result is None

        # Slashes in ID
        result = pipeline._find_pdf("paper/001")
        assert result is None

    def test_find_pdf_returns_path(self, tmp_path):
        """Returns path when PDF exists."""
        pdf_file = tmp_path / "paper-001.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        config = PipelineConfig(pdf_dir=str(tmp_path))
        pipeline = FigurePipeline(config)

        result = pipeline._find_pdf("paper-001")

        assert result is not None
        assert "paper-001.pdf" in result

    def test_find_pdf_returns_none_when_missing(self, tmp_path):
        """Returns None when PDF not found."""
        config = PipelineConfig(pdf_dir=str(tmp_path))
        pipeline = FigurePipeline(config)

        result = pipeline._find_pdf("nonexistent-paper")

        assert result is None


class TestProcessPaper:
    """Test process_paper orchestration."""

    @patch.object(FigurePipeline, "_find_pdf")
    def test_returns_empty_when_no_pdf(self, mock_find_pdf):
        """Returns empty result when PDF not found."""
        mock_find_pdf.return_value = None

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        result = pipeline.process_paper("nonexistent")

        assert result.paper_id == "nonexistent"
        assert result.total_figures == 0

    @patch.object(FigurePipeline, "_find_pdf")
    @patch.object(FigurePipeline, "extractor", new_callable=PropertyMock)
    def test_returns_empty_when_no_figures(self, mock_extractor_prop, mock_find_pdf):
        """Returns empty result when no figures extracted."""
        mock_find_pdf.return_value = "/tmp/paper.pdf"

        mock_extractor = MagicMock()
        mock_extractor.extract_all.return_value = []
        mock_extractor_prop.return_value = mock_extractor

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        result = pipeline.process_paper("paper-001")

        assert result.total_figures == 0

    @patch.object(FigurePipeline, "_find_pdf")
    @patch.object(FigurePipeline, "extractor", new_callable=PropertyMock)
    @patch.object(FigurePipeline, "validator", new_callable=PropertyMock)
    @patch.object(FigurePipeline, "translator", new_callable=PropertyMock)
    @patch.object(FigurePipeline, "storage", new_callable=PropertyMock)
    def test_full_pipeline_flow(
        self,
        mock_storage_prop,
        mock_translator_prop,
        mock_validator_prop,
        mock_extractor_prop,
        mock_find_pdf,
        tmp_path,
    ):
        """Test full pipeline with mocked components."""
        # Setup PDF
        pdf_path = tmp_path / "paper-001.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")
        mock_find_pdf.return_value = str(pdf_path)

        # Setup extractor
        mock_extractor = MagicMock()
        fig1 = Figure(
            paper_id="paper-001",
            figure_number="1",
            figure_type=FigureType.FIGURE,
            status=ProcessingStatus.EXTRACTED,
            original_path=str(tmp_path / "fig1.png"),
        )
        (tmp_path / "fig1.png").write_bytes(b"PNG")
        mock_extractor.extract_all.return_value = [fig1]
        mock_extractor_prop.return_value = mock_extractor

        # Setup validator
        mock_validator = MagicMock()
        mock_validator.validate.return_value = {
            "readable": True,
            "has_chinese": True,
            "figure_type": "chart",
        }
        mock_validator.qa_translation.return_value = {
            "passed": True,
            "differences": "",
        }
        mock_validator_prop.return_value = mock_validator

        # Setup translator
        mock_translator = MagicMock()
        mock_translator.translate.return_value = str(tmp_path / "fig1_en.png")
        mock_translator_prop.return_value = mock_translator

        # Setup storage (dry run, so won't be called for upload)
        mock_storage = MagicMock()
        mock_storage_prop.return_value = mock_storage

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        result = pipeline.process_paper("paper-001")

        # Verify flow
        assert result.total_figures == 1
        mock_extractor.extract_all.assert_called_once()
        mock_validator.validate.assert_called_once()
        mock_translator.translate.assert_called_once()
        # Storage not called in dry run
        mock_storage.upload.assert_not_called()


class TestProcessBatch:
    """Test batch processing."""

    @patch.object(FigurePipeline, "process_paper")
    def test_processes_multiple_papers(self, mock_process):
        """Processes multiple papers in parallel."""
        mock_process.return_value = FigureProcessingResult(
            paper_id="test",
            total_figures=2,
            translated=2,
        )

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        results = pipeline.process_batch(["paper-001", "paper-002"], workers=2)

        assert len(results) == 2

    @patch.object(FigurePipeline, "process_paper")
    def test_handles_errors_gracefully(self, mock_process):
        """Handles errors without crashing batch."""
        mock_process.side_effect = [
            FigureProcessingResult(paper_id="paper-001", translated=1),
            Exception("API error"),
        ]

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        results = pipeline.process_batch(["paper-001", "paper-002"], workers=1)

        # Both should have results (error case gets failed result)
        assert len(results) == 2


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration."""

    @patch("src.figure_pipeline.get_circuit_breaker")
    def test_checks_circuit_breaker(self, mock_get_cb):
        """Process paper checks circuit breaker first."""
        mock_cb = MagicMock()
        mock_get_cb.return_value = mock_cb

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        # This will fail after CB check since PDF won't be found
        pipeline.process_paper("test")

        mock_cb.check.assert_called_once()

    @patch("src.figure_pipeline.get_circuit_breaker")
    def test_raises_when_circuit_open(self, mock_get_cb):
        """Raises when circuit breaker is open."""
        # Circuit breaker raises RuntimeError, not a custom exception
        mock_cb = MagicMock()
        mock_cb.check.side_effect = RuntimeError("Circuit breaker open: Rate limited")
        mock_get_cb.return_value = mock_cb

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        with pytest.raises(RuntimeError) as exc_info:
            pipeline.process_paper("test")

        assert "Circuit breaker open" in str(exc_info.value)


class TestRateLimiterIntegration:
    """Test rate limiter integration in parallel translation."""

    def test_rate_limiter_module_exists(self):
        """Rate limiter module can be imported."""
        from src.figure_pipeline.rate_limiter import get_rate_limiter

        rl = get_rate_limiter()
        assert rl is not None
        assert rl.get_concurrent() > 0


class TestEnvironmentConcurrency:
    """Test concurrency from environment variables."""

    @patch.dict(os.environ, {"FIGURE_CONCURRENT": "4"})
    @patch.object(FigurePipeline, "_find_pdf")
    def test_reads_concurrent_from_env(self, mock_find_pdf):
        """Reads concurrency limit from FIGURE_CONCURRENT env var."""
        mock_find_pdf.return_value = None  # Skip actual processing

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        # Just verify it doesn't crash with env var set
        result = pipeline.process_paper("test", max_concurrent_figures=8)

        assert result.paper_id == "test"

    @patch.dict(os.environ, {"FIGURE_CONCURRENT": "invalid"})
    @patch.object(FigurePipeline, "_find_pdf")
    def test_handles_invalid_concurrent_env(self, mock_find_pdf):
        """Handles invalid FIGURE_CONCURRENT gracefully."""
        mock_find_pdf.return_value = None

        config = PipelineConfig(dry_run=True)
        pipeline = FigurePipeline(config)

        # Should use default, not crash
        result = pipeline.process_paper("test", max_concurrent_figures=8)

        assert result.paper_id == "test"


class TestUploadFlow:
    """Test upload flow (non-dry-run)."""

    @patch.object(FigurePipeline, "_find_pdf")
    @patch.object(FigurePipeline, "extractor", new_callable=PropertyMock)
    @patch.object(FigurePipeline, "validator", new_callable=PropertyMock)
    @patch.object(FigurePipeline, "translator", new_callable=PropertyMock)
    @patch.object(FigurePipeline, "storage", new_callable=PropertyMock)
    def test_uploads_when_not_dry_run(
        self,
        mock_storage_prop,
        mock_translator_prop,
        mock_validator_prop,
        mock_extractor_prop,
        mock_find_pdf,
        tmp_path,
    ):
        """Uploads to B2 when not in dry run mode."""
        mock_find_pdf.return_value = str(tmp_path / "test.pdf")
        (tmp_path / "test.pdf").write_bytes(b"%PDF")

        # Setup figure
        fig = Figure(
            paper_id="test",
            figure_number="1",
            figure_type=FigureType.FIGURE,
            status=ProcessingStatus.EXTRACTED,
            original_path=str(tmp_path / "fig.png"),
        )
        (tmp_path / "fig.png").write_bytes(b"PNG")

        mock_extractor = MagicMock()
        mock_extractor.extract_all.return_value = [fig]
        mock_extractor_prop.return_value = mock_extractor

        mock_validator = MagicMock()
        mock_validator.validate.return_value = {"readable": True, "has_chinese": True}
        mock_validator.qa_translation.return_value = {"passed": True}
        mock_validator_prop.return_value = mock_validator

        translated_path = str(tmp_path / "fig_en.png")
        (tmp_path / "fig_en.png").write_bytes(b"PNG")
        mock_translator = MagicMock()
        mock_translator.translate.return_value = translated_path
        mock_translator_prop.return_value = mock_translator

        mock_storage = MagicMock()
        mock_storage.upload.return_value = "https://b2.example.com/fig.png"
        mock_storage.update_manifest.return_value = True
        mock_storage_prop.return_value = mock_storage

        # NOT dry run
        config = PipelineConfig(dry_run=False)
        pipeline = FigurePipeline(config)

        result = pipeline.process_paper("test")

        # Storage should be called
        assert mock_storage.upload.call_count >= 1
        mock_storage.update_manifest.assert_called_once()
        assert result.uploaded >= 1
