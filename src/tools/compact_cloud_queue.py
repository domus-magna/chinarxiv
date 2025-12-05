"""
Utility to compact data/cloud_jobs.json by archiving completed jobs.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


QUEUE_DEFAULT = Path("data/cloud_jobs.json")
ARCHIVE_DEFAULT = Path("data/cloud_jobs_archive.json")
COMPLETED_STATUS = "completed"


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def _extend_archive(path: Path, jobs: List[dict]) -> None:
    if not jobs:
        return
    existing = _load_json(path)
    archive_payload = {"jobs": []}
    if isinstance(existing, dict) and isinstance(existing.get("jobs"), list):
        archive_payload["jobs"] = existing["jobs"]
    archive_payload["jobs"].extend(jobs)
    archive_payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    _write_json(path, archive_payload)


def _completed_sort_key(job: dict) -> Tuple[bool, str]:
    ts = (
        job.get("completed_at")
        or job.get("updated_at")
        or job.get("started_at")
        or job.get("created_at")
        or ""
    )
    return (False, ts) if ts else (True, "")


def compact_queue(
    queue_path: Path,
    archive_path: Path,
    retain_completed: int,
) -> Tuple[int, int]:
    """
    Remove completed jobs from queue file and append them to archive.
    Returns (archived_count, retained_completed_count).
    """
    payload = _load_json(queue_path)
    if not payload or not isinstance(payload.get("jobs"), list):
        return (0, 0)

    jobs: List[dict] = payload["jobs"]
    completed_jobs: List[dict] = [
        job for job in jobs if job.get("status") == COMPLETED_STATUS
    ]
    retain_completed = max(retain_completed, 0)

    retained_jobs: List[dict] = []
    archived_jobs: List[dict] = []

    if retain_completed >= len(completed_jobs):
        # Nothing to archive; keep everything as-is.
        return (0, len(completed_jobs))

    # Determine which completed jobs to retain (most recent first).
    completed_sorted = sorted(
        completed_jobs,
        key=_completed_sort_key,
        reverse=True,
    )
    retained_subset = completed_sorted[:retain_completed] if retain_completed else []
    retained_keys = {
        (
            job.get("paper_id"),
            job.get("completed_at") or job.get("started_at") or job.get("created_at"),
        )
        for job in retained_subset
    }

    for job in jobs:
        if job.get("status") != COMPLETED_STATUS:
            retained_jobs.append(job)
            continue
        key = (
            job.get("paper_id"),
            job.get("completed_at") or job.get("started_at") or job.get("created_at"),
        )
        if key in retained_keys:
            retained_jobs.append(job)
            retained_keys.remove(key)
        else:
            archived_jobs.append(job)

    payload["jobs"] = retained_jobs
    metadata = payload.get("metadata") or {}
    metadata["last_compacted"] = datetime.now(timezone.utc).isoformat()
    payload["metadata"] = metadata

    _write_json(queue_path, payload)
    _extend_archive(archive_path, archived_jobs)

    return (len(archived_jobs), len(retained_subset))


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compact cloud job queue and archive completed entries."
    )
    parser.add_argument(
        "--queue-file",
        type=Path,
        default=QUEUE_DEFAULT,
        help="Queue JSON path (default: data/cloud_jobs.json)",
    )
    parser.add_argument(
        "--archive-file",
        type=Path,
        default=ARCHIVE_DEFAULT,
        help="Archive JSON path for removed jobs (default: data/cloud_jobs_archive.json)",
    )
    parser.add_argument(
        "--retain-completed",
        type=int,
        default=0,
        help="Number of most recent completed jobs to keep in the active queue (default: 0).",
    )
    args = parser.parse_args(argv)

    archived, retained = compact_queue(
        args.queue_file, args.archive_file, args.retain_completed
    )
    print(f"Archived {archived} completed jobs; retained {retained}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
