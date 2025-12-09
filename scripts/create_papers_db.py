#!/usr/bin/env python3
"""
Database migration script: JSON files → SQLite database

Creates a production-ready SQLite database with:
- Normalized paper_subjects table for efficient category filtering
- FTS5 full-text search with automatic sync triggers
- Proper indexes for common queries
- Transaction safety with count verification

Usage:
    python scripts/create_papers_db.py

Creates: data/papers.db
"""

import sqlite3
import json
import sys
from pathlib import Path


def create_schema(db_path):
    """
    Create database schema with all tables, indexes, and triggers.

    Includes:
    - papers: Main table with paper metadata
    - paper_subjects: Normalized join table for subject filtering
    - papers_fts: FTS5 virtual table for full-text search
    - Triggers to keep FTS5 in sync with main table
    - Composite indexes for common query patterns
    """
    print("Creating database schema...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Main papers table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS papers (
        id TEXT PRIMARY KEY,
        title_en TEXT NOT NULL,
        abstract_en TEXT,
        creators_en TEXT,  -- JSON array as text
        date TEXT,         -- ISO format: 2022-01-15T12:00:00Z
        has_figures INTEGER DEFAULT 0,
        has_full_text INTEGER DEFAULT 0,
        qa_status TEXT DEFAULT 'pass',
        source_url TEXT,
        pdf_url TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Normalized subjects table for efficient filtering (Codex P1 fix)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_subjects (
        paper_id TEXT NOT NULL,
        subject TEXT NOT NULL,
        PRIMARY KEY (paper_id, subject),
        FOREIGN KEY (paper_id) REFERENCES papers(id)
    );
    """)

    # Indexes for fast filtering
    print("Creating indexes...")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_default ON papers(date DESC, qa_status, has_figures);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects ON paper_subjects(subject, paper_id);")

    # Full-text search (SQLite FTS5)
    print("Creating FTS5 virtual table...")
    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
        id UNINDEXED,
        title_en,
        abstract_en,
        creators_en,
        content='papers',
        content_rowid='rowid'
    );
    """)

    # FTS sync triggers (Codex P1 fix)
    print("Creating FTS sync triggers...")
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
      INSERT INTO papers_fts(rowid, id, title_en, abstract_en, creators_en)
      VALUES (new.rowid, new.id, new.title_en, new.abstract_en, new.creators_en);
    END;
    """)

    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
      DELETE FROM papers_fts WHERE rowid = old.rowid;
    END;
    """)

    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
      UPDATE papers_fts SET
        title_en = new.title_en,
        abstract_en = new.abstract_en,
        creators_en = new.creators_en
      WHERE rowid = new.rowid;
    END;
    """)

    conn.commit()
    conn.close()
    print("✅ Schema created successfully")


def import_translations(db_path, translations_dir="data/translated"):
    """
    Import JSON files to SQLite database with transaction safety and verification.

    Features:
    - Wrapped in transaction for atomicity
    - Populates normalized paper_subjects table
    - Verifies count matches file count
    - FTS table automatically synced via triggers

    Args:
        db_path: Path to SQLite database file
        translations_dir: Directory containing JSON paper files
    """
    print(f"\nImporting translations from {translations_dir}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Count JSON files first
    json_files = list(Path(translations_dir).glob("*.json"))
    expected_count = len(json_files)
    print(f"Found {expected_count} JSON files to import")

    if expected_count == 0:
        print("⚠️  No JSON files found. Skipping import.")
        conn.close()
        return

    try:
        # Wrap entire import in transaction (Codex P2 fix)
        cursor.execute("BEGIN TRANSACTION")

        for i, json_file in enumerate(json_files, 1):
            if i % 100 == 0:
                print(f"  Processed {i}/{expected_count}...")

            try:
                with open(json_file) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"⚠️  Skipping {json_file.name}: {e}")
                continue

            # Insert into main papers table (triggers handle FTS sync automatically)
            cursor.execute("""
            INSERT OR REPLACE INTO papers (
                id, title_en, abstract_en, creators_en,
                date, has_figures, has_full_text, qa_status, source_url, pdf_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('id'),
                data.get('title_en', ''),
                data.get('abstract_en', ''),
                json.dumps(data.get('creators_en', [])),
                data.get('date'),
                int(data.get('_has_translated_figures', False)),
                int(data.get('_has_full_body', False)),
                data.get('_qa_status', 'pass'),
                data.get('source_url', ''),
                data.get('pdf_url', '')
            ))

            # Populate normalized paper_subjects table (Codex P1 fix)
            subjects = data.get('subjects_en', [])
            if subjects:
                # Delete old subjects for this paper (for REPLACE case)
                cursor.execute("DELETE FROM paper_subjects WHERE paper_id = ?", (data.get('id'),))

                # Insert new subjects (deduplicate to avoid UNIQUE constraint violations)
                for subject in set(subjects):
                    cursor.execute("""
                    INSERT INTO paper_subjects (paper_id, subject)
                    VALUES (?, ?)
                    """, (data.get('id'), subject))

        # Verify count matches (Codex P2 fix)
        actual_count = cursor.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        if actual_count != expected_count:
            raise ValueError(f"Count mismatch: expected {expected_count}, got {actual_count}")

        conn.commit()
        print(f"✅ Imported {actual_count} papers (verified)")

        # Show subject distribution
        subject_count = cursor.execute("SELECT COUNT(DISTINCT subject) FROM paper_subjects").fetchone()[0]
        print(f"✅ Indexed {subject_count} unique subjects")

    except Exception as e:
        conn.rollback()
        print(f"❌ Import failed: {e}")
        raise
    finally:
        conn.close()


def main():
    """Main entry point"""
    # Ensure data directory exists
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    db_path = data_dir / "papers.db"

    # Warn if database already exists
    if db_path.exists():
        print(f"⚠️  Database already exists at {db_path}")
        response = input("Delete and recreate? (y/N): ")
        if response.lower() != 'y':
            print("Aborted.")
            sys.exit(0)
        db_path.unlink()

    # Create schema
    create_schema(str(db_path))

    # Import translations
    import_translations(str(db_path))

    print(f"\n✅ Database created successfully: {db_path}")
    print(f"   Size: {db_path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
