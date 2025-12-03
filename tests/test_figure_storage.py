"""Tests for figure pipeline storage module."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.figure_pipeline.models import PipelineConfig


class TestFigureStorageCredentials:
    """Tests for B2 credential resolution."""

    def test_missing_credentials_raises(self):
        """Verify ValueError when no credentials available."""
        with patch.dict(os.environ, {}, clear=True):
            from src.figure_pipeline.storage import FigureStorage

            storage = FigureStorage(PipelineConfig(b2_key_id=None, b2_app_key=None))
            # Reset any cached bucket
            storage._bucket = None
            storage._client = None

            with pytest.raises(
                ValueError, match="B2_KEY_ID and B2_APP_KEY must be set"
            ):
                _ = storage.bucket

    def test_credentials_from_b2_env(self):
        """Verify B2_* env vars are used."""
        with patch.dict(
            os.environ,
            {"B2_KEY_ID": "b2-key", "B2_APP_KEY": "b2-secret"},
            clear=True,
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo") , \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = MagicMock()

                from src.figure_pipeline.storage import FigureStorage

                storage = FigureStorage(PipelineConfig(b2_key_id=None, b2_app_key=None))
                storage._bucket = None
                storage._client = None
                _ = storage.bucket

                mock_api.authorize_account.assert_called_once_with(
                    "production", "b2-key", "b2-secret"
                )

    def test_credentials_from_backblaze_env(self):
        """Verify BACKBLAZE_* env vars are used as fallback."""
        with patch.dict(
            os.environ,
            {"BACKBLAZE_KEY_ID": "bb-key", "BACKBLAZE_APPLICATION_KEY": "bb-secret"},
            clear=True,
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo") , \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = MagicMock()

                from src.figure_pipeline.storage import FigureStorage

                storage = FigureStorage(PipelineConfig(b2_key_id=None, b2_app_key=None))
                storage._bucket = None
                storage._client = None
                _ = storage.bucket

                mock_api.authorize_account.assert_called_once_with(
                    "production", "bb-key", "bb-secret"
                )

    def test_b2_env_takes_precedence(self):
        """Verify B2_* env vars take precedence over BACKBLAZE_*."""
        with patch.dict(
            os.environ,
            {
                "B2_KEY_ID": "b2-key",
                "B2_APP_KEY": "b2-secret",
                "BACKBLAZE_KEY_ID": "bb-key",
                "BACKBLAZE_APPLICATION_KEY": "bb-secret",
            },
            clear=True,
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo") , \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = MagicMock()

                from src.figure_pipeline.storage import FigureStorage

                storage = FigureStorage(PipelineConfig(b2_key_id=None, b2_app_key=None))
                storage._bucket = None
                storage._client = None
                _ = storage.bucket

                # B2_* should be used, not BACKBLAZE_*
                mock_api.authorize_account.assert_called_once_with(
                    "production", "b2-key", "b2-secret"
                )


class TestFigureStorageBucket:
    """Tests for bucket name resolution."""

    def test_bucket_from_env_when_default(self):
        """Verify BACKBLAZE_BUCKET env var is used when config has default."""
        with patch.dict(
            os.environ,
            {
                "B2_KEY_ID": "key",
                "B2_APP_KEY": "secret",
                "BACKBLAZE_BUCKET": "my-custom-bucket",
            },
            clear=True,
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo") , \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = MagicMock()

                from src.figure_pipeline.storage import FigureStorage

                # Default config has b2_bucket="chinaxiv"
                storage = FigureStorage(PipelineConfig())
                storage._bucket = None
                storage._client = None
                _ = storage.bucket

                mock_api.get_bucket_by_name.assert_called_once_with("my-custom-bucket")

    def test_explicit_bucket_takes_precedence(self):
        """Verify explicit config bucket overrides env var."""
        with patch.dict(
            os.environ,
            {
                "B2_KEY_ID": "key",
                "B2_APP_KEY": "secret",
                "BACKBLAZE_BUCKET": "env-bucket",
            },
            clear=True,
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo") , \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = MagicMock()

                from src.figure_pipeline.storage import FigureStorage

                storage = FigureStorage(PipelineConfig(b2_bucket="explicit-bucket"))
                storage._bucket = None
                storage._client = None
                _ = storage.bucket

                mock_api.get_bucket_by_name.assert_called_once_with("explicit-bucket")


class TestFigureStorageUpload:
    """Tests for upload operations."""

    def test_upload_file_not_found(self):
        """Verify missing file returns None without exception."""
        from src.figure_pipeline.storage import FigureStorage

        storage = FigureStorage(PipelineConfig())

        result = storage.upload("/nonexistent/path/file.png", "remote/key")
        assert result is None

    def test_upload_success(self):
        """Verify successful upload returns public URL derived from S3 endpoint."""
        with patch.dict(
            os.environ,
            {
                "B2_KEY_ID": "key",
                "B2_APP_KEY": "secret",
                "BACKBLAZE_S3_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
            },
            clear=True,
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo"), \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_bucket = MagicMock()
                mock_bucket.name = "chinaxiv"
                mock_file_info = MagicMock()
                mock_file_info.id_ = "file_123"

                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = mock_bucket
                mock_bucket.upload_local_file.return_value = mock_file_info

                from src.figure_pipeline.storage import FigureStorage

                storage = FigureStorage(PipelineConfig())
                storage._bucket = None
                storage._client = None

                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
                    f.write(b"fake image data")
                    temp_path = f.name

                try:
                    result = storage.upload(temp_path, "figures/test/fig_1.png")
                    # URL should use public format derived from S3 endpoint
                    assert result == "https://f004.backblazeb2.com/file/chinaxiv/figures/test/fig_1.png"
                    mock_bucket.upload_local_file.assert_called_once()
                finally:
                    os.unlink(temp_path)

    def test_upload_b2_error_returns_none(self):
        """Verify B2 error returns None (does not raise)."""
        with patch.dict(
            os.environ, {"B2_KEY_ID": "key", "B2_APP_KEY": "secret"}, clear=True
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo") , \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_bucket = MagicMock()

                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = mock_bucket
                mock_bucket.upload_local_file.side_effect = Exception("B2 upload failed")

                from src.figure_pipeline.storage import FigureStorage

                storage = FigureStorage(PipelineConfig())
                storage._bucket = None
                storage._client = None

                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
                    f.write(b"fake image data")
                    temp_path = f.name

                try:
                    result = storage.upload(temp_path, "figures/test/fig_1.png")
                    assert result is None  # Should not raise, just return None
                finally:
                    os.unlink(temp_path)


class TestFigureStorageManifest:
    """Tests for manifest operations."""

    def test_download_manifest_not_found(self):
        """Verify missing manifest returns None."""
        with patch.dict(
            os.environ, {"B2_KEY_ID": "key", "B2_APP_KEY": "secret"}, clear=True
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo") , \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_bucket = MagicMock()

                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = mock_bucket
                mock_bucket.download_file_by_name.side_effect = Exception(
                    "File not found"
                )

                from src.figure_pipeline.storage import FigureStorage

                storage = FigureStorage(PipelineConfig())
                storage._bucket = None
                storage._client = None
                result = storage.download_manifest()

                assert result is None

    def test_update_manifest_creates_new(self):
        """Verify update_manifest creates new manifest if none exists."""
        with patch.dict(
            os.environ, {"B2_KEY_ID": "key", "B2_APP_KEY": "secret"}, clear=True
        ):
            with patch("b2sdk.v2.InMemoryAccountInfo") , \
                 patch("b2sdk.v2.B2Api") as mock_api_class:
                mock_api = MagicMock()
                mock_bucket = MagicMock()

                mock_api_class.return_value = mock_api
                mock_api.get_bucket_by_name.return_value = mock_bucket

                # Simulate no existing manifest
                mock_bucket.download_file_by_name.side_effect = Exception("Not found")
                mock_bucket.upload_local_file.return_value = MagicMock()

                from src.figure_pipeline.storage import FigureStorage

                storage = FigureStorage(PipelineConfig())
                storage._bucket = None
                storage._client = None
                result = storage.update_manifest(
                    "chinaxiv-202201.00001",
                    [{"number": "1", "url": "https://example.com/fig1.png"}],
                )

                assert result is True
                mock_bucket.upload_local_file.assert_called_once()
