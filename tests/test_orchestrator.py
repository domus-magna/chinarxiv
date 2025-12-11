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

    Also sets DATABASE_URL environment variable for orchestrator functions.
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

    # Set DATABASE_URL for orchestrator to use
    old_db_url = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = test_database_schema

    yield test_database_schema

    # Restore old DATABASE_URL or remove it
    if old_db_url is not None:
        os.environ['DATABASE_URL'] = old_db_url
    elif 'DATABASE_URL' in os.environ:
        del os.environ['DATABASE_URL']


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
        # Failed paper (recent - should NOT auto-retry by default)
        {
            'id': 'chinaxiv-202401.00006',
            'title_en': 'Test Paper 6 - Failed Recent',
            'abstract_en': 'Abstract 6',
            'creators_en': '["Author 6"]',
            'date': '2024-01-20T10:00:00',
            'processing_status': 'failed',
            'processing_started_at': datetime.now(timezone.utc) - timedelta(days=2),
            'processing_error': 'Previous error message',
            'text_status': 'failed',
            'figures_status': 'pending',
            'pdf_status': 'pending',
        },
        # Failed paper (old - should auto-retry after 7 days)
        {
            'id': 'chinaxiv-202401.00007',
            'title_en': 'Test Paper 7 - Failed Old',
            'abstract_en': 'Abstract 7',
            'creators_en': '["Author 7"]',
            'date': '2024-01-21T10:00:00',
            'processing_status': 'failed',
            'processing_started_at': datetime.now(timezone.utc) - timedelta(days=10),
            'processing_error': 'Old error message',
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
            # January 2024 should have 7 papers
            jan_papers = get_papers_by_month(conn, '202401')
            assert len(jan_papers) == 7
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
        assert len(queue) == 7
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


# ============================================================================
# Test: Auto-Retry Failed Papers
# ============================================================================

class TestAutoRetryFailed:
    """Tests for auto-retry of failed papers after 7 days."""

    def test_recent_failed_not_auto_retried(self, sample_orchestrator_papers):
        """Recent failed papers (< 7 days) should NOT be auto-retried."""
        conn = get_db_connection()
        try:
            papers = get_papers_needing_work(conn)

            # chinaxiv-202401.00006 failed 2 days ago - should NOT be included
            assert 'chinaxiv-202401.00006' not in papers
        finally:
            conn.close()

    def test_old_failed_auto_retried(self, sample_orchestrator_papers):
        """Failed papers older than 7 days should be auto-retried."""
        conn = get_db_connection()
        try:
            papers = get_papers_needing_work(conn)

            # chinaxiv-202401.00007 failed 10 days ago - should be included
            assert 'chinaxiv-202401.00007' in papers
        finally:
            conn.close()

    def test_include_failed_flag_includes_all_failed(self, sample_orchestrator_papers):
        """include_failed=True should include ALL failed papers."""
        conn = get_db_connection()
        try:
            papers = get_papers_needing_work(conn, include_failed=True)

            # Both failed papers should be included
            assert 'chinaxiv-202401.00006' in papers  # Recent failed
            assert 'chinaxiv-202401.00007' in papers  # Old failed
        finally:
            conn.close()

    def test_work_queue_include_failed(self, sample_orchestrator_papers):
        """Test work queue with include_failed flag."""
        queue = get_work_queue(
            scope='smart-resume',
            target=None,
            include_failed=True
        )

        # Both failed papers should be in queue
        assert 'chinaxiv-202401.00006' in queue
        assert 'chinaxiv-202401.00007' in queue


# ============================================================================
# Test: Run Harvest with DB pdf_url
# ============================================================================

class TestRunHarvestWithPdfUrl:
    """Tests for run_harvest using pdf_url from database."""

    def test_harvest_existing_pdf_returns_true(self, sample_orchestrator_papers, tmp_path):
        """If PDF exists locally, harvest should succeed without download."""
        from src.orchestrator import run_harvest

        # Create a fake PDF file
        pdf_dir = tmp_path / "data" / "pdfs"
        pdf_dir.mkdir(parents=True)
        pdf_file = pdf_dir / "chinaxiv-202401.00001.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake pdf content")

        # Temporarily change working directory
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = run_harvest('chinaxiv-202401.00001')
            assert result is True
        finally:
            os.chdir(original_cwd)

    def test_harvest_dry_run_succeeds_without_download(self, sample_orchestrator_papers):
        """Dry run should succeed without actually downloading."""
        from src.orchestrator import run_harvest

        result = run_harvest('chinaxiv-202401.00001', dry_run=True)
        assert result is True

    def test_harvest_missing_paper_returns_false(self, sample_orchestrator_papers):
        """Harvest for non-existent paper should fail gracefully."""
        from src.orchestrator import run_harvest

        # Paper not in DB should return False
        result = run_harvest('chinaxiv-999999.99999')
        assert result is False

    def test_harvest_missing_pdf_url_returns_false(self, sample_orchestrator_papers):
        """Harvest should fail if paper has no pdf_url in DB."""
        from src.orchestrator import run_harvest

        # Our test papers don't have pdf_url set, so this should fail
        # (assuming PDF doesn't exist locally and B2 download fails)
        with patch('src.orchestrator.download_pdf_from_b2', return_value=False):
            result = run_harvest('chinaxiv-202401.00001')
            # Should fail because no pdf_url in test data
            assert result is False

    def test_download_pdf_from_b2_missing_credentials(self, sample_orchestrator_papers):
        """download_pdf_from_b2 should return False if credentials missing."""
        from src.orchestrator import download_pdf_from_b2

        # Clear B2 environment variables
        import os
        env_vars = ['BACKBLAZE_BUCKET', 'BACKBLAZE_S3_ENDPOINT', 'BACKBLAZE_KEY_ID',
                    'BACKBLAZE_APPLICATION_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY']
        saved = {k: os.environ.pop(k, None) for k in env_vars}

        try:
            result = download_pdf_from_b2('chinaxiv-202401.00001', '/tmp/test.pdf')
            assert result is False
        finally:
            # Restore environment
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


