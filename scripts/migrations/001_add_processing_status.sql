-- Migration: Add processing status columns for pipeline orchestrator
-- Created: 2024-12-10
-- Purpose: Track paper processing state for idempotent pipeline execution
--
-- This migration adds columns to track:
-- 1. Overall processing status (pending/processing/complete/failed)
-- 2. Per-stage completion status (text, figures, pdf)
-- 3. Timestamps for monitoring and zombie detection
--
-- After applying, run scripts/backfill_state_from_b2.py to populate from B2

-- ============================================================================
-- Processing Status (for the orchestrator)
-- ============================================================================

-- Overall processing state
ALTER TABLE papers ADD COLUMN IF NOT EXISTS processing_status VARCHAR(20) DEFAULT 'pending';
COMMENT ON COLUMN papers.processing_status IS 'Pipeline processing state: pending, processing, complete, failed';

-- When processing started (for zombie detection - 4 hour timeout)
ALTER TABLE papers ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN papers.processing_started_at IS 'When current processing attempt started (null if not processing)';

-- Error message if processing failed
ALTER TABLE papers ADD COLUMN IF NOT EXISTS processing_error TEXT;
COMMENT ON COLUMN papers.processing_error IS 'Error message from most recent failed processing attempt';

-- ============================================================================
-- Stage Completion Status (for idempotency)
-- ============================================================================

-- Text translation stage
ALTER TABLE papers ADD COLUMN IF NOT EXISTS text_status VARCHAR(20) DEFAULT 'pending';
COMMENT ON COLUMN papers.text_status IS 'Text translation state: pending, processing, complete, failed';

ALTER TABLE papers ADD COLUMN IF NOT EXISTS text_completed_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN papers.text_completed_at IS 'When text translation completed successfully';

-- Figure translation stage
ALTER TABLE papers ADD COLUMN IF NOT EXISTS figures_status VARCHAR(20) DEFAULT 'pending';
COMMENT ON COLUMN papers.figures_status IS 'Figure translation state: pending, processing, complete, failed';

ALTER TABLE papers ADD COLUMN IF NOT EXISTS figures_completed_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN papers.figures_completed_at IS 'When figure translation completed successfully';

-- PDF generation stage
ALTER TABLE papers ADD COLUMN IF NOT EXISTS pdf_status VARCHAR(20) DEFAULT 'pending';
COMMENT ON COLUMN papers.pdf_status IS 'PDF generation state: pending, processing, complete, failed';

ALTER TABLE papers ADD COLUMN IF NOT EXISTS pdf_completed_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN papers.pdf_completed_at IS 'When PDF generation completed successfully';

-- ============================================================================
-- Source Data Tracking (what we have vs what we need)
-- ============================================================================

-- Track if we have the Chinese PDF in B2
ALTER TABLE papers ADD COLUMN IF NOT EXISTS has_chinese_pdf BOOLEAN DEFAULT FALSE;
COMMENT ON COLUMN papers.has_chinese_pdf IS 'Whether Chinese source PDF exists in B2 storage';

-- Track if we have the English PDF in B2 (different from english_pdf_url which is the URL)
ALTER TABLE papers ADD COLUMN IF NOT EXISTS has_english_pdf BOOLEAN DEFAULT FALSE;
COMMENT ON COLUMN papers.has_english_pdf IS 'Whether English translated PDF exists in B2 storage';

-- ============================================================================
-- Indexes for Queue Queries
-- ============================================================================

-- Primary queue query: find papers needing work (pending or zombie)
CREATE INDEX IF NOT EXISTS idx_papers_processing_queue
    ON papers (processing_status, processing_started_at)
    WHERE processing_status IN ('pending', 'processing');

-- Index for filtering by text status
CREATE INDEX IF NOT EXISTS idx_papers_text_status
    ON papers (text_status)
    WHERE text_status != 'complete';

-- Index for filtering by figures status
CREATE INDEX IF NOT EXISTS idx_papers_figures_status
    ON papers (figures_status)
    WHERE figures_status != 'complete';

-- Index for filtering by pdf status
CREATE INDEX IF NOT EXISTS idx_papers_pdf_status
    ON papers (pdf_status)
    WHERE pdf_status != 'complete';

-- Composite index for orchestrator queue queries
CREATE INDEX IF NOT EXISTS idx_papers_orchestrator_queue
    ON papers (processing_status, text_status, figures_status, pdf_status);

-- ============================================================================
-- Constraints
-- ============================================================================

-- Ensure processing_status is a valid value
ALTER TABLE papers DROP CONSTRAINT IF EXISTS chk_processing_status;
ALTER TABLE papers ADD CONSTRAINT chk_processing_status
    CHECK (processing_status IN ('pending', 'processing', 'complete', 'failed'));

-- Ensure text_status is a valid value
ALTER TABLE papers DROP CONSTRAINT IF EXISTS chk_text_status;
ALTER TABLE papers ADD CONSTRAINT chk_text_status
    CHECK (text_status IN ('pending', 'processing', 'complete', 'failed', 'skipped'));

-- Ensure figures_status is a valid value
ALTER TABLE papers DROP CONSTRAINT IF EXISTS chk_figures_status;
ALTER TABLE papers ADD CONSTRAINT chk_figures_status
    CHECK (figures_status IN ('pending', 'processing', 'complete', 'failed', 'skipped'));

-- Ensure pdf_status is a valid value
ALTER TABLE papers DROP CONSTRAINT IF EXISTS chk_pdf_status;
ALTER TABLE papers ADD CONSTRAINT chk_pdf_status
    CHECK (pdf_status IN ('pending', 'processing', 'complete', 'failed', 'skipped'));

-- ============================================================================
-- Migration Metadata
-- ============================================================================

-- Create migrations table if it doesn't exist
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(50) PRIMARY KEY,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Record this migration
INSERT INTO schema_migrations (version) VALUES ('001_add_processing_status')
    ON CONFLICT (version) DO NOTHING;
