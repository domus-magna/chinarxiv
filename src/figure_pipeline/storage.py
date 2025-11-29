"""
Figure storage using Backblaze B2.

Handles upload/download of figure images to cloud storage.
"""
from __future__ import annotations

import os
from typing import Optional

from .models import PipelineConfig


class FigureStorage:
    """
    Upload/download figures to Backblaze B2.

    B2 storage layout:
    chinaxiv/
    ├── figures/{paper_id}/
    │   ├── original/
    │   │   ├── fig_1.png
    │   │   └── fig_2.png
    │   └── translated/
    │       ├── fig_1_en.png
    │       └── fig_2_en.png
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        """Initialize storage client."""
        self.config = config or PipelineConfig()
        self._client = None
        self._bucket = None

    @property
    def bucket(self):
        """Lazy-load B2 bucket."""
        if self._bucket is None:
            try:
                import b2sdk.v2 as b2

                key_id = self.config.b2_key_id or os.environ.get("B2_KEY_ID")
                app_key = self.config.b2_app_key or os.environ.get("B2_APP_KEY")

                if not key_id or not app_key:
                    raise ValueError("B2_KEY_ID and B2_APP_KEY must be set")

                info = b2.InMemoryAccountInfo()
                self._client = b2.B2Api(info)
                self._client.authorize_account("production", key_id, app_key)
                self._bucket = self._client.get_bucket_by_name(self.config.b2_bucket)

            except ImportError:
                raise ImportError(
                    "b2sdk not installed. Install with: pip install b2sdk"
                )
        return self._bucket

    def upload(self, local_path: str, remote_key: str) -> Optional[str]:
        """
        Upload file to B2.

        Args:
            local_path: Path to local file
            remote_key: Remote path in bucket (e.g., "figures/paper_id/fig_1.png")

        Returns:
            Public URL if successful, None otherwise
        """
        if not os.path.exists(local_path):
            print(f"[storage] File not found for upload: {local_path}")
            return None

        try:
            # Upload file
            file_info = self.bucket.upload_local_file(
                local_file=local_path,
                file_name=remote_key,
            )

            # Generate public URL
            # B2 public URL format: https://f002.backblazeb2.com/file/{bucket}/{key}
            download_url = self._client.get_download_url_for_fileid(file_info.id_)
            return download_url

        except Exception as e:
            print(f"[storage] Upload failed for {local_path}: {e}")
            return None

    def download(self, remote_key: str, local_path: str) -> bool:
        """
        Download file from B2.

        Args:
            remote_key: Remote path in bucket
            local_path: Path to save file locally

        Returns:
            True if successful
        """
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            downloaded_file = self.bucket.download_file_by_name(remote_key)
            downloaded_file.save_to(local_path)
            return True

        except Exception as e:
            print(f"[storage] Download failed for {remote_key}: {e}")
            return False

    def exists(self, remote_key: str) -> bool:
        """Check if file exists in B2."""
        try:
            self.bucket.get_file_info_by_name(remote_key)
            return True
        except Exception:
            return False

    def list_figures(self, paper_id: str) -> dict:
        """
        List all figures for a paper in B2.

        Args:
            paper_id: Paper ID

        Returns:
            Dict with 'original' and 'translated' lists of URLs
        """
        prefix = f"figures/{paper_id}/"
        result = {"original": [], "translated": []}

        try:
            for file_info, _ in self.bucket.ls(folder_to_list=prefix):
                url = self._client.get_download_url_for_fileid(file_info.id_)
                if "/original/" in file_info.file_name:
                    result["original"].append(url)
                elif "/translated/" in file_info.file_name:
                    result["translated"].append(url)
        except Exception:
            pass

        return result

    def delete_figures(self, paper_id: str) -> int:
        """
        Delete all figures for a paper.

        Args:
            paper_id: Paper ID

        Returns:
            Number of files deleted
        """
        prefix = f"figures/{paper_id}/"
        deleted = 0

        try:
            for file_info, _ in self.bucket.ls(folder_to_list=prefix):
                self.bucket.delete_file_version(file_info.id_, file_info.file_name)
                deleted += 1
        except Exception as e:
            print(f"[storage] Delete failed for {paper_id}: {e}")

        return deleted
