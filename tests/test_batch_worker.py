"""
Tests for batch translation system (src/batch_translate.py and src/job_queue.py).

These tests verify job queue operations, worker lifecycle, and batch
processing coordination. All file operations use tmp_path fixtures.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from src.job_queue import JobQueue


class TestJobQueueBasics:
    """Test basic job queue operations."""

    def test_add_jobs_creates_files(self, tmp_path):
        """Adding jobs creates JSON files."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        added = queue.add_jobs(["paper-001", "paper-002"])

        assert added == 2
        assert (queue.jobs_dir / "paper-001.json").exists()
        assert (queue.jobs_dir / "paper-002.json").exists()

    def test_add_jobs_skips_existing(self, tmp_path):
        """Adding existing jobs returns 0 for duplicates."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        # Add first time
        added1 = queue.add_jobs(["paper-001"])
        # Add again
        added2 = queue.add_jobs(["paper-001"])

        assert added1 == 1
        assert added2 == 0

    def test_job_file_structure(self, tmp_path):
        """Job file has expected structure."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])

        job_file = queue.jobs_dir / "paper-001.json"
        job = json.loads(job_file.read_text())

        assert job["id"] == "paper-001"
        assert job["status"] == "pending"
        assert job["attempts"] == 0
        assert "created_at" in job


class TestJobClaiming:
    """Test job claiming mechanics."""

    def test_claim_job_returns_pending(self, tmp_path):
        """Claiming returns a pending job."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])
        job = queue.claim_job("worker-1")

        assert job is not None
        assert job["id"] == "paper-001"
        assert job["status"] == "in_progress"
        assert job["worker_id"] == "worker-1"

    def test_claim_job_returns_none_when_empty(self, tmp_path):
        """Claiming returns None when no jobs available."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        job = queue.claim_job("worker-1")

        assert job is None

    def test_claim_job_skips_in_progress(self, tmp_path):
        """Claiming skips jobs already in progress."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        # Add and claim first job
        queue.add_jobs(["paper-001", "paper-002"])
        job1 = queue.claim_job("worker-1")

        # Second worker should get different job
        job2 = queue.claim_job("worker-2")

        assert job1["id"] != job2["id"]


class TestJobCompletion:
    """Test job completion and failure."""

    def test_complete_job_updates_status(self, tmp_path):
        """Completing a job updates status to completed."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])
        queue.claim_job("worker-1")
        queue.complete_job("paper-001")

        job_file = queue.jobs_dir / "paper-001.json"
        job = json.loads(job_file.read_text())

        assert job["status"] == "completed"
        assert "completed_at" in job

    def test_fail_job_increments_attempts(self, tmp_path):
        """Failing a job increments attempt counter."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])
        queue.fail_job("paper-001", "Test error")

        job_file = queue.jobs_dir / "paper-001.json"
        job = json.loads(job_file.read_text())

        assert job["attempts"] == 1
        assert job["last_error"] == "Test error"
        # First failure returns to pending
        assert job["status"] == "pending"

    def test_fail_job_marks_failed_after_3_attempts(self, tmp_path):
        """Job marked failed after 3 attempts."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])

        # Fail 3 times
        for i in range(3):
            queue.fail_job("paper-001", f"Error {i+1}")

        job_file = queue.jobs_dir / "paper-001.json"
        job = json.loads(job_file.read_text())

        assert job["attempts"] == 3
        assert job["status"] == "failed"


class TestJobStats:
    """Test job statistics."""

    def test_get_stats_empty(self, tmp_path):
        """Stats for empty queue."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        stats = queue.get_stats()

        assert stats["total"] == 0
        assert stats["pending"] == 0

    def test_get_stats_mixed(self, tmp_path):
        """Stats with mixed job statuses."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001", "paper-002", "paper-003", "paper-004"])

        # Complete one
        queue.complete_job("paper-001")
        # Fail one (3 times)
        for _ in range(3):
            queue.fail_job("paper-002", "error")
        # Claim one
        queue.claim_job("worker-1")  # Gets paper-003 or paper-004

        stats = queue.get_stats()

        assert stats["total"] == 4
        assert stats["completed"] == 1
        assert stats["failed"] == 1
        assert stats["in_progress"] == 1
        assert stats["pending"] == 1


