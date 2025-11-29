"""
Tests for simplified job queue functionality.
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch
from src.job_queue import (
    JobQueue,
    add_jobs,
    claim_job,
    complete_job,
    fail_job,
    get_stats,
)


class TestJobQueue:
    """Test simplified job queue functionality."""

    @pytest.fixture
    def temp_queue(self):
        """Create a temporary job queue for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("src.job_queue.Path") as mock_path:
                mock_path.return_value = Path(temp_dir)
                queue = JobQueue()
                yield queue

    def test_add_jobs(self, temp_queue):
        """Test adding jobs to queue."""
        paper_ids = ["paper1", "paper2", "paper3"]
        added = temp_queue.add_jobs(paper_ids)
        assert added == 3

        # Check that jobs were created
        stats = temp_queue.get_stats()
        assert stats["total"] == 3
        assert stats["pending"] == 3

    def test_claim_job(self, temp_queue):
        """Test claiming a job."""
        paper_ids = ["paper1", "paper2"]
        temp_queue.add_jobs(paper_ids)

        # Claim first job
        job = temp_queue.claim_job("worker1")
        assert job is not None
        assert job["id"] == "paper1"
        assert job["status"] == "in_progress"
        assert job["worker_id"] == "worker1"

        # Check stats
        stats = temp_queue.get_stats()
        assert stats["pending"] == 1
        assert stats["in_progress"] == 1

    def test_complete_job(self, temp_queue):
        """Test completing a job."""
        paper_ids = ["paper1"]
        temp_queue.add_jobs(paper_ids)

        # Claim and complete job
        job = temp_queue.claim_job("worker1")
        temp_queue.complete_job(job["id"])

        # Check stats
        stats = temp_queue.get_stats()
        assert stats["completed"] == 1
        assert stats["pending"] == 0
        assert stats["in_progress"] == 0

    def test_fail_job(self, temp_queue):
        """Test failing a job."""
        paper_ids = ["paper1"]
        temp_queue.add_jobs(paper_ids)

        # Claim and fail job
        job = temp_queue.claim_job("worker1")
        temp_queue.fail_job(job["id"], "Test error")

        # Check stats
        stats = temp_queue.get_stats()
        assert stats["failed"] == 0  # Should retry first
        assert stats["pending"] == 1

        # Fail again to trigger permanent failure
        job = temp_queue.claim_job("worker1")
        temp_queue.fail_job(job["id"], "Test error")
        temp_queue.fail_job(job["id"], "Test error")

        stats = temp_queue.get_stats()
        assert stats["failed"] == 1
        assert stats["pending"] == 0

    def test_get_stats(self, temp_queue):
        """Test getting job statistics."""
        paper_ids = ["paper1", "paper2", "paper3"]
        temp_queue.add_jobs(paper_ids)

        stats = temp_queue.get_stats()
        assert stats["total"] == 3
        assert stats["pending"] == 3
        assert stats["in_progress"] == 0
        assert stats["completed"] == 0
        assert stats["failed"] == 0

    def test_cleanup_completed(self, temp_queue):
        """Test cleaning up completed jobs."""
        paper_ids = ["paper1"]
        temp_queue.add_jobs(paper_ids)

        # Complete job
        job = temp_queue.claim_job("worker1")
        temp_queue.complete_job(job["id"])

        # Cleanup (with 0 days to force cleanup)
        temp_queue.cleanup_completed(days=0)

        # Check that job was removed
        stats = temp_queue.get_stats()
        assert stats["total"] == 0


class TestConvenienceFunctions:
    """Test convenience functions."""

    @patch("src.job_queue.job_queue")
    def test_add_jobs_function(self, mock_queue):
        """Test add_jobs convenience function."""
        mock_queue.add_jobs.return_value = 2

        paper_ids = ["paper1", "paper2"]
        added = add_jobs(paper_ids)
        assert added == 2
        mock_queue.add_jobs.assert_called_once_with(paper_ids)

    @patch("src.job_queue.job_queue")
    def test_claim_job_function(self, mock_queue):
        """Test claim_job convenience function."""
        mock_queue.claim_job.return_value = {"id": "paper1", "status": "in_progress"}

        job = claim_job("worker1")
        assert job is not None
        assert job["id"] == "paper1"
        mock_queue.claim_job.assert_called_once_with("worker1")

    @patch("src.job_queue.job_queue")
    def test_complete_job_function(self, mock_queue):
        """Test complete_job convenience function."""
        complete_job("paper1")
        mock_queue.complete_job.assert_called_once_with("paper1")

    @patch("src.job_queue.job_queue")
    def test_fail_job_function(self, mock_queue):
        """Test fail_job convenience function."""
        fail_job("paper1", "Test error")
        mock_queue.fail_job.assert_called_once_with("paper1", "Test error")

    @patch("src.job_queue.job_queue")
    def test_get_stats_function(self, mock_queue):
        """Test get_stats convenience function."""
        mock_queue.get_stats.return_value = {"total": 1, "pending": 1}

        stats = get_stats()
        assert stats["total"] == 1
        assert stats["pending"] == 1
        mock_queue.get_stats.assert_called_once()
