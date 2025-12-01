"""
Pipeline status writer for observability dashboard.

Writes status manifests to B2 at well-defined checkpoints. Uses batching to avoid
excessive uploads while providing near-real-time progress visibility.

Status files:
- status/pipeline-status.json - Current pipeline progress (updated every 25 papers or 30s)
- status/inventory.json - B2 content inventory (updated at stage completion)

Usage:
    from src.status_writer import StatusWriter

    writer = StatusWriter()
    writer.start_stage("figures", month="202402", total=244)

    for paper in papers:
        process(paper)
        writer.record_completion(success=True)  # Batched writes

    writer.finish_stage()  # Final write + inventory update
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class StatusWriter:
    """
    Write pipeline status to B2 with batching.

    Batches updates to avoid excessive B2 writes while maintaining visibility.
    Writes occur every BATCH_SIZE completions OR every BATCH_SECONDS, whichever first.
    """

    BATCH_SIZE = 25  # Papers between status updates
    BATCH_SECONDS = 30  # Seconds between status updates
    STATUS_KEY = "status/pipeline-status.json"
    INVENTORY_KEY = "status/inventory.json"

    def __init__(self):
        """Initialize status writer with B2 credentials from environment."""
        self._bucket = os.environ.get("BACKBLAZE_BUCKET", "chinaxiv")
        self._endpoint = os.environ.get(
            "BACKBLAZE_S3_ENDPOINT", "https://s3.us-west-004.backblazeb2.com"
        )
        self._prefix = os.environ.get("BACKBLAZE_PREFIX", "")

        # Batch state
        self._current_status: Optional[Dict[str, Any]] = None
        self._last_write_time: float = 0
        self._pending_completions: int = 0
        self._last_written_counts: Optional[Dict[str, int]] = None

    def _s3_key(self, key: str) -> str:
        """Build full S3 key with prefix."""
        if self._prefix:
            return f"s3://{self._bucket}/{self._prefix}{key}"
        return f"s3://{self._bucket}/{key}"

    def _upload_json(self, data: Dict[str, Any], key: str) -> bool:
        """
        Upload JSON to B2 with cache-control headers.

        Args:
            data: Dict to serialize and upload
            key: B2 key (e.g., "status/pipeline-status.json")

        Returns:
            True if upload succeeded
        """
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = tmp.name

        try:
            s3_path = self._s3_key(key)
            cmd = (
                f"aws s3 cp {shlex.quote(tmp_path)} {shlex.quote(s3_path)} "
                f'--cache-control "max-age=5, s-maxage=5" '
                f"--content-type application/json "
                f"--endpoint-url {shlex.quote(self._endpoint)} "
                f"--only-show-errors"
            )

            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0

        except Exception as e:
            print(f"[status_writer] Upload failed: {e}")
            return False
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _download_json(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Download JSON from B2.

        Args:
            key: B2 key

        Returns:
            Parsed dict or None if not found/error
        """
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            s3_path = self._s3_key(key)
            cmd = (
                f"aws s3 cp {shlex.quote(s3_path)} {shlex.quote(tmp_path)} "
                f"--endpoint-url {shlex.quote(self._endpoint)} "
                f"--only-show-errors"
            )

            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                with open(tmp_path) as f:
                    return json.load(f)
            return None

        except Exception:
            return None
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def start_stage(
        self,
        stage: str,
        total: int,
        month: Optional[str] = None,
        run_id: Optional[int] = None,
        run_url: Optional[str] = None,
    ) -> None:
        """
        Initialize status for a new pipeline stage.

        Args:
            stage: Stage name (e.g., "translate", "figures")
            total: Total items to process
            month: Optional month being processed (YYYYMM)
            run_id: GitHub Actions run ID
            run_url: GitHub Actions run URL
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build run URL from environment if not provided
        if not run_url and not run_id:
            run_id = os.environ.get("GITHUB_RUN_ID")
            repo = os.environ.get("GITHUB_REPOSITORY", "domus-magna/chinaxiv-english")
            if run_id:
                run_url = f"https://github.com/{repo}/actions/runs/{run_id}"

        self._current_status = {
            "stage": stage,
            "month": month,
            "run_id": int(run_id) if run_id else None,
            "run_url": run_url,
            "started_at": now,
            "updated_at": now,
            "status": "in_progress",
            "counts": {"total": total, "completed": 0, "failed": 0},
        }

        self._pending_completions = 0
        self._last_write_time = time.time()
        self._last_written_counts = None

        # Write initial status
        self._write_status()

    def record_completion(self, success: bool = True) -> None:
        """
        Record completion of one item. May trigger batched write.

        Args:
            success: True if item succeeded, False if failed
        """
        if not self._current_status:
            return

        if success:
            self._current_status["counts"]["completed"] += 1
        else:
            self._current_status["counts"]["failed"] += 1

        self._pending_completions += 1

        # Check if we should write
        time_elapsed = time.time() - self._last_write_time
        should_write = (
            self._pending_completions >= self.BATCH_SIZE
            or time_elapsed >= self.BATCH_SECONDS
        )

        if should_write:
            self._maybe_write_status()

    def _counts_changed(self) -> bool:
        """Check if counts have changed since last write."""
        if self._last_written_counts is None:
            return True
        current = self._current_status["counts"]
        return (
            current["completed"] != self._last_written_counts["completed"]
            or current["failed"] != self._last_written_counts["failed"]
        )

    def _maybe_write_status(self) -> None:
        """Write status if counts have changed."""
        if self._counts_changed():
            self._write_status()

    def _write_status(self) -> None:
        """Write current status to B2."""
        if not self._current_status:
            return

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._current_status["updated_at"] = now

        if self._upload_json(self._current_status, self.STATUS_KEY):
            self._last_write_time = time.time()
            self._pending_completions = 0
            self._last_written_counts = dict(self._current_status["counts"])
            completed = self._current_status["counts"]["completed"]
            total = self._current_status["counts"]["total"]
            print(f"[status_writer] Progress: {completed}/{total}")

    def finish_stage(
        self,
        success: bool = True,
        figures_translated: Optional[int] = None,
    ) -> None:
        """
        Finalize stage and update inventory.

        Args:
            success: True if stage completed successfully
            figures_translated: Actual number of figures translated (for figures stage)
                               If not provided, falls back to completed papers count
        """
        if not self._current_status:
            return

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._current_status["updated_at"] = now
        self._current_status["status"] = "completed" if success else "failed"

        # Store actual figures count for inventory
        if figures_translated is not None:
            self._current_status["figures_translated"] = figures_translated

        # Final status write
        self._upload_json(self._current_status, self.STATUS_KEY)

        # Update inventory on success
        if success:
            self._update_inventory()

        self._current_status = None

    def write_failure(self, error_message: Optional[str] = None) -> None:
        """
        Write failure status. Call from finally/except blocks.

        Args:
            error_message: Optional error message to include
        """
        if not self._current_status:
            return

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._current_status["updated_at"] = now
        self._current_status["status"] = "failed"
        if error_message:
            self._current_status["error"] = error_message

        self._upload_json(self._current_status, self.STATUS_KEY)

    def _update_inventory(self) -> None:
        """
        Update B2 inventory with stage completion data.

        Uses idempotent per-month overwrites to prevent double-counting on reruns.
        Each (month, stage) combination stores absolute counts, not deltas.
        """
        if not self._current_status:
            return

        # Download existing inventory
        inventory = self._download_json(self.INVENTORY_KEY) or {
            "updated_at": "",
            "pdfs": 0,
            "validated": 0,
            "flagged": 0,
            "figures": 0,
            "by_month": {},
        }

        # Update timestamp
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        inventory["updated_at"] = now

        # Get stage info
        stage = self._current_status["stage"]
        month = self._current_status.get("month")
        run_id = self._current_status.get("run_id")

        # For figures stage, use actual figures count (not papers count)
        if stage == "figures":
            count = self._current_status.get("figures_translated", 0)
        else:
            count = self._current_status["counts"]["completed"]

        # Idempotent update: overwrite per-month counts (not add)
        # This prevents double-counting on reruns
        if month:
            if month not in inventory["by_month"]:
                inventory["by_month"][month] = {}
            month_data = inventory["by_month"][month]

            if stage == "translate":
                month_data["validated"] = count
                month_data["last_translate_run"] = run_id
            elif stage == "figures":
                month_data["figures"] = count
                month_data["last_figures_run"] = run_id

        # Recalculate totals from per-month data (idempotent)
        # This ensures totals are always correct regardless of rerun order
        total_validated = sum(
            m.get("validated", 0) for m in inventory["by_month"].values()
        )
        total_figures = sum(
            m.get("figures", 0) for m in inventory["by_month"].values()
        )

        inventory["validated"] = total_validated
        inventory["figures"] = total_figures

        # Upload updated inventory
        self._upload_json(inventory, self.INVENTORY_KEY)
        print(f"[status_writer] Inventory updated (month={month}, {stage}={count})")


# Module-level convenience functions for simple usage
_default_writer: Optional[StatusWriter] = None


def get_writer() -> StatusWriter:
    """Get the default status writer instance."""
    global _default_writer
    if _default_writer is None:
        _default_writer = StatusWriter()
    return _default_writer


def start_stage(
    stage: str,
    total: int,
    month: Optional[str] = None,
    run_id: Optional[int] = None,
    run_url: Optional[str] = None,
) -> None:
    """Start tracking a new pipeline stage."""
    get_writer().start_stage(stage, total, month, run_id, run_url)


def record_completion(success: bool = True) -> None:
    """Record completion of one item."""
    get_writer().record_completion(success)


def finish_stage(
    success: bool = True,
    figures_translated: Optional[int] = None,
) -> None:
    """Finalize stage and update inventory."""
    get_writer().finish_stage(success, figures_translated)


def write_failure(error_message: Optional[str] = None) -> None:
    """Write failure status."""
    get_writer().write_failure(error_message)
