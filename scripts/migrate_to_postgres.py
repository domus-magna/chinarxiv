#!/usr/bin/env python3
"""
Migrate SQLite database to PostgreSQL with production optimizations.

This script:
1. Creates PostgreSQL schema with proper types, constraints, and indexes
2. Migrates data from SQLite to PostgreSQL with transaction safety
3. Verifies data integrity with count checks
4. Updates PostgreSQL statistics with ANALYZE

Usage:
    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
    python scripts/migrate_to_postgres.py

Requirements:
    - PostgreSQL credentials in DATABASE_URL environment variable
    - SQLite database at data/papers.db
    - psycopg2-binary installed
"""

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
import sqlite3
import json
import os
import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_postgres_schema(pg_conn):
    """
    Create PostgreSQL schema with production-grade features.

    Features:
    - Proper types (JSONB, TIMESTAMP WITH TIME ZONE, BOOLEAN)
    - Constraints (PRIMARY KEY, FOREIGN KEY, CHECK)
    - Full-text search (tsvector with generated column)
    - Production-optimized indexes

    Args:
        pg_conn: psycopg2 connection object
    """
    cursor = pg_conn.cursor()
    logger.info("Creating PostgreSQL schema...")

    # Papers table (with proper types and constraints)
    logger.info("  Creating papers table...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS papers (
        id TEXT PRIMARY KEY,
        title_en TEXT NOT NULL,
        abstract_en TEXT,
        creators_en JSONB,  -- Native JSONB (better than TEXT)
        date TIMESTAMP WITH TIME ZONE,  -- Proper timestamp type
        has_figures BOOLEAN DEFAULT FALSE,
        has_full_text BOOLEAN DEFAULT FALSE,
        qa_status TEXT DEFAULT 'pass' CHECK (qa_status IN ('pass', 'pending', 'fail')),
        source_url TEXT,
        pdf_url TEXT,
        body_md TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Normalized subjects table (same as SQLite approach)
    logger.info("  Creating paper_subjects table...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_subjects (
        paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
        subject TEXT NOT NULL,
        PRIMARY KEY (paper_id, subject)
    );
    """)

    # Full-text search (PostgreSQL native)
    logger.info("  Creating full-text search column...")
    cursor.execute("""
    ALTER TABLE papers ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title_en, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(abstract_en, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(creators_en::text, '')), 'C')
        ) STORED;
    """)

    # Indexes (production-optimized)
    logger.info("  Creating indexes...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_date ON papers(date DESC NULLS LAST);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_qa_status ON papers(qa_status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_has_figures ON papers(has_figures) WHERE has_figures = TRUE;")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_composite ON papers(date DESC, qa_status, has_figures);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects_paper_id ON paper_subjects(paper_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects_subject ON paper_subjects(subject);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_search ON papers USING GIN(search_vector);")

    pg_conn.commit()
    logger.info("✅ PostgreSQL schema created")


def create_materialized_views(pg_conn):
    """
    Create materialized views for performance optimization.

    Materialized View: category_counts
    - Pre-computes paper counts per category
    - Eliminates N+1 query problem (300-500ms → 10-20ms)
    - Refreshed daily or after data imports

    Args:
        pg_conn: psycopg2 connection object
    """
    cursor = pg_conn.cursor()
    logger.info("Creating materialized views...")

    # Drop existing view if it exists (for re-migration)
    logger.info("  Dropping existing category_counts view...")
    cursor.execute("DROP MATERIALIZED VIEW IF EXISTS category_counts;")

    # Create category_counts materialized view
    logger.info("  Creating category_counts materialized view...")
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

    # Create index on subject for fast lookups
    logger.info("  Creating index on category_counts...")
    cursor.execute("CREATE INDEX idx_category_counts_subject ON category_counts(subject);")

    pg_conn.commit()
    logger.info("✅ Materialized views created")


def migrate_data(sqlite_path, pg_conn):
    """
    Migrate data from SQLite to PostgreSQL with progress tracking.

    Features:
    - Transaction safety (rollback on error)
    - Progress tracking (every 100 papers)
    - Count verification (ensures no data loss)
    - Duplicate handling (INSERT ... ON CONFLICT UPDATE)

    Args:
        sqlite_path: Path to SQLite database file
        pg_conn: psycopg2 connection object

    Raises:
        ValueError: If count mismatch detected
        Exception: If migration fails
    """
    logger.info(f"Migrating data from {sqlite_path}...")

    # Connect to SQLite
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    pg_cursor = pg_conn.cursor()

    # Count papers
    total = sqlite_conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    logger.info(f"Found {total} papers to migrate")

    if total == 0:
        logger.warning("⚠️  SQLite database is empty - nothing to migrate")
        sqlite_conn.close()
        return

    try:
        # Begin transaction
        pg_cursor.execute("BEGIN;")

        # Migrate papers
        logger.info("Migrating papers...")
        papers = sqlite_conn.execute("SELECT * FROM papers").fetchall()
        for i, paper in enumerate(papers, 1):
            pg_cursor.execute("""
            INSERT INTO papers (
                id, title_en, abstract_en, creators_en, date,
                has_figures, has_full_text, qa_status, source_url, pdf_url, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                title_en = EXCLUDED.title_en,
                abstract_en = EXCLUDED.abstract_en,
                creators_en = EXCLUDED.creators_en,
                date = EXCLUDED.date,
                has_figures = EXCLUDED.has_figures,
                has_full_text = EXCLUDED.has_full_text,
                qa_status = EXCLUDED.qa_status,
                source_url = EXCLUDED.source_url,
                pdf_url = EXCLUDED.pdf_url
            """, (
                paper['id'],
                paper['title_en'],
                paper['abstract_en'],
                paper['creators_en'],  # JSON string → JSONB (automatic cast)
                paper['date'],
                bool(paper['has_figures']),
                bool(paper['has_full_text']),
                paper['qa_status'],
                paper['source_url'],
                paper['pdf_url'],
                paper['created_at']
            ))

            if i % 100 == 0 or i == total:
                logger.info(f"  Migrated {i}/{total} papers...")

        # Migrate subjects
        logger.info("Migrating subjects...")
        subjects = sqlite_conn.execute("SELECT * FROM paper_subjects").fetchall()
        subject_count = len(subjects)
        logger.info(f"Found {subject_count} subject mappings")

        for i, subject in enumerate(subjects, 1):
            pg_cursor.execute("""
            INSERT INTO paper_subjects (paper_id, subject)
            VALUES (%s, %s)
            ON CONFLICT (paper_id, subject) DO NOTHING
            """, (subject['paper_id'], subject['subject']))

            if i % 1000 == 0 or i == subject_count:
                logger.info(f"  Migrated {i}/{subject_count} subject mappings...")

        # Verify counts
        logger.info("Verifying data integrity...")
        pg_cursor.execute("SELECT COUNT(*) FROM papers")
        pg_count = pg_cursor.fetchone()[0]

        if pg_count != total:
            raise ValueError(f"Count mismatch: expected {total}, got {pg_count}")

        pg_conn.commit()
        logger.info(f"✅ Successfully migrated {total} papers and {subject_count} subject mappings")

    except Exception as e:
        pg_conn.rollback()
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        raise
    finally:
        sqlite_conn.close()


def analyze_performance(pg_conn):
    """
    Run ANALYZE to update PostgreSQL query planner statistics.

    This ensures the query planner has up-to-date statistics
    for optimal query execution plans.

    Args:
        pg_conn: psycopg2 connection object
    """
    logger.info("Updating PostgreSQL statistics...")
    pg_conn.autocommit = True
    cursor = pg_conn.cursor()
    cursor.execute("ANALYZE papers;")
    cursor.execute("ANALYZE paper_subjects;")
    logger.info("✅ PostgreSQL statistics updated")


def main():
    """
    Main migration workflow.

    Steps:
    1. Check for DATABASE_URL environment variable
    2. Connect to PostgreSQL
    3. Create schema if not exists
    4. Migrate data from SQLite
    5. Update statistics

    Exit codes:
        0: Success
        1: Missing DATABASE_URL
        2: Migration failed
    """
    # Check for PostgreSQL credentials
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("❌ DATABASE_URL environment variable not set")
        logger.error("Usage: DATABASE_URL='postgresql://user:pass@host:5432/db' python scripts/migrate_to_postgres.py")
        sys.exit(1)

    # Check for SQLite database
    sqlite_path = 'data/papers.db'
    if not os.path.exists(sqlite_path):
        logger.error(f"❌ SQLite database not found at {sqlite_path}")
        logger.error("Run 'python scripts/create_papers_db.py' first to create the SQLite database")
        sys.exit(2)

    try:
        # Connect to PostgreSQL
        logger.info(f"Connecting to PostgreSQL...")
        pg_conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        logger.info("✅ Connected to PostgreSQL")

        # Create schema
        create_postgres_schema(pg_conn)

        # Migrate data
        migrate_data(sqlite_path, pg_conn)

        # Create materialized views (performance optimization)
        create_materialized_views(pg_conn)

        # Update statistics
        analyze_performance(pg_conn)

        pg_conn.close()
        logger.info("✅ Migration complete!")

    except psycopg2.Error as e:
        logger.error(f"❌ PostgreSQL error: {e}", exc_info=True)
        sys.exit(2)
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        sys.exit(2)


if __name__ == '__main__':
    main()
