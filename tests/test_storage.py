"""
Tests for B2 storage operations (src/figure_pipeline/storage.py).

These tests verify upload, download, and manifest management with
mocked B2 SDK to avoid actual cloud operations.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from src.figure_pipeline.storage import FigureStorage
from src.figure_pipeline.models import PipelineConfig


class TestFigureStorageInit:
    """Test FigureStorage initialization."""

    def test_default_config(self):
        """Uses default config if none provided."""
        storage = FigureStorage()

        assert storage.config is not None
        assert storage._bucket is None  # Lazy loaded

    def test_with_config(self):
        """Accepts custom config."""
        config = PipelineConfig(b2_bucket="custom-bucket")
        storage = FigureStorage(config)

        assert storage.config.b2_bucket == "custom-bucket"


class TestBucketLazyLoad:
    """Test lazy loading of B2 bucket."""

    def test_bucket_is_none_initially(self):
        """Bucket is None before accessed (lazy loading)."""
        storage = FigureStorage()
        assert storage._bucket is None

    @patch.dict(os.environ, {}, clear=True)
    def test_raises_without_credentials(self):
        """Raises ValueError when B2 credentials missing."""
        # Clear any existing env vars
        for key in ["B2_KEY_ID", "B2_APP_KEY", "BACKBLAZE_KEY_ID", "BACKBLAZE_APPLICATION_KEY"]:
            os.environ.pop(key, None)

        storage = FigureStorage()

        with pytest.raises(ValueError) as exc_info:
            _ = storage.bucket

        assert "B2_KEY_ID" in str(exc_info.value)


class TestPublicBaseUrl:
    """Test public URL generation."""

    @patch.dict(os.environ, {"BACKBLAZE_S3_ENDPOINT": "https://s3.us-west-004.backblazeb2.com"})
    def test_extracts_region_from_endpoint(self):
        """Extracts region suffix from S3 endpoint."""
        storage = FigureStorage()
        storage._bucket = MagicMock()
        storage._bucket.name = "chinaxiv"

        url = storage._get_public_base_url()

        assert "f004" in url
        assert "chinaxiv" in url

    @patch.dict(os.environ, {"BACKBLAZE_S3_ENDPOINT": "https://s3.eu-central-003.backblazeb2.com"})
    def test_handles_different_regions(self):
        """Handles different B2 regions."""
        storage = FigureStorage()
        storage._bucket = MagicMock()
        storage._bucket.name = "test-bucket"

        url = storage._get_public_base_url()

        assert "f003" in url

    @patch.dict(os.environ, {}, clear=True)
    def test_fallback_url(self):
        """Falls back to f004 when endpoint not set."""
        storage = FigureStorage()
        storage._bucket = MagicMock()
        storage._bucket.name = "chinaxiv"

        url = storage._get_public_base_url()

        assert "f004" in url


class TestUpload:
    """Test file upload."""

    def test_upload_returns_none_when_file_missing(self, tmp_path):
        """Returns None when local file doesn't exist."""
        storage = FigureStorage()

        result = storage.upload("/nonexistent/file.png", "test/file.png")

        assert result is None

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_upload_returns_url(self, mock_bucket_prop, tmp_path):
        """Returns public URL on successful upload."""
        # Create test file
        test_file = tmp_path / "test.png"
        test_file.write_bytes(b"PNG data")

        mock_bucket = MagicMock()
        mock_bucket.name = "chinaxiv"
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()
        storage._get_public_base_url = MagicMock(
            return_value="https://f004.backblazeb2.com/file/chinaxiv"
        )

        result = storage.upload(str(test_file), "figures/test.png")

        assert result is not None
        assert "figures/test.png" in result
        mock_bucket.upload_local_file.assert_called_once()

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_upload_handles_error(self, mock_bucket_prop, tmp_path):
        """Returns None and logs error on failure."""
        test_file = tmp_path / "test.png"
        test_file.write_bytes(b"PNG")

        mock_bucket = MagicMock()
        mock_bucket.upload_local_file.side_effect = Exception("Upload failed")
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        result = storage.upload(str(test_file), "test.png")

        assert result is None


class TestDownload:
    """Test file download."""

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_download_saves_file(self, mock_bucket_prop, tmp_path):
        """Downloads file and saves locally."""
        mock_bucket = MagicMock()
        mock_downloaded = MagicMock()
        mock_bucket.download_file_by_name.return_value = mock_downloaded
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()
        local_path = str(tmp_path / "downloaded.png")

        result = storage.download("figures/test.png", local_path)

        assert result is True
        mock_bucket.download_file_by_name.assert_called_with("figures/test.png")
        mock_downloaded.save_to.assert_called_with(local_path)

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_download_creates_dirs(self, mock_bucket_prop, tmp_path):
        """Creates parent directories for download."""
        mock_bucket = MagicMock()
        mock_bucket.download_file_by_name.return_value = MagicMock()
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()
        local_path = str(tmp_path / "nested" / "dir" / "file.png")

        storage.download("test.png", local_path)

        assert (tmp_path / "nested" / "dir").is_dir()

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_download_handles_error(self, mock_bucket_prop):
        """Returns False on download error."""
        mock_bucket = MagicMock()
        mock_bucket.download_file_by_name.side_effect = Exception("Not found")
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        result = storage.download("nonexistent.png", "/tmp/test.png")

        assert result is False


class TestExists:
    """Test file existence check."""

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_exists_returns_true(self, mock_bucket_prop):
        """Returns True when file exists."""
        mock_bucket = MagicMock()
        mock_bucket.get_file_info_by_name.return_value = MagicMock()
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        assert storage.exists("figures/test.png") is True

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_exists_returns_false(self, mock_bucket_prop):
        """Returns False when file doesn't exist."""
        mock_bucket = MagicMock()
        mock_bucket.get_file_info_by_name.side_effect = Exception("Not found")
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        assert storage.exists("nonexistent.png") is False