class TestJobHelpers:
    """Test helper functions for job management."""

    def test_get_pending_job_ids(self, tmp_path):
        """Gets list of pending job IDs."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001", "paper-002", "paper-003"])
        queue.complete_job("paper-001")

        pending = queue.get_pending_job_ids()

        assert len(pending) == 2
        assert "paper-001" not in pending

    def test_get_recent_completions(self, tmp_path):
        """Gets recent completed jobs sorted by time."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001", "paper-002"])
        queue.complete_job("paper-001")
        queue.complete_job("paper-002")

        recent = queue.get_recent_completions(limit=5)

        assert len(recent) == 2
        assert recent[0]["paper_id"] in ["paper-001", "paper-002"]

    def test_reset_stuck_jobs(self, tmp_path):
        """Resets jobs stuck in progress."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])
        queue.claim_job("worker-1")

        # Manually set old started_at
        job_file = queue.jobs_dir / "paper-001.json"
        job = json.loads(job_file.read_text())
        job["started_at"] = (datetime.now() - timedelta(hours=1)).isoformat()
        job_file.write_text(json.dumps(job))

        reset = queue.reset_stuck_jobs(timeout_minutes=10)

        assert reset == 1

        job = json.loads(job_file.read_text())
        assert job["status"] == "pending"

    def test_get_failed_jobs(self, tmp_path):
        """Gets list of failed jobs."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001", "paper-002"])
        for _ in range(3):
            queue.fail_job("paper-001", "API error")

        failed = queue.get_failed_jobs()

        assert len(failed) == 1
        assert failed[0]["paper_id"] == "paper-001"
        assert failed[0]["attempts"] == 3
        assert "API error" in failed[0]["error"]

    def test_reset_failed_jobs(self, tmp_path):
        """Resets failed jobs to pending."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])
        for _ in range(3):
            queue.fail_job("paper-001", "error")

        reset = queue.reset_failed_jobs()

        assert reset == 1

        job_file = queue.jobs_dir / "paper-001.json"
        job = json.loads(job_file.read_text())
        assert job["status"] == "pending"


class TestCleanup:
    """Test job cleanup operations."""

    def test_cleanup_completed_removes_old(self, tmp_path):
        """Cleanup removes completed jobs older than threshold."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])
        queue.complete_job("paper-001")

        # Set old completed_at
        job_file = queue.jobs_dir / "paper-001.json"
        job = json.loads(job_file.read_text())
        job["completed_at"] = (datetime.now() - timedelta(days=10)).isoformat()
        job_file.write_text(json.dumps(job))

        queue.cleanup_completed(days=7)

        assert not job_file.exists()

    def test_cleanup_completed_keeps_recent(self, tmp_path):
        """Cleanup keeps recently completed jobs."""
        queue = JobQueue()
        queue.jobs_dir = tmp_path / "jobs"
        queue.jobs_dir.mkdir()

        queue.add_jobs(["paper-001"])
        queue.complete_job("paper-001")

        queue.cleanup_completed(days=7)

        job_file = queue.jobs_dir / "paper-001.json"
        assert job_file.exists()


class TestBatchTranslateCLI:
    """Test batch_translate CLI commands."""

    @patch("src.batch_translate.job_queue")
    def test_init_queue_with_harvested(self, mock_queue, tmp_path, monkeypatch):
        """Init command loads from harvested records."""
        from src.batch_translate import init_queue

        # Create records file
        records_dir = tmp_path / "data" / "records"
        records_dir.mkdir(parents=True)
        records_file = records_dir / "chinaxiv_202401.json"
        records_file.write_text('[{"id": "paper-001"}, {"id": "paper-002"}]')

        monkeypatch.chdir(tmp_path)
        mock_queue.add_jobs.return_value = 2

        init_queue(["2024"], limit=None, use_harvested=True)

        mock_queue.add_jobs.assert_called_once()
        called_ids = mock_queue.add_jobs.call_args[0][0]
        assert len(called_ids) == 2

    @patch("src.batch_translate.job_queue")
    def test_init_queue_with_limit(self, mock_queue, tmp_path, monkeypatch):
        """Init command respects limit."""
        from src.batch_translate import init_queue

        records_dir = tmp_path / "data" / "records"
        records_dir.mkdir(parents=True)
        records_file = records_dir / "chinaxiv_202401.json"
        records_file.write_text('[{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}, {"id": "5"}]')

        monkeypatch.chdir(tmp_path)
        mock_queue.add_jobs.return_value = 2

        init_queue(["2024"], limit=2, use_harvested=True)

        called_ids = mock_queue.add_jobs.call_args[0][0]
        assert len(called_ids) == 2

    @patch("src.batch_translate.process_papers_streaming")
    @patch("src.batch_translate.job_queue")
    def test_start_workers_processes_jobs(self, mock_queue, mock_streaming):
        """Start workers processes pending jobs."""
        from src.batch_translate import start_workers

        mock_queue.get_stats.return_value = {"pending": 2}
        mock_queue.get_pending_job_ids.return_value = ["paper-001", "paper-002"]
        mock_streaming.return_value = iter([
            {"id": "paper-001", "status": "completed"},
            {"id": "paper-002", "status": "completed"},
        ])

        start_workers(num_workers=2)

        mock_queue.complete_job.assert_called()
        assert mock_queue.complete_job.call_count == 2

    @patch("src.batch_translate.process_papers_streaming")
    @patch("src.batch_translate.job_queue")
    def test_start_workers_handles_failures(self, mock_queue, mock_streaming):
        """Start workers handles failed jobs."""
        from src.batch_translate import start_workers

        mock_queue.get_stats.return_value = {"pending": 1}
        mock_queue.get_pending_job_ids.return_value = ["paper-001"]
        mock_streaming.return_value = iter([
            {"id": "paper-001", "status": "failed", "error": "API error"},
        ])

        start_workers(num_workers=1)

        mock_queue.fail_job.assert_called_once_with("paper-001", "API error")

    @patch("src.batch_translate.process_papers_streaming")
    @patch("src.batch_translate.job_queue")
    def test_start_workers_exits_when_empty(self, mock_queue, mock_streaming):
        """Start workers exits when no pending jobs."""
        from src.batch_translate import start_workers

        mock_queue.get_stats.return_value = {"pending": 0}

        start_workers(num_workers=1)

        mock_streaming.assert_not_called()


