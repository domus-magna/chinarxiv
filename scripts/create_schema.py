#!/usr/bin/env python3
"""
Create PostgreSQL schema for Railway database.

This script creates the schema only (no data migration).
Intended to be run via `railway run` to use internal database URL.

Usage:
    railway run python scripts/create_schema.py
"""

import psycopg2
import os
import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_schema(conn):
    """Create PostgreSQL schema with tables, indexes, and materialized views."""
    cursor = conn.cursor()

    # Papers table
    logger.info("Creating papers table...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS papers (
        id TEXT PRIMARY KEY,
        title_en TEXT NOT NULL,
        abstract_en TEXT,
        creators_en JSONB,
        date TIMESTAMP WITH TIME ZONE,
        has_figures BOOLEAN DEFAULT FALSE,
        has_full_text BOOLEAN DEFAULT FALSE,
        qa_status TEXT DEFAULT 'pass' CHECK (qa_status IN ('pass', 'pending', 'fail')),
        source_url TEXT,
        pdf_url TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Paper subjects table
    logger.info("Creating paper_subjects table...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_subjects (
        paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        subject TEXT NOT NULL,
        PRIMARY KEY (paper_id, subject)
    );
    """)

    # Full-text search column
    logger.info("Creating full-text search column...")
    cursor.execute("""
    ALTER TABLE papers ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title_en, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(abstract_en, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(creators_en::text, '')), 'C')
        ) STORED;
    """)

    # Indexes
    logger.info("Creating indexes...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_date ON papers(date DESC NULLS LAST);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_qa_status ON papers(qa_status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_has_figures ON papers(has_figures) WHERE has_figures = TRUE;")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_composite ON papers(date DESC, qa_status, has_figures);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects_paper_id ON paper_subjects(paper_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects_subject ON paper_subjects(subject);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_search ON papers USING GIN(search_vector);")

    # Materialized view for category counts
    logger.info("Creating category_counts materialized view...")
    cursor.execute("DROP MATERIALIZED VIEW IF EXISTS category_counts;")
    cursor.execute("""
    CREATE MATERIALIZED VIEW category_counts AS
    SELECT
        subject,
        COUNT(DISTINCT paper_id) AS paper_count
    FROM paper_subjects ps
    JOIN papers p ON ps.paper_id = p.id
    WHERE p.qa_status = 'pass'
    GROUP BY subject;
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_category_counts_subject ON category_counts(subject);")

    conn.commit()
    logger.info("Schema created successfully!")


def main():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable is required")
        sys.exit(1)

    logger.info(f"Connecting to database...")
    try:
        conn = psycopg2.connect(database_url)
        create_schema(conn)

        # Verify
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM papers;")
        count = cursor.fetchone()[0]
        logger.info(f"Papers count: {count}")

        conn.close()
        logger.info("Done!")
    except Exception as e:
        logger.error(f"Failed to create schema: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
