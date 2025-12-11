"""
Tests for the pipeline orchestrator (src/orchestrator.py).

This is CRITICAL code - the orchestrator controls all pipeline processing.
Tests cover:
- Work queue generation (all scope types)
- Paper locking and zombie detection
- Stage execution and status tracking
- Error handling and rollback
- Concurrent processing safety
"""

import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import psycopg2

from src.orchestrator import (
    get_db_connection,
    get_papers_by_month,
    get_papers_needing_work,
    get_paper_status,
    acquire_paper_lock,
    update_stage_status,
    mark_paper_complete,
    mark_paper_failed,
    release_paper_lock,
    process_paper,
    get_work_queue,
    run_orchestrator,
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def orchestrator_test_database(test_database_schema):
    """
    Set up test database with orchestrator-specific columns.

    This extends the base test_database_schema fixture to add
    the processing_status columns from our migration.
    """
    conn = psycopg2.connect(test_database_schema)
    cursor = conn.cursor()

    # Add orchestrator columns if they don't exist
    columns_to_add = [
        ("processing_status", "VARCHAR(20) DEFAULT 'pending'"),
        ("processing_started_at", "TIMESTAMP WITH TIME ZONE"),
        ("processing_error", "TEXT"),
        ("text_status", "VARCHAR(20) DEFAULT 'pending'"),
        ("text_completed_at", "TIMESTAMP WITH TIME ZONE"),
        ("figures_status", "VARCHAR(20) DEFAULT 'pending'"),
        ("figures_completed_at", "TIMESTAMP WITH TIME ZONE"),
        ("pdf_status", "VARCHAR(20) DEFAULT 'pending'"),
        ("pdf_completed_at", "TIMESTAMP WITH TIME ZONE"),
        ("has_chinese_pdf", "BOOLEAN DEFAULT FALSE"),
        ("has_english_pdf", "BOOLEAN DEFAULT FALSE"),
    ]

    for col_name, col_def in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE papers ADD COLUMN IF NOT EXISTS {col_name} {col_def}")
        except Exception as e:
            print(f"Column {col_name} may already exist: {e}")

    conn.commit()

    # Clear data
    cursor.execute("DELETE FROM paper_subjects;")
    cursor.execute("DELETE FROM papers;")
    conn.commit()
    conn.close()

    return test_database_schema


@pytest.fixture
def sample_orchestrator_papers(orchestrator_test_database):
    """
    Insert sample papers with orchestrator-specific fields.
    """
    conn = psycopg2.connect(orchestrator_test_database)
    cursor = conn.cursor()

    papers = [
        # Paper ready to process (pending)
        {
            'id': 'chinaxiv-202401.00001',
            'title_en': 'Test Paper 1',
            'abstract_en': 'Abstract 1',
            'creators_en': '["Author 1"]',
            'date': '2024-01-15T10:00:00',
            'processing_status': 'pending',
            'text_status': 'pending',
            'figures_status': 'pending',
            'pdf_status': 'pending',
        },
        # Paper already complete
        {
            'id': 'chinaxiv-202401.00002',
            'title_en': 'Test Paper 2',
            'abstract_en': 'Abstract 2',
            'creators_en': '["Author 2"]',
            'date': '2024-01-16T10:00:00',
            'processing_status': 'complete',
            'text_status': 'complete',
            'figures_status': 'complete',
            'pdf_status': 'complete',
        },
        # Paper currently processing (not zombie)
        {
            'id': 'chinaxiv-202401.00003',
            'title_en': 'Test Paper 3',
            'abstract_en': 'Abstract 3',
            'creators_en': '["Author 3"]',
            'date': '2024-01-17T10:00:00',
            'processing_status': 'processing',
            'processing_started_at': datetime.now(timezone.utc),
            'text_status': 'pending',
            'figures_status': 'pending',
            'pdf_status': 'pending',
        },
        # Zombie paper (processing started > 4 hours ago)
        {
            'id': 'chinaxiv-202401.00004',
            'title_en': 'Test Paper 4 - Zombie',
            'abstract_en': 'Abstract 4',
            'creators_en': '["Author 4"]',
            'date': '2024-01-18T10:00:00',
            'processing_status': 'processing',
            'processing_started_at': datetime.now(timezone.utc) - timedelta(hours=5),
            'text_status': 'pending',
            'figures_status': 'pending',
            'pdf_status': 'pending',
        },
        # Paper with text done, needs figures
        {
            'id': 'chinaxiv-202401.00005',
            'title_en': 'Test Paper 5 - Needs Figures',
            'abstract_en': 'Abstract 5',
            'creators_en': '["Author 5"]',
            'date': '2024-01-19T10:00:00',
            'processing_status': 'pending',
            'text_status': 'complete',
            'figures_status': 'pending',
            'pdf_status': 'pending',
        },
        # Different month paper
        {
            'id': 'chinaxiv-202402.00001',
            'title_en': 'February Paper',
            'abstract_en': 'February Abstract',
            'creators_en': '["Feb Author"]',
            'date': '2024-02-15T10:00:00',
            'processing_status': 'pending',
            'text_status': 'pending',
            'figures_status': 'pending',
            'pdf_status': 'pending',
        },
        # Failed paper
        {
            'id': 'chinaxiv-202401.00006',
            'title_en': 'Test Paper 6 - Failed',
            'abstract_en': 'Abstract 6',
            'creators_en': '["Author 6"]',
            'date': '2024-01-20T10:00:00',
            'processing_status': 'failed',
            'processing_error': 'Previous error message',
            'text_status': 'failed',
            'figures_status': 'pending',
            'pdf_status': 'pending',
        },
    ]

    for paper in papers:
        cols = ', '.join(paper.keys())
        placeholders = ', '.join(['%s'] * len(paper))
        cursor.execute(
            f"INSERT INTO papers ({cols}) VALUES ({placeholders})",
            list(paper.values())
        )

    conn.commit()
    conn.close()

    # Set DATABASE_URL for orchestrator to use
    os.environ['DATABASE_URL'] = orchestrator_test_database

    yield orchestrator_test_database

    # Cleanup
    if 'DATABASE_URL' in os.environ:
        del os.environ['DATABASE_URL']


# ============================================================================
# Test: Work Queue Generation
# ============================================================================

class TestWorkQueue:
    """Tests for get_work_queue and related functions."""

    def test_get_papers_by_month(self, sample_orchestrator_papers):
        """Test filtering papers by month."""
        conn = get_db_connection()
        try:
            # January 2024 should have 6 papers
            jan_papers = get_papers_by_month(conn, '202401')
            assert len(jan_papers) == 6
            assert all(p.startswith('chinaxiv-202401') for p in jan_papers)

            # February 2024 should have 1 paper
            feb_papers = get_papers_by_month(conn, '202402')
            assert len(feb_papers) == 1
            assert feb_papers[0] == 'chinaxiv-202402.00001'

            # March 2024 should have 0 papers
            mar_papers = get_papers_by_month(conn, '202403')
            assert len(mar_papers) == 0
        finally:
            conn.close()

    def test_get_papers_needing_work(self, sample_orchestrator_papers):
        """Test finding papers that need processing."""
        conn = get_db_connection()
        try:
            papers = get_papers_needing_work(conn)

            # Should include: pending + zombie, NOT: complete + currently processing
            # Pending: 00001, 00005, 202402.00001
            # Zombie: 00004
            # Failed counts as needing work (can be retried)
            # NOT included: 00002 (complete), 00003 (processing but not zombie)

            assert 'chinaxiv-202401.00001' in papers
            assert 'chinaxiv-202401.00004' in papers  # Zombie
            assert 'chinaxiv-202402.00001' in papers

            assert 'chinaxiv-202401.00002' not in papers  # Complete
            assert 'chinaxiv-202401.00003' not in papers  # Processing (not zombie)
        finally:
            conn.close()

    def test_get_papers_needing_work_text_only(self, sample_orchestrator_papers):
        """Test finding papers needing text translation only."""
        conn = get_db_connection()
        try:
            papers = get_papers_needing_work(conn, text_only=True)

            # Should include papers where text_status != 'complete'
            # 00001, 00003, 00004, 202402.00001, 00006
            # NOT: 00002 (all complete), 00005 (text complete)

            assert 'chinaxiv-202401.00001' in papers
            assert 'chinaxiv-202401.00005' not in papers  # Text already complete
            assert 'chinaxiv-202401.00002' not in papers  # All complete
        finally:
            conn.close()

    def test_get_papers_needing_work_figures_only(self, sample_orchestrator_papers):
        """Test finding papers needing figure translation only."""
        conn = get_db_connection()
        try:
            papers = get_papers_needing_work(conn, figures_only=True)

            # Should include papers where text=complete AND figures!=complete
            # Only: 00005
            # NOT: papers without text complete

            assert 'chinaxiv-202401.00005' in papers
            assert 'chinaxiv-202401.00001' not in papers  # Text not complete
            assert 'chinaxiv-202401.00002' not in papers  # Figures already complete
        finally:
            conn.close()

    def test_work_queue_scope_month(self, sample_orchestrator_papers):
        """Test work queue generation with month scope."""
        queue = get_work_queue(scope='month', target='202401', force=False)

        # Should exclude complete papers but include pending and zombie
        assert 'chinaxiv-202401.00001' in queue
        assert 'chinaxiv-202401.00004' in queue  # Zombie
        assert 'chinaxiv-202401.00002' not in queue  # Complete

    def test_work_queue_scope_list(self, sample_orchestrator_papers):
        """Test work queue generation with list scope."""
        queue = get_work_queue(
            scope='list',
            target='chinaxiv-202401.00001,chinaxiv-202401.00002',
            force=False
        )

        # 00001 is pending (included), 00002 is complete (excluded)
        assert 'chinaxiv-202401.00001' in queue
        assert 'chinaxiv-202401.00002' not in queue

    def test_work_queue_force_mode(self, sample_orchestrator_papers):
        """Test work queue with force flag includes all papers."""
        queue = get_work_queue(scope='month', target='202401', force=True)

        # Force mode should include ALL papers in the month
        assert len(queue) == 6
        assert 'chinaxiv-202401.00002' in queue  # Even complete papers

    def test_work_queue_smart_resume(self, sample_orchestrator_papers):
        """Test smart-resume scope finds pending and zombie papers."""
        queue = get_work_queue(scope='smart-resume', target=None, force=False)

        # Should find pending and zombie papers across all months
        assert 'chinaxiv-202401.00001' in queue
        assert 'chinaxiv-202401.00004' in queue  # Zombie
        assert 'chinaxiv-202402.00001' in queue

        # Should NOT find complete or currently-processing papers
        assert 'chinaxiv-202401.00002' not in queue  # Complete
        assert 'chinaxiv-202401.00003' not in queue  # Processing (not zombie)


# ============================================================================
# Test: Paper Locking
# ============================================================================

class TestPaperLocking:
    """Tests for paper locking and zombie detection."""

    def test_acquire_lock_success(self, sample_orchestrator_papers):
        """Test successfully acquiring lock on pending paper."""
        conn = get_db_connection()
        try:
            # Paper 00001 is pending - should acquire lock
            success = acquire_paper_lock(conn, 'chinaxiv-202401.00001')
            assert success is True

            # Verify status changed
            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['processing_status'] == 'processing'
            assert status['processing_started_at'] is not None
        finally:
            conn.close()

    def test_acquire_lock_already_processing(self, sample_orchestrator_papers):
        """Test lock acquisition fails on paper being processed."""
        conn = get_db_connection()
        try:
            # Paper 00003 is currently processing (not zombie)
            success = acquire_paper_lock(conn, 'chinaxiv-202401.00003')
            assert success is False
        finally:
            conn.close()

    def test_acquire_lock_zombie_recovery(self, sample_orchestrator_papers):
        """Test acquiring lock on zombie paper."""
        conn = get_db_connection()
        try:
            # Paper 00004 is a zombie (processing > 4 hours) - should acquire
            success = acquire_paper_lock(conn, 'chinaxiv-202401.00004')
            assert success is True

            # Verify status updated
            status = get_paper_status(conn, 'chinaxiv-202401.00004')
            assert status['processing_status'] == 'processing'
            # processing_started_at should be updated to NOW
            age = datetime.now(timezone.utc) - status['processing_started_at']
            assert age.total_seconds() < 60  # Should be recent
        finally:
            conn.close()

    def test_double_lock_fails(self, sample_orchestrator_papers):
        """Test that acquiring lock twice on same paper fails."""
        conn1 = get_db_connection()
        conn2 = get_db_connection()
        try:
            # First lock should succeed
            success1 = acquire_paper_lock(conn1, 'chinaxiv-202401.00001')
            assert success1 is True

            # Second lock should fail
            success2 = acquire_paper_lock(conn2, 'chinaxiv-202401.00001')
            assert success2 is False
        finally:
            conn1.close()
            conn2.close()

    def test_release_lock(self, sample_orchestrator_papers):
        """Test releasing a paper lock."""
        conn = get_db_connection()
        try:
            # Acquire lock
            acquire_paper_lock(conn, 'chinaxiv-202401.00001')

            # Release lock
            release_paper_lock(conn, 'chinaxiv-202401.00001')

            # Verify status is back to pending
            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['processing_status'] == 'pending'
            assert status['processing_started_at'] is None
        finally:
            conn.close()


# ============================================================================
# Test: Stage Status Updates
# ============================================================================

class TestStageStatus:
    """Tests for stage status tracking."""

    def test_update_text_status_complete(self, sample_orchestrator_papers):
        """Test marking text stage as complete."""
        conn = get_db_connection()
        try:
            update_stage_status(conn, 'chinaxiv-202401.00001', 'text', 'complete')

            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['text_status'] == 'complete'
            assert status['text_completed_at'] is not None
        finally:
            conn.close()

    def test_update_figures_status_complete(self, sample_orchestrator_papers):
        """Test marking figures stage as complete."""
        conn = get_db_connection()
        try:
            update_stage_status(conn, 'chinaxiv-202401.00001', 'figures', 'complete')

            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['figures_status'] == 'complete'
            assert status['figures_completed_at'] is not None
        finally:
            conn.close()

    def test_update_pdf_status_skipped(self, sample_orchestrator_papers):
        """Test marking PDF stage as skipped."""
        conn = get_db_connection()
        try:
            update_stage_status(conn, 'chinaxiv-202401.00001', 'pdf', 'skipped')

            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['pdf_status'] == 'skipped'
            # completed_at should NOT be set for skipped
            assert status['pdf_completed_at'] is None
        finally:
            conn.close()

    def test_mark_paper_complete(self, sample_orchestrator_papers):
        """Test marking paper as fully complete."""
        conn = get_db_connection()
        try:
            # First acquire lock
            acquire_paper_lock(conn, 'chinaxiv-202401.00001')

            # Then mark complete
            mark_paper_complete(conn, 'chinaxiv-202401.00001')

            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['processing_status'] == 'complete'
            assert status['processing_started_at'] is None
            assert status['processing_error'] is None
        finally:
            conn.close()

    def test_mark_paper_failed(self, sample_orchestrator_papers):
        """Test marking paper as failed with error."""
        conn = get_db_connection()
        try:
            mark_paper_failed(conn, 'chinaxiv-202401.00001', 'Test error message')

            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['processing_status'] == 'failed'
            assert status['processing_error'] == 'Test error message'
        finally:
            conn.close()

    def test_mark_paper_failed_truncates_long_errors(self, sample_orchestrator_papers):
        """Test that very long error messages are truncated."""
        conn = get_db_connection()
        try:
            long_error = 'x' * 1000  # 1000 character error
            mark_paper_failed(conn, 'chinaxiv-202401.00001', long_error)

            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert len(status['processing_error']) <= 500
        finally:
            conn.close()


# ============================================================================
# Test: Paper Processing
# ============================================================================

class TestProcessPaper:
    """Tests for the process_paper function."""

    @patch('src.orchestrator.run_harvest')
    @patch('src.orchestrator.run_text_translation')
    @patch('src.orchestrator.run_figure_translation')
    @patch('src.orchestrator.run_pdf_generation')
    @patch('src.orchestrator.run_post_processing')
    def test_process_paper_full_success(
        self,
        mock_post,
        mock_pdf,
        mock_figures,
        mock_text,
        mock_harvest,
        sample_orchestrator_papers
    ):
        """Test successful full pipeline processing."""
        # Configure mocks
        mock_harvest.return_value = True
        mock_text.return_value = True
        mock_figures.return_value = True
        mock_pdf.return_value = True
        mock_post.return_value = True

        # Process paper
        result = process_paper(
            'chinaxiv-202401.00001',
            stages=['harvest', 'text', 'figures', 'pdf', 'post']
        )

        # Verify result
        assert result.status == 'success'
        assert result.error is None
        assert 'harvest' in result.stages_completed
        assert 'text' in result.stages_completed
        assert 'figures' in result.stages_completed
        assert 'post' in result.stages_completed

        # Verify mocks called
        mock_harvest.assert_called_once()
        mock_text.assert_called_once()
        mock_figures.assert_called_once()

        # Verify DB status
        conn = get_db_connection()
        try:
            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['processing_status'] == 'complete'
            assert status['text_status'] == 'complete'
            assert status['figures_status'] == 'complete'
        finally:
            conn.close()

    @patch('src.orchestrator.run_harvest')
    def test_process_paper_harvest_fails(
        self,
        mock_harvest,
        sample_orchestrator_papers
    ):
        """Test handling of harvest failure."""
        mock_harvest.return_value = False

        result = process_paper(
            'chinaxiv-202401.00001',
            stages=['harvest', 'text']
        )

        assert result.status == 'failed'
        assert 'harvest' in result.error

        # Verify DB marked as failed
        conn = get_db_connection()
        try:
            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['processing_status'] == 'failed'
        finally:
            conn.close()

    @patch('src.orchestrator.run_harvest')
    @patch('src.orchestrator.run_text_translation')
    def test_process_paper_text_fails(
        self,
        mock_text,
        mock_harvest,
        sample_orchestrator_papers
    ):
        """Test handling of text translation failure."""
        mock_harvest.return_value = True
        mock_text.side_effect = RuntimeError("Translation API error")

        result = process_paper(
            'chinaxiv-202401.00001',
            stages=['harvest', 'text']
        )

        assert result.status == 'failed'
        assert 'text' in result.error
        assert 'Translation API error' in result.error

        # Verify DB status
        conn = get_db_connection()
        try:
            status = get_paper_status(conn, 'chinaxiv-202401.00001')
            assert status['processing_status'] == 'failed'
            assert status['text_status'] == 'failed'
        finally:
            conn.close()

    def test_process_paper_skips_already_processing(self, sample_orchestrator_papers):
        """Test that paper being processed by another worker is skipped."""
        # Paper 00003 is already processing (not zombie)
        result = process_paper(
            'chinaxiv-202401.00003',
            stages=['harvest', 'text']
        )

        assert result.status == 'skipped'
        assert result.error is None

    @patch('src.orchestrator.run_harvest')
    @patch('src.orchestrator.run_text_translation')
    def test_process_paper_skips_complete_stages(
        self,
        mock_text,
        mock_harvest,
        sample_orchestrator_papers
    ):
        """Test that already-complete stages are skipped."""
        mock_harvest.return_value = True
        mock_text.return_value = True

        # Paper 00005 already has text complete
        process_paper(
            'chinaxiv-202401.00005',
            stages=['harvest', 'text', 'figures']
        )

        # Text stage should be skipped (was already complete)
        # But mock_text should NOT be called
        mock_text.assert_not_called()

    @patch('src.orchestrator.run_harvest')
    @patch('src.orchestrator.run_text_translation')
    def test_process_paper_text_only(
        self,
        mock_text,
        mock_harvest,
        sample_orchestrator_papers
    ):
        """Test text-only processing mode."""
        mock_harvest.return_value = True
        mock_text.return_value = True

        result = process_paper(
            'chinaxiv-202401.00001',
            stages=['harvest', 'text', 'post']
        )

        assert result.status == 'success'
        assert 'text' in result.stages_completed
        assert 'figures' not in result.stages_completed


# ============================================================================
# Test: Orchestrator Integration
# ============================================================================

class TestOrchestratorIntegration:
    """Integration tests for run_orchestrator."""

    @patch('src.orchestrator.run_harvest')
    @patch('src.orchestrator.run_text_translation')
    @patch('src.orchestrator.run_figure_translation')
    @patch('src.orchestrator.run_pdf_generation')
    @patch('src.orchestrator.run_post_processing')
    @patch('src.orchestrator.pipeline_started')
    @patch('src.orchestrator.pipeline_complete')
    def test_orchestrator_processes_batch(
        self,
        mock_alert_complete,
        mock_alert_start,
        mock_post,
        mock_pdf,
        mock_figures,
        mock_text,
        mock_harvest,
        sample_orchestrator_papers
    ):
        """Test orchestrator processes a batch of papers."""
        # Configure mocks
        mock_harvest.return_value = True
        mock_text.return_value = True
        mock_figures.return_value = True
        mock_pdf.return_value = True
        mock_post.return_value = True

        # Run orchestrator
        stats = run_orchestrator(
            scope='list',
            target='chinaxiv-202401.00001,chinaxiv-202402.00001',
            workers=1,
        )

        # Verify stats
        assert stats.total == 2
        assert stats.success == 2
        assert stats.failed == 0

        # Verify alerts sent
        mock_alert_start.assert_called_once()
        mock_alert_complete.assert_called_once()

    @patch('src.orchestrator.run_harvest')
    @patch('src.orchestrator.run_text_translation')
    @patch('src.orchestrator.pipeline_started')
    @patch('src.orchestrator.pipeline_complete')
    def test_orchestrator_handles_partial_failure(
        self,
        mock_alert_complete,
        mock_alert_start,
        mock_text,
        mock_harvest,
        sample_orchestrator_papers
    ):
        """Test orchestrator handles mix of success and failure."""
        mock_harvest.return_value = True

        # First paper succeeds, second fails
        call_count = [0]
        def text_side_effect(paper_id, dry_run=False):
            call_count[0] += 1
            if call_count[0] == 1:
                return True
            raise RuntimeError("Translation failed")

        mock_text.side_effect = text_side_effect

        stats = run_orchestrator(
            scope='list',
            target='chinaxiv-202401.00001,chinaxiv-202402.00001',
            workers=1,
            text_only=True,
        )

        assert stats.success == 1
        assert stats.failed == 1
        assert len(stats.errors) == 1

    @patch('src.orchestrator.run_harvest')
    @patch('src.orchestrator.run_text_translation')
    @patch('src.orchestrator.run_figure_translation')
    @patch('src.orchestrator.run_pdf_generation')
    @patch('src.orchestrator.run_post_processing')
    @patch('src.orchestrator.pipeline_started')
    @patch('src.orchestrator.pipeline_complete')
    def test_orchestrator_dry_run(
        self,
        mock_alert_complete,
        mock_alert_start,
        mock_post,
        mock_pdf,
        mock_figures,
        mock_text,
        mock_harvest,
        sample_orchestrator_papers
    ):
        """Test orchestrator dry run doesn't modify data."""
        mock_harvest.return_value = True
        mock_text.return_value = True
        mock_figures.return_value = True
        mock_pdf.return_value = True
        mock_post.return_value = True

        # Run with dry_run
        run_orchestrator(
            scope='list',
            target='chinaxiv-202401.00001',
            dry_run=True,
            workers=1,
        )

        # Dry run should still call functions (they handle dry_run internally)
        mock_text.assert_called_once_with('chinaxiv-202401.00001', dry_run=True)

    def test_orchestrator_empty_queue(self, sample_orchestrator_papers):
        """Test orchestrator with no papers to process."""
        stats = run_orchestrator(
            scope='month',
            target='202412',  # Month with no papers
            workers=1,
        )

        assert stats.total == 0
        assert stats.success == 0
        assert stats.failed == 0


# ============================================================================
# Test: Edge Cases and Error Handling
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_invalid_scope(self, sample_orchestrator_papers):
        """Test invalid scope raises error."""
        with pytest.raises(ValueError, match="Unknown scope"):
            get_work_queue(scope='invalid', target='202401')

    def test_invalid_month_format(self, sample_orchestrator_papers):
        """Test invalid month format raises error."""
        with pytest.raises(ValueError, match="Month must be YYYYMM"):
            get_work_queue(scope='month', target='2024-01')

    def test_missing_file(self, sample_orchestrator_papers):
        """Test missing file raises error."""
        with pytest.raises(ValueError, match="File not found"):
            get_work_queue(scope='file', target='/nonexistent/file.txt')

    def test_empty_list(self, sample_orchestrator_papers):
        """Test empty list scope raises error."""
        with pytest.raises(ValueError, match="requires comma-separated"):
            get_work_queue(scope='list', target='')

    def test_paper_not_in_db(self, sample_orchestrator_papers):
        """Test processing paper not in database."""
        # This should still work - paper will be in queue but may fail at processing
        queue = get_work_queue(
            scope='list',
            target='chinaxiv-999999.99999',
            force=True
        )
        assert 'chinaxiv-999999.99999' in queue

    def test_concurrent_zombie_recovery(self, sample_orchestrator_papers):
        """Test that only one worker can recover a zombie."""
        conn1 = get_db_connection()
        conn2 = get_db_connection()
        try:
            # Both try to acquire zombie paper at once
            result1 = acquire_paper_lock(conn1, 'chinaxiv-202401.00004')
            result2 = acquire_paper_lock(conn2, 'chinaxiv-202401.00004')

            # Exactly one should succeed
            assert (result1 and not result2) or (not result1 and result2)
        finally:
            conn1.close()
            conn2.close()