# ============================================================================
# Test: Discovery Functions
# ============================================================================

class TestDiscovery:
    """Tests for paper discovery (harvest) functionality."""

    def test_insert_paper_if_new_inserts_new_paper(self, orchestrator_test_database):
        """insert_paper_if_new should insert a paper that doesn't exist."""
        from src.orchestrator import insert_paper_if_new, get_paper_status

        record = {
            'id': 'chinaxiv-202501.00001',
            'title': 'Test Paper Title',
            'abstract': 'Test abstract',
            'creators': ['Author One', 'Author Two'],
            'subjects': ['Physics', 'Math'],
            'date': '2025-01-15T00:00:00Z',
            'source_url': 'https://chinaxiv.org/abs/202501.00001',
            'pdf_url': 'https://chinaxiv.org/pdf/202501.00001',
        }

        conn = get_db_connection()
        try:
            # Should return True for new paper
            result = insert_paper_if_new(conn, record)
            conn.commit()
            assert result is True

            # Verify paper exists
            status = get_paper_status(conn, 'chinaxiv-202501.00001')
            assert status is not None
            assert status['processing_status'] == 'pending'

            # Verify subjects were inserted into paper_subjects table
            cursor = conn.cursor()
            cursor.execute(
                "SELECT subject FROM paper_subjects WHERE paper_id = %s ORDER BY subject",
                ('chinaxiv-202501.00001',)
            )
            subjects = [row['subject'] for row in cursor.fetchall()]
            assert subjects == ['Math', 'Physics'], f"Expected ['Math', 'Physics'], got {subjects}"
        finally:
            conn.close()

    def test_insert_paper_if_new_skips_existing_paper(self, sample_orchestrator_papers):
        """insert_paper_if_new should skip papers that already exist."""
        from src.orchestrator import insert_paper_if_new

        # Try to insert a paper that already exists from fixtures
        record = {
            'id': 'chinaxiv-202401.00001',  # Already exists
            'title': 'Different Title',
            'abstract': 'Different abstract',
            'creators': ['Different Author'],
            'subjects': ['Different Subject'],
            'date': '2024-01-15T00:00:00Z',
            'source_url': 'https://chinaxiv.org/abs/202401.00001',
            'pdf_url': 'https://chinaxiv.org/pdf/202401.00001',
        }

        conn = get_db_connection()
        try:
            # Should return False for existing paper
            result = insert_paper_if_new(conn, record)
            assert result is False
        finally:
            conn.close()

    def test_insert_paper_stores_chinese_in_cn_columns(self, orchestrator_test_database):
        """insert_paper_if_new should store Chinese metadata in _cn columns."""
        from src.orchestrator import insert_paper_if_new

        # Record with Chinese metadata (as returned by scraper)
        record = {
            'id': 'chinaxiv-202501.00002',
            'title': '中文论文标题',
            'abstract': '这是中文摘要。',
            'creators': ['张三', '李四'],
            'subjects': ['计算机科学', '人工智能'],
            'date': '2025-01-15T00:00:00Z',
            'source_url': 'https://chinaxiv.org/abs/202501.00002',
            'pdf_url': 'https://chinaxiv.org/pdf/202501.00002',
        }

        conn = get_db_connection()
        try:
            result = insert_paper_if_new(conn, record)
            conn.commit()
            assert result is True

            # Verify Chinese is stored in _cn columns
            cursor = conn.cursor()
            cursor.execute("""
                SELECT title_cn, abstract_cn, creators_cn, subjects_cn,
                       title_en, abstract_en, creators_en
                FROM papers WHERE id = %s
            """, ('chinaxiv-202501.00002',))
            row = cursor.fetchone()

            assert row['title_cn'] == '中文论文标题'
            assert row['abstract_cn'] == '这是中文摘要。'
            # creators_cn should be JSON list
            import json
            creators = row['creators_cn'] if isinstance(row['creators_cn'], list) else json.loads(row['creators_cn'])
            assert creators == ['张三', '李四']
            subjects = row['subjects_cn'] if isinstance(row['subjects_cn'], list) else json.loads(row['subjects_cn'])
            assert subjects == ['计算机科学', '人工智能']

            # Verify _en columns are NULL (not populated until translation)
            assert row['title_en'] is None
            assert row['abstract_en'] is None
            assert row['creators_en'] is None
        finally:
            conn.close()

    def test_run_discover_requires_brightdata_credentials(self, orchestrator_test_database):
        """run_discover should fail without BrightData credentials."""
        from src.orchestrator import run_discover

        # Clear BrightData environment variables
        env_vars = ['BRIGHTDATA_API_KEY', 'BRIGHTDATA_ZONE']
        saved = {k: os.environ.pop(k, None) for k in env_vars}

        try:
            with pytest.raises(RuntimeError, match="BRIGHTDATA"):
                run_discover('202501')
        finally:
            # Restore environment
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    @patch('src.harvest_chinaxiv_optimized.OptimizedChinaXivScraper')
    @patch('src.orchestrator.upload_records_to_b2')
    def test_run_discover_with_mocked_scraper(
        self,
        mock_upload,
        mock_scraper_class,
        orchestrator_test_database
    ):
        """run_discover should scrape, import to DB, and upload to B2."""
        from src.orchestrator import run_discover, get_paper_status

        # Set up BrightData credentials
        os.environ['BRIGHTDATA_API_KEY'] = 'test_key'
        os.environ['BRIGHTDATA_ZONE'] = 'test_zone'

        # Mock scraper instance
        mock_scraper = mock_scraper_class.return_value
        mock_scraper.extract_homepage_max_ids.return_value = {'202501': 5}
        mock_scraper.scrape_month_optimized.return_value = [
            {
                'id': 'chinaxiv-202501.00001',
                'title': 'Paper 1',
                'abstract': 'Abstract 1',
                'creators': ['Author 1'],
                'subjects': ['Subject 1'],
                'date': '2025-01-01T00:00:00Z',
                'source_url': 'https://chinaxiv.org/abs/202501.00001',
                'pdf_url': 'https://chinaxiv.org/pdf/202501.00001',
            },
            {
                'id': 'chinaxiv-202501.00002',
                'title': 'Paper 2',
                'abstract': 'Abstract 2',
                'creators': ['Author 2'],
                'subjects': ['Subject 2'],
                'date': '2025-01-02T00:00:00Z',
                'source_url': 'https://chinaxiv.org/abs/202501.00002',
                'pdf_url': 'https://chinaxiv.org/pdf/202501.00002',
            },
        ]
        mock_upload.return_value = True

        try:
            # Run discovery
            new_ids = run_discover('202501')

            # Should have found 2 new papers
            assert len(new_ids) == 2
            assert 'chinaxiv-202501.00001' in new_ids
            assert 'chinaxiv-202501.00002' in new_ids

            # Verify papers are in database
            conn = get_db_connection()
            try:
                status1 = get_paper_status(conn, 'chinaxiv-202501.00001')
                status2 = get_paper_status(conn, 'chinaxiv-202501.00002')
                assert status1 is not None
                assert status2 is not None
                assert status1['processing_status'] == 'pending'
                assert status2['processing_status'] == 'pending'
            finally:
                conn.close()

            # Verify B2 upload was called
            mock_upload.assert_called_once()

        finally:
            # Cleanup
            os.environ.pop('BRIGHTDATA_API_KEY', None)
            os.environ.pop('BRIGHTDATA_ZONE', None)

    def test_run_discover_dry_run_doesnt_modify_db(self, orchestrator_test_database):
        """run_discover with dry_run=True should not modify database."""
        from src.orchestrator import run_discover, get_paper_status

        # Set up credentials
        os.environ['BRIGHTDATA_API_KEY'] = 'test_key'
        os.environ['BRIGHTDATA_ZONE'] = 'test_zone'

        with patch('src.harvest_chinaxiv_optimized.OptimizedChinaXivScraper') as mock_scraper_class:
            mock_scraper = mock_scraper_class.return_value
            mock_scraper.extract_homepage_max_ids.return_value = {'202501': 1}
            mock_scraper.scrape_month_optimized.return_value = [
                {
                    'id': 'chinaxiv-202501.00099',
                    'title': 'Dry Run Paper',
                    'abstract': 'Should not be in DB',
                    'creators': [],
                    'subjects': [],
                    'date': '2025-01-01T00:00:00Z',
                    'source_url': 'https://chinaxiv.org/abs/202501.00099',
                    'pdf_url': '',
                },
            ]

            try:
                # Run with dry_run=True
                new_ids = run_discover('202501', dry_run=True)

                # Should return the paper ID
                assert len(new_ids) == 1

                # But paper should NOT be in database
                conn = get_db_connection()
                try:
                    status = get_paper_status(conn, 'chinaxiv-202501.00099')
                    assert status == {}  # Empty dict means not found
                finally:
                    conn.close()

            finally:
                os.environ.pop('BRIGHTDATA_API_KEY', None)
                os.environ.pop('BRIGHTDATA_ZONE', None)

    def test_run_orchestrator_discover_scope(self, orchestrator_test_database):
        """run_orchestrator with scope=discover should call run_discover."""
        from src.orchestrator import run_orchestrator

        # Set up credentials
        os.environ['BRIGHTDATA_API_KEY'] = 'test_key'
        os.environ['BRIGHTDATA_ZONE'] = 'test_zone'

        with patch('src.orchestrator.run_discover') as mock_discover:
            mock_discover.return_value = ['chinaxiv-202501.00001', 'chinaxiv-202501.00002']

            try:
                stats = run_orchestrator(scope='discover', target='202501')

                # Should have called run_discover
                mock_discover.assert_called_once_with('202501', dry_run=False)

                # Stats should reflect discovery
                assert stats.total == 2
                assert stats.success == 2
                assert stats.failed == 0

            finally:
                os.environ.pop('BRIGHTDATA_API_KEY', None)
                os.environ.pop('BRIGHTDATA_ZONE', None)

    def test_run_orchestrator_discover_requires_target(self, orchestrator_test_database):
        """run_orchestrator with scope=discover should require target."""
        from src.orchestrator import run_orchestrator

        with pytest.raises(ValueError, match="YYYYMM"):
            run_orchestrator(scope='discover', target=None)

        with pytest.raises(ValueError, match="YYYYMM"):
            run_orchestrator(scope='discover', target='invalid')


