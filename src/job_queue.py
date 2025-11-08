"""
Simplified file-based job queue for batch translation.
"""

import json
import fcntl
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional


class JobQueue:
    """Simplified file-based job queue."""

    def __init__(self):
        self.jobs_dir = Path("data/jobs")
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def add_jobs(self, paper_ids: List[str]) -> int:
        """Add jobs to queue."""
        added = 0
        for paper_id in paper_ids:
            job_file = self.jobs_dir / f"{paper_id}.json"
            if not job_file.exists():
                job = {
                    "id": paper_id,
                    "status": "pending",
                    "created_at": datetime.now().isoformat(),
                    "attempts": 0,
                }
                with open(job_file, "w") as f:
                    json.dump(job, f)
                added += 1
        return added

    def claim_job(self, worker_id: str) -> Optional[Dict]:
        """Claim a pending job atomically."""
        for job_file in self.jobs_dir.glob("*.json"):
            try:
                # Use file locking to prevent race conditions
                with open(job_file, "r+") as f:
                    # Try to acquire exclusive lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                    # Read job data
                    f.seek(0)
                    job = json.load(f)

                    # Check if still pending (another worker might have claimed it)
                    if job["status"] == "pending":
                        # Update job status atomically
                        job["status"] = "in_progress"
                        job["worker_id"] = worker_id
                        job["started_at"] = datetime.now().isoformat()

                        # Write updated job data
                        f.seek(0)
                        f.truncate()
                        json.dump(job, f)
                        f.flush()

                        return job

            except (OSError, IOError):
                # File is locked by another process, try next file
                continue
            except (json.JSONDecodeError, KeyError):
                # Corrupted job file, skip
                continue

        return None

    def complete_job(self, job_id: str):
        """Mark job as completed."""
        job_file = self.jobs_dir / f"{job_id}.json"
        if job_file.exists():
            with open(job_file, "r") as f:
                job = json.load(f)

            job["status"] = "completed"
            job["completed_at"] = datetime.now().isoformat()

            with open(job_file, "w") as f:
                json.dump(job, f)

    def fail_job(self, job_id: str, error: str):
        """Mark job as failed."""
        job_file = self.jobs_dir / f"{job_id}.json"
        if job_file.exists():
            with open(job_file, "r") as f:
                job = json.load(f)

            job["attempts"] += 1
            job["last_error"] = error

            if job["attempts"] >= 3:
                job["status"] = "failed"
            else:
                job["status"] = "pending"

            with open(job_file, "w") as f:
                json.dump(job, f)

    def get_stats(self) -> Dict:
        """Get job statistics."""
        stats = {
            "total": 0,
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "failed": 0,
        }

        for job_file in self.jobs_dir.glob("*.json"):
            with open(job_file, "r") as f:
                job = json.load(f)

            stats["total"] += 1
            stats[job["status"]] += 1

        return stats

    def cleanup_completed(self, days: int = 7):
        """Clean up completed jobs older than specified days."""
        cutoff = datetime.now() - timedelta(days=days)

        for job_file in self.jobs_dir.glob("*.json"):
            with open(job_file, "r") as f:
                job = json.load(f)

            if job["status"] == "completed":
                try:
                    completed_at = datetime.fromisoformat(job.get("completed_at", ""))
                    if completed_at < cutoff:
                        job_file.unlink()
                except ValueError:
                    continue

    # --- Added helpers for CLI parity ---
    def get_recent_completions(self, limit: int = 5) -> List[Dict]:
        """Return recent completed jobs with metadata sorted by completion time desc."""
        items: List[Dict] = []
        for job_file in self.jobs_dir.glob("*.json"):
            try:
                with open(job_file, "r") as f:
                    job = json.load(f)
            except Exception:
                continue
            if job.get("status") == "completed":
                items.append(
                    {
                        "paper_id": job.get("id"),
                        "worker_id": job.get("worker_id"),
                        "completed_at": job.get("completed_at"),
                    }
                )

        # Sort by completed_at desc
        def _key(j: Dict):
            try:
                return datetime.fromisoformat((j.get("completed_at") or ""))
            except Exception:
                return datetime.min

        items.sort(key=_key, reverse=True)
        return items[:limit]

    def reset_stuck_jobs(self, timeout_minutes: int = 10) -> int:
        """Reset in-progress jobs that have been running longer than timeout to pending."""
        cutoff = datetime.now() - timedelta(minutes=timeout_minutes)
        reset = 0
        for job_file in self.jobs_dir.glob("*.json"):
            try:
                with open(job_file, "r") as f:
                    job = json.load(f)
            except Exception:
                continue
            if job.get("status") != "in_progress":
                continue
            started_at = job.get("started_at")
            try:
                started_dt = datetime.fromisoformat(started_at) if started_at else None
            except Exception:
                started_dt = None
            if (started_dt is None) or (started_dt < cutoff):
                job["status"] = "pending"
                # keep attempts and last_error
                with open(job_file, "w") as f:
                    json.dump(job, f)
                reset += 1
        return reset

    def get_failed_jobs(self) -> List[Dict]:
        """Return list of failed jobs with attempts and error."""
        out: List[Dict] = []
        for job_file in self.jobs_dir.glob("*.json"):
            try:
                with open(job_file, "r") as f:
                    job = json.load(f)
            except Exception:
                continue
            if job.get("status") == "failed":
                out.append(
                    {
                        "paper_id": job.get("id"),
                        "attempts": job.get("attempts", 0),
                        "error": job.get("last_error") or job.get("error"),
                    }
                )
        return out

    def reset_failed_jobs(self) -> int:
        """Reset failed jobs back to pending. Returns number reset."""
        reset = 0
        for job_file in self.jobs_dir.glob("*.json"):
            try:
                with open(job_file, "r") as f:
                    job = json.load(f)
            except Exception:
                continue
            if job.get("status") == "failed":
                job["status"] = "pending"
                with open(job_file, "w") as f:
                    json.dump(job, f)
                reset += 1
        return reset

    # Removed legacy duplicate helpers below in favor of the unified versions above

    def get_pending_job_ids(self) -> List[str]:
        """Get all pending job IDs without loading full job data."""
        pending_ids = []
        for job_file in self.jobs_dir.glob("*.json"):
            with open(job_file, "r") as f:
                job = json.load(f)

            if job["status"] == "pending":
                pending_ids.append(job["id"])

        return pending_ids


# Global job queue instance
job_queue = JobQueue()


# Convenience functions
def add_jobs(paper_ids: List[str]) -> int:
    """Convenience function to add jobs."""
    return job_queue.add_jobs(paper_ids)


def claim_job(worker_id: str) -> Optional[Dict]:
    """Convenience function to claim a job."""
    return job_queue.claim_job(worker_id)


def complete_job(job_id: str):
    """Convenience function to complete a job."""
    job_queue.complete_job(job_id)


def fail_job(job_id: str, error: str):
    """Convenience function to fail a job."""
    job_queue.fail_job(job_id, error)


def get_stats() -> Dict:
    """Convenience function to get job statistics."""
    return job_queue.get_stats()