class TestListFigures:
    """Test listing figures for a paper."""

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_lists_original_and_translated(self, mock_bucket_prop):
        """Separates original and translated figures."""
        mock_bucket = MagicMock()
        mock_bucket.name = "chinaxiv"

        # Mock file listing
        mock_file1 = MagicMock()
        mock_file1.file_name = "figures/paper-001/original/fig_1.png"
        mock_file2 = MagicMock()
        mock_file2.file_name = "figures/paper-001/translated/fig_1.png"

        mock_bucket.ls.return_value = [(mock_file1, None), (mock_file2, None)]
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()
        storage._get_public_base_url = MagicMock(
            return_value="https://f004.backblazeb2.com/file/chinaxiv"
        )

        result = storage.list_figures("paper-001")

        assert len(result["original"]) == 1
        assert len(result["translated"]) == 1
        assert "original" in result["original"][0]
        assert "translated" in result["translated"][0]

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_handles_empty_list(self, mock_bucket_prop):
        """Returns empty lists when no figures."""
        mock_bucket = MagicMock()
        mock_bucket.ls.return_value = []
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        result = storage.list_figures("paper-001")

        assert result == {"original": [], "translated": []}


class TestDeleteFigures:
    """Test figure deletion."""

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_deletes_all_figures(self, mock_bucket_prop):
        """Deletes all figures for a paper."""
        mock_bucket = MagicMock()

        mock_file1 = MagicMock()
        mock_file1.id_ = "file-1"
        mock_file1.file_name = "figures/paper-001/fig_1.png"
        mock_file2 = MagicMock()
        mock_file2.id_ = "file-2"
        mock_file2.file_name = "figures/paper-001/fig_2.png"

        mock_bucket.ls.return_value = [(mock_file1, None), (mock_file2, None)]
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        count = storage.delete_figures("paper-001")

        assert count == 2
        assert mock_bucket.delete_file_version.call_count == 2


class TestManifestManagement:
    """Test manifest download/upload/update."""

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_download_manifest(self, mock_bucket_prop, tmp_path):
        """Downloads and parses manifest JSON."""
        mock_bucket = MagicMock()

        # Mock download that writes to temp file
        def mock_save(path):
            with open(path, "w") as f:
                json.dump({"papers": {"paper-001": {"figure_count": 2}}}, f)

        mock_downloaded = MagicMock()
        mock_downloaded.save_to = mock_save
        mock_bucket.download_file_by_name.return_value = mock_downloaded
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        manifest = storage.download_manifest()

        assert manifest is not None
        assert "papers" in manifest
        assert "paper-001" in manifest["papers"]

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_download_manifest_handles_missing(self, mock_bucket_prop):
        """Returns None when manifest doesn't exist."""
        mock_bucket = MagicMock()
        mock_bucket.download_file_by_name.side_effect = Exception("Not found")
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        manifest = storage.download_manifest()

        assert manifest is None

    @patch.object(FigureStorage, "bucket", new_callable=PropertyMock)
    def test_upload_manifest(self, mock_bucket_prop):
        """Uploads manifest JSON."""
        mock_bucket = MagicMock()
        mock_bucket_prop.return_value = mock_bucket

        storage = FigureStorage()

        result = storage.upload_manifest({"papers": {"test": {"figure_count": 1}}})

        assert result is True
        mock_bucket.upload_local_file.assert_called_once()

    @patch.object(FigureStorage, "download_manifest")
    @patch.object(FigureStorage, "upload_manifest")
    def test_update_manifest_adds_paper(self, mock_upload, mock_download):
        """Updates manifest with new paper entry."""
        mock_download.return_value = {
            "updated_at": "2024-01-01",
            "papers": {"existing": {"figure_count": 1}},
        }
        mock_upload.return_value = True

        storage = FigureStorage()

        figures = [
            {"number": "1", "url": "https://example.com/fig1.png"},
            {"number": "2", "url": "https://example.com/fig2.png"},
        ]

        result = storage.update_manifest("new-paper", figures)

        assert result is True
        mock_upload.assert_called_once()

        # Check the manifest passed to upload
        uploaded_manifest = mock_upload.call_args[0][0]
        assert "new-paper" in uploaded_manifest["papers"]
        assert uploaded_manifest["papers"]["new-paper"]["figure_count"] == 2

    @patch.object(FigureStorage, "download_manifest")
    @patch.object(FigureStorage, "upload_manifest")
    def test_update_manifest_creates_new(self, mock_upload, mock_download):
        """Creates new manifest if none exists."""
        mock_download.return_value = None
        mock_upload.return_value = True

        storage = FigureStorage()

        result = storage.update_manifest("paper-001", [{"number": "1", "url": "url"}])

        assert result is True

        uploaded_manifest = mock_upload.call_args[0][0]
        assert "papers" in uploaded_manifest
        assert "updated_at" in uploaded_manifest


class TestEnvironmentVariables:
    """Test environment variable handling."""

    def test_config_has_default_bucket(self):
        """Config has default bucket name."""
        config = PipelineConfig()

        # Default bucket is "chinaxiv"
        assert config.b2_bucket == "chinaxiv"

    def test_storage_accepts_custom_bucket_config(self):
        """Storage accepts custom bucket name via config."""
        config = PipelineConfig(b2_bucket="my-custom-bucket")
        storage = FigureStorage(config)

        assert storage.config.b2_bucket == "my-custom-bucket"
