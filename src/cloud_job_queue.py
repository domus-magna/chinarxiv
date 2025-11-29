"""
Cloud-native job queue for batch translation in GitHub Actions.

Uses a single JSON file for all jobs, optimized for Git-based workflows.
Atomic operations via file locking prevent race conditions between parallel workers.
"""

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List


@dataclass
class JobStatus:
    """Job status constants."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    QA_FLAGGED = "qa_flagged"


class CloudJobQueue:
    """
    Cloud-native job queue using single JSON file.

    Designed for GitHub Actions workflows with larger runners.
    Supports atomic batch claiming and progress tracking.
    """

    def __init__(self, queue_file: str = "data/cloud_jobs.json"):
        self.queue_file = Path(queue_file)
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)

        # Initialize queue file if it doesn't exist
        if not self.queue_file.exists():
            self._write_queue(
                {"jobs": [], "metadata": {"created_at": datetime.now().isoformat()}}
            )

    def _read_queue(self) -> Dict:
        """Read queue with file locking."""
        with open(self.queue_file, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return data

    def _write_queue(self, data: Dict) -> None:
        """Write queue with file locking."""
        with open(self.queue_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def add_jobs(self, paper_ids: List[str], force: bool = False) -> int:
        """
        Add jobs to queue.

        Args:
            paper_ids: List of paper IDs to add
            force: If True, re-add even if already exists

        Returns:
            Number of jobs added
        """
        data = self._read_queue()
        existing_ids = {job["paper_id"] for job in data["jobs"]}

        added = 0
        for paper_id in paper_ids:
            if paper_id not in existing_ids or force:
                data["jobs"].append(
                    {
                        "paper_id": paper_id,
                        "status": JobStatus.PENDING,
                        "created_at": datetime.now().isoformat(),
                        "attempts": 0,
                        "worker_id": None,
                        "started_at": None,
                        "completed_at": None,
                        "error": None,
                    }
                )
                added += 1

        if added > 0:
            data["metadata"]["last_updated"] = datetime.now().isoformat()
            self._write_queue(data)

        return added

    def claim_batch(
        self, worker_id: str, batch_size: int = 100, max_attempts: int = 3
    ) -> List[Dict]:
        """
        Atomically claim a batch of pending jobs.

        Args:
            worker_id: Worker identifier (e.g., "worker-1", "gh-actions-run-123")
            batch_size: Number of jobs to claim
            max_attempts: Skip jobs that have failed this many times

        Returns:
            List of claimed job dictionaries
        """
        data = self._read_queue()

        claimed = []
        for job in data["jobs"]:
            if len(claimed) >= batch_size:
                break

            if job["status"] == JobStatus.PENDING and job["attempts"] < max_attempts:
                job["status"] = JobStatus.IN_PROGRESS
                job["worker_id"] = worker_id
                job["started_at"] = datetime.now().isoformat()
                job["attempts"] += 1
                claimed.append(job.copy())

        if claimed:
            data["metadata"]["last_updated"] = datetime.now().isoformat()
            self._write_queue(data)

        return claimed

    def complete_job(self, paper_id: str, qa_passed: bool = True) -> None:
        """
        Mark job as completed.

        Args:
            paper_id: Paper ID
            qa_passed: Whether QA checks passed
        """
        data = self._read_queue()

        for job in data["jobs"]:
            if job["paper_id"] == paper_id:
                if qa_passed:
                    job["status"] = JobStatus.COMPLETED
                else:
                    job["status"] = JobStatus.QA_FLAGGED
                job["completed_at"] = datetime.now().isoformat()
                break

        data["metadata"]["last_updated"] = datetime.now().isoformat()
        self._write_queue(data)

    def fail_job(self, paper_id: str, error: str, max_attempts: int = 3) -> None:
        """
        Mark job as failed.

        Args:
            paper_id: Paper ID
            error: Error message
            max_attempts: Maximum retry attempts before permanent failure
        """
        data = self._read_queue()

        for job in data["jobs"]:
            if job["paper_id"] == paper_id:
                job["error"] = error
                job["updated_at"] = datetime.now().isoformat()

                if job["attempts"] >= max_attempts:
                    job["status"] = JobStatus.FAILED
                else:
                    # Reset to pending for retry
                    job["status"] = JobStatus.PENDING
                    job["worker_id"] = None
                    job["started_at"] = None
                break

        data["metadata"]["last_updated"] = datetime.now().isoformat()
        self._write_queue(data)

    def get_stats(self) -> Dict[str, int]:
        """Get job statistics."""
        data = self._read_queue()

        stats = {
            "total": len(data["jobs"]),
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "failed": 0,
            "qa_flagged": 0,
        }

        for job in data["jobs"]:
            status = job["status"]
            if status in stats:
                stats[status] += 1

        return stats

    def reset_stuck_jobs(self, timeout_minutes: int = 60) -> int:
        """
        Reset jobs stuck in_progress for too long.

        Args:
            timeout_minutes: Jobs running longer than this are reset

        Returns:
            Number of jobs reset
        """
        data = self._read_queue()
        cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
        reset_count = 0

        for job in data["jobs"]:
            if job["status"] == JobStatus.IN_PROGRESS and job.get("started_at"):
                try:
                    started = datetime.fromisoformat(job["started_at"])
                    if started < cutoff:
                        job["status"] = JobStatus.PENDING
                        job["worker_id"] = None
                        job["started_at"] = None
                        reset_count += 1
                except (ValueError, TypeError):
                    pass

        if reset_count > 0:
            data["metadata"]["last_updated"] = datetime.now().isoformat()
            self._write_queue(data)

        return reset_count

    def get_failed_jobs(self, limit: int = 100) -> List[Dict]:
        """Get list of failed jobs."""
        data = self._read_queue()

        failed = [
            {
                "paper_id": job["paper_id"],
                "attempts": job["attempts"],
                "error": job.get("error", "Unknown error"),
                "updated_at": job.get("updated_at", job.get("started_at")),
            }
            for job in data["jobs"]
            if job["status"] == JobStatus.FAILED
        ]

        return failed[:limit]

    def get_qa_flagged_jobs(self, limit: int = 100) -> List[Dict]:
        """Get list of QA-flagged jobs."""
        data = self._read_queue()

        flagged = [
            {
                "paper_id": job["paper_id"],
                "completed_at": job.get("completed_at"),
            }
            for job in data["jobs"]
            if job["status"] == JobStatus.QA_FLAGGED
        ]

        return flagged[:limit]

    def reset_failed_jobs(self) -> int:
        """Reset all failed jobs back to pending."""
        data = self._read_queue()
        reset_count = 0

        for job in data["jobs"]:
            if job["status"] == JobStatus.FAILED:
                job["status"] = JobStatus.PENDING
                job["worker_id"] = None
                job["started_at"] = None
                job["attempts"] = 0  # Reset attempts
                job["error"] = None
                reset_count += 1

        if reset_count > 0:
            data["metadata"]["last_updated"] = datetime.now().isoformat()
            self._write_queue(data)

        return reset_count

    def export_completed_ids(self) -> List[str]:
        """Export list of completed paper IDs."""
        data = self._read_queue()
        return [
            job["paper_id"]
            for job in data["jobs"]
            if job["status"] == JobStatus.COMPLETED
        ]


# Global instance
cloud_queue = CloudJobQueue()


# CLI interface for debugging
def main():
    import argparse

    parser = argparse.ArgumentParser(description="Cloud job queue management")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Stats command
    subparsers.add_parser("stats", help="Show queue statistics")

    # Reset command
    reset_parser = subparsers.add_parser("reset-stuck", help="Reset stuck jobs")
    reset_parser.add_argument(
        "--timeout", type=int, default=60, help="Timeout in minutes"
    )

    # Failed command
    subparsers.add_parser("failed", help="Show failed jobs")

    # Retry command
    subparsers.add_parser("retry", help="Retry all failed jobs")

    # QA flagged command
    subparsers.add_parser("qa-flagged", help="Show QA-flagged jobs")

    args = parser.parse_args()

    if args.command == "stats":
        stats = cloud_queue.get_stats()
        print("\n" + "=" * 60)
        print("CLOUD JOB QUEUE STATS")
        print("=" * 60)
        for key, value in stats.items():
            print(f"{key:15} {value:,}")
        print("=" * 60 + "\n")

    elif args.command == "reset-stuck":
        count = cloud_queue.reset_stuck_jobs(timeout_minutes=args.timeout)
        print(f"Reset {count} stuck jobs")

    elif args.command == "failed":
        failed = cloud_queue.get_failed_jobs()
        print(f"\nFailed jobs ({len(failed)}):")
        for job in failed[:20]:
            print(f"  {job['paper_id']}: {job['error'][:60]}...")

    elif args.command == "retry":
        count = cloud_queue.reset_failed_jobs()
        print(f"Reset {count} failed jobs to pending")

    elif args.command == "qa-flagged":
        flagged = cloud_queue.get_qa_flagged_jobs()
        print(f"\nQA-flagged jobs ({len(flagged)}):")
        for job in flagged[:20]:
            print(f"  {job['paper_id']}")


if __name__ == "__main__":
    main()