class TestWorkerLifecycle:
    """Test worker start/stop lifecycle."""

    def test_stop_workers_sends_sigterm(self, tmp_path):
        """Stop workers sends SIGTERM to PIDs."""
        from src.batch_translate import stop_workers

        # Create mock PID file
        pid_dir = tmp_path / "data" / "workers"
        pid_dir.mkdir(parents=True)
        (pid_dir / "worker-1.pid").write_text("12345")

        with patch("src.batch_translate.Path") as mock_path:
            mock_path.return_value = pid_dir
            with patch("os.kill") as mock_kill:
                # First call succeeds, second raises (process gone)
                mock_kill.side_effect = [None, OSError()]
                stop_workers()

                mock_kill.assert_called()

    @patch("src.batch_translate.job_queue")
    def test_resume_resets_and_restarts(self, mock_queue):
        """Resume resets stuck jobs and restarts workers."""
        from src.batch_translate import resume

        mock_queue.reset_stuck_jobs.return_value = 2
        mock_queue.get_stats.return_value = {"pending": 0}

        resume()

        mock_queue.reset_stuck_jobs.assert_called_with(timeout_minutes=10)

    @patch("src.batch_translate.job_queue")
    def test_retry_failed_resets_failed(self, mock_queue):
        """Retry failed resets failed jobs to pending."""
        from src.batch_translate import retry_failed

        mock_queue.reset_failed_jobs.return_value = 3
        mock_queue.get_stats.return_value = {"pending": 0}

        retry_failed(num_workers=5)

        mock_queue.reset_failed_jobs.assert_called_once()


class TestStatusDisplay:
    """Test status display functions."""

    @patch("src.batch_translate.job_queue")
    @patch("builtins.print")
    def test_show_status_displays_stats(self, mock_print, mock_queue):
        """Show status displays queue statistics."""
        from src.batch_translate import show_status

        mock_queue.get_stats.return_value = {
            "total": 100,
            "pending": 50,
            "in_progress": 10,
            "completed": 35,
            "failed": 5,
        }

        show_status()

        # Verify stats were printed
        output = " ".join(str(call) for call in mock_print.call_args_list)
        assert "100" in output
        assert "Completed" in output

    @patch("src.batch_translate.job_queue")
    @patch("builtins.print")
    def test_show_failed_lists_failures(self, mock_print, mock_queue):
        """Show failed lists failed jobs with errors."""
        from src.batch_translate import show_failed

        mock_queue.get_failed_jobs.return_value = [
            {"paper_id": "paper-001", "attempts": 3, "error": "Connection timeout"},
        ]

        show_failed()

        output = " ".join(str(call) for call in mock_print.call_args_list)
        assert "paper-001" in output
        assert "Connection timeout" in output


class TestCircuitBreakerIntegration:
    """Test circuit breaker integration in batch translate."""

    @patch("src.batch_translate.log")
    @patch("src.batch_translate.process_papers_streaming")
    @patch("src.batch_translate.job_queue")
    def test_circuit_breaker_handled_gracefully(
        self, mock_queue, mock_streaming, mock_log
    ):
        """Circuit breaker open is handled gracefully (logged, not re-raised).

        Note: Alerts are sent by the circuit breaker itself when it trips,
        not by batch_translate when it catches the exception. See
        test_circuit_breaker.py for alert tests.
        """
        from src.batch_translate import start_workers
        from src.services.translation_service import CircuitBreakerOpen

        mock_queue.get_stats.return_value = {"pending": 1}
        mock_queue.get_pending_job_ids.return_value = ["paper-001"]
        mock_streaming.side_effect = CircuitBreakerOpen("Rate limit exceeded")

        # Should not raise - circuit breaker exception is handled gracefully
        start_workers(num_workers=1)

        # Verify it was logged
        log_calls = [str(call) for call in mock_log.call_args_list]
        assert any("Circuit breaker triggered" in str(call) for call in log_calls)