class TestBackfillPreservesFailedStatus:
    """Tests for backfill_state_from_b2.py preserving failed status."""

    def test_backfill_preserves_failed_text_status(self, orchestrator_test_database):
        """backfill_database should preserve 'failed' status and not revert to 'pending'."""
        from scripts.backfill_state_from_b2 import backfill_database

        conn = get_db_connection()
        try:
            cursor = conn.cursor()

            # Insert a paper with text_status='failed'
            cursor.execute("""
                INSERT INTO papers (
                    id, title_en, abstract_en, processing_status, text_status,
                    figures_status, pdf_status
                ) VALUES (
                    'chinaxiv-202501.00099', 'Test Paper', 'Abstract',
                    'failed', 'failed', 'pending', 'pending'
                )
            """)
            conn.commit()

            # Create B2 state with NO text translation for this paper
            b2_state = {
                'chinese_pdfs': set(),
                'text_translations': set(),  # Paper has no text translation in B2
                'figures': set(),
                'english_pdfs': set(),
            }

            # Run backfill
            backfill_database(conn, b2_state, dry_run=False)

            # Verify the failed status is preserved, not reverted to 'pending'
            cursor.execute("""
                SELECT text_status, processing_status
                FROM papers WHERE id = 'chinaxiv-202501.00099'
            """)
            row = cursor.fetchone()

            assert row['text_status'] == 'failed', \
                f"Expected text_status='failed' to be preserved, got '{row['text_status']}'"
            assert row['processing_status'] == 'failed', \
                f"Expected processing_status='failed' to be preserved, got '{row['processing_status']}'"

        finally:
            conn.close()

    def test_backfill_updates_pending_to_complete_with_b2_file(self, orchestrator_test_database):
        """backfill_database should update 'pending' to 'complete' when B2 file exists."""
        from scripts.backfill_state_from_b2 import backfill_database

        conn = get_db_connection()
        try:
            cursor = conn.cursor()

            # Insert a paper with text_status='pending'
            cursor.execute("""
                INSERT INTO papers (
                    id, title_en, abstract_en, processing_status, text_status,
                    figures_status, pdf_status
                ) VALUES (
                    'chinaxiv-202501.00098', 'Test Paper 2', 'Abstract 2',
                    'pending', 'pending', 'pending', 'pending'
                )
            """)
            conn.commit()

            # Create B2 state WITH text translation for this paper
            b2_state = {
                'chinese_pdfs': set(),
                'text_translations': {'chinaxiv-202501.00098'},  # Has text in B2
                'figures': set(),
                'english_pdfs': set(),
            }

            # Run backfill
            backfill_database(conn, b2_state, dry_run=False)

            # Verify status is updated to 'complete'
            cursor.execute("""
                SELECT text_status FROM papers WHERE id = 'chinaxiv-202501.00098'
            """)
            row = cursor.fetchone()

            assert row['text_status'] == 'complete', \
                f"Expected text_status='complete' after backfill, got '{row['text_status']}'"

        finally:
            conn.close()
