"""
Figure storage using Backblaze B2.

Handles upload/download of figure images to cloud storage.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

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

                # Support both B2_* and BACKBLAZE_* env var naming conventions
                key_id = (
                    self.config.b2_key_id
                    or os.environ.get("B2_KEY_ID")
                    or os.environ.get("BACKBLAZE_KEY_ID")
                )
                app_key = (
                    self.config.b2_app_key
                    or os.environ.get("B2_APP_KEY")
                    or os.environ.get("BACKBLAZE_APPLICATION_KEY")
                )

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

    # ─────────────────────────────────────────────────────────────────────────
    # Figure Manifest Management
    # ─────────────────────────────────────────────────────────────────────────

    MANIFEST_KEY = "figures/manifest.json"

    def download_manifest(self) -> Optional[Dict[str, Any]]:
        """
        Download the figure manifest from B2.

        Returns:
            Manifest dict or None if not found/error
        """
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                tmp_path = tmp.name

            downloaded = self.bucket.download_file_by_name(self.MANIFEST_KEY)
            downloaded.save_to(tmp_path)

            with open(tmp_path, "r") as f:
                manifest = json.load(f)

            os.unlink(tmp_path)
            return manifest

        except Exception as e:
            print(f"[storage] Manifest download failed: {e}")
            return None

    def upload_manifest(self, manifest: Dict[str, Any]) -> bool:
        """
        Upload the figure manifest to B2.

        Args:
            manifest: Manifest dict to upload

        Returns:
            True if successful
        """
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as tmp:
                json.dump(manifest, tmp, indent=2)
                tmp_path = tmp.name

            self.bucket.upload_local_file(
                local_file=tmp_path,
                file_name=self.MANIFEST_KEY,
            )

            os.unlink(tmp_path)
            print(f"[storage] Manifest uploaded with {len(manifest.get('papers', {}))} papers")
            return True

        except Exception as e:
            print(f"[storage] Manifest upload failed: {e}")
            return False

    def update_manifest(
        self, paper_id: str, figures: List[Dict[str, Any]]
    ) -> bool:
        """
        Update the B2 manifest with translated figure URLs for a paper.

        This is called after successfully translating figures for a paper.
        It downloads the current manifest, adds/updates the paper entry,
        and uploads the updated manifest.

        Args:
            paper_id: Paper ID (e.g., "chinaxiv-202201.00012")
            figures: List of figure dicts with keys:
                - number: Figure number (e.g., "1", "2")
                - url: Public URL to translated figure

        Returns:
            True if successful
        """
        # Download existing manifest or create new one
        manifest = self.download_manifest()
        if manifest is None:
            manifest = {"updated_at": "", "papers": {}}

        # Update timestamp
        manifest["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Add/update paper entry
        manifest["papers"][paper_id] = {
            "figure_count": len(figures),
            "figures": [
                {"number": fig.get("number", str(i + 1)), "url": fig.get("url", "")}
                for i, fig in enumerate(figures)
            ],
        }

        # Upload updated manifest
        return self.upload_manifest(manifest)
