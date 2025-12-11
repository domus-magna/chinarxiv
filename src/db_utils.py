"""
Database utility functions for the translation pipeline.

This module provides database access for translate_paper() and related functions,
making the PostgreSQL database the source of truth instead of local JSON files.

Usage:
    from src.db_utils import get_paper_for_translation, save_translation_result

    # Load paper metadata for translation
    record = get_paper_for_translation('chinaxiv-202401.00001')

    # Save translation results
    save_translation_result('chinaxiv-202401.00001', translation_dict)
"""

import json
import os
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from .utils import log


def get_db_connection():
    """
    Get PostgreSQL connection from DATABASE_URL environment variable.

    Returns:
        psycopg2 connection object

    Raises:
        RuntimeError: If DATABASE_URL is not set
    """
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def get_paper_for_translation(paper_id: str, conn=None) -> Optional[Dict[str, Any]]:
    """
    Load paper record from database for translation.

    Returns a dict compatible with TranslationService:
    {
        "id": "chinaxiv-202401.00001",
        "title": "Chinese title",
        "abstract": "Chinese abstract",
        "creators": ["Author1", "Author2"],
        "subjects": ["Subject1", "Subject2"],
        "pdf_url": "https://...",
        "source_url": "https://..."
    }

    Priority for Chinese content:
    1. Use _cn columns if populated (preferred)
    2. Fall back to _en columns if text_status='pending' (contains Chinese)
    3. Return None if paper not found

    Args:
        paper_id: Paper identifier (e.g., 'chinaxiv-202401.00001')
        conn: Optional database connection (creates new one if not provided)

    Returns:
        Dict with paper metadata for translation, or None if not found/not translatable
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                id,
                title_cn, abstract_cn, creators_cn, subjects_cn,
                title_en, abstract_en, creators_en,
                text_status,
                date, pdf_url, source_url
            FROM papers
            WHERE id = %s
        """, (paper_id,))

        row = cursor.fetchone()
        if not row:
            log(f"Paper {paper_id} not found in database")
            return None

        # Determine source of Chinese content
        # Priority: _cn columns > _en columns (if pending)
        title = row.get('title_cn')
        abstract = row.get('abstract_cn')
        creators = row.get('creators_cn')
        subjects = row.get('subjects_cn')

        # Fall back to _en columns if _cn not populated and text_status is 'pending'
        # (During discovery, Chinese gets stored in _en columns temporarily)
        if not title and row.get('text_status') == 'pending':
            title = row.get('title_en')
            abstract = row.get('abstract_en')
            creators = row.get('creators_en')
            # Subjects from paper_subjects table for fallback
            cursor.execute("""
                SELECT subject FROM paper_subjects WHERE paper_id = %s
            """, (paper_id,))
            subjects = [r['subject'] for r in cursor.fetchall()]

        if not title:
            log(f"Paper {paper_id} has no Chinese metadata for translation")
            return None

        # Ensure creators is a list
        if isinstance(creators, str):
            try:
                creators = json.loads(creators)
            except json.JSONDecodeError:
                creators = []
        elif not creators:
            creators = []

        # Ensure subjects is a list
        if isinstance(subjects, str):
            try:
                subjects = json.loads(subjects)
            except json.JSONDecodeError:
                subjects = []
        elif not subjects:
            subjects = []

        # Build record in format expected by TranslationService
        record = {
            "id": row['id'],
            "title": title,
            "abstract": abstract or "",
            "creators": creators,
            "subjects": subjects,
            "date": row['date'].isoformat() if row.get('date') else None,
            "pdf_url": row.get('pdf_url'),
            "source_url": row.get('source_url'),
        }

        return record

    finally:
        if close_conn:
            conn.close()


def save_translation_result(
    paper_id: str,
    translation: Dict[str, Any],
    conn=None
) -> bool:
    """
    Save translation result directly to database.

    Updates the _en columns with translated content and marks the paper
    as translated. Does NOT modify _cn columns (preserves originals).

    Args:
        paper_id: Paper identifier
        translation: Dict with translation results from TranslationService:
            - title_en: Translated title
            - abstract_en: Translated abstract
            - creators_en: Translated author names (list)
            - body_md: Translated body in markdown
            - subjects_en: Translated subjects (list, optional)
            - _qa_status: QA result ('pass', 'pending', 'fail')
        conn: Optional database connection

    Returns:
        True if saved successfully, False otherwise
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    try:
        cursor = conn.cursor()

        # Prepare creators as JSONB
        creators_en = translation.get('creators_en', [])
        if isinstance(creators_en, str):
            try:
                creators_en = json.loads(creators_en)
            except json.JSONDecodeError:
                creators_en = []

        body_md = translation.get('body_md', '')
        has_full_text = bool(body_md and len(body_md) > 100)
        qa_status = translation.get('_qa_status', 'pass')

        # Update English columns and mark as complete
        cursor.execute("""
            UPDATE papers SET
                title_en = %s,
                abstract_en = %s,
                creators_en = %s,
                body_md = %s,
                has_full_text = %s,
                qa_status = %s,
                text_status = 'complete',
                text_completed_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            translation.get('title_en', ''),
            translation.get('abstract_en', ''),
            json.dumps(creators_en),
            body_md,
            has_full_text,
            qa_status,
            paper_id
        ))

        if cursor.rowcount == 0:
            log(f"Warning: No paper found with id {paper_id}")
            return False

        # Update paper_subjects table with translated subjects
        subjects_en = translation.get('subjects_en', [])
        if isinstance(subjects_en, str):
            try:
                subjects_en = json.loads(subjects_en)
            except json.JSONDecodeError:
                subjects_en = []

        if subjects_en:
            # Replace existing subjects with translated ones
            cursor.execute(
                "DELETE FROM paper_subjects WHERE paper_id = %s",
                (paper_id,)
            )
            for subject in subjects_en:
                if subject:  # Skip empty subjects
                    cursor.execute(
                        "INSERT INTO paper_subjects (paper_id, subject) VALUES (%s, %s)",
                        (paper_id, subject)
                    )

        conn.commit()
        log(f"Saved translation for {paper_id} to database")
        return True

    except Exception as e:
        conn.rollback()
        log(f"Error saving translation for {paper_id}: {e}")
        raise
    finally:
        if close_conn:
            conn.close()


def get_papers_needing_translation(
    limit: int = 100,
    conn=None
) -> List[str]:
    """
    Get list of paper IDs that need translation.

    Returns papers where:
    - text_status is 'pending' or 'failed'
    - Has Chinese metadata available (_cn columns or _en fallback)

    Args:
        limit: Maximum number of papers to return
        conn: Optional database connection

    Returns:
        List of paper IDs
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM papers
            WHERE text_status IN ('pending', 'failed')
              AND (title_cn IS NOT NULL OR title_en IS NOT NULL)
            ORDER BY date DESC NULLS LAST
            LIMIT %s
        """, (limit,))

        return [row['id'] for row in cursor.fetchall()]

    finally:
        if close_conn:
            conn.close()


def update_chinese_metadata(
    paper_id: str,
    title_cn: str,
    abstract_cn: str,
    creators_cn: List[str],
    subjects_cn: List[str],
    conn=None
) -> bool:
    """
    Update Chinese metadata columns for a paper.

    Used by backfill scripts to populate _cn columns from B2 records.

    Args:
        paper_id: Paper identifier
        title_cn: Chinese title
        abstract_cn: Chinese abstract
        creators_cn: List of Chinese author names
        subjects_cn: List of Chinese subjects
        conn: Optional database connection

    Returns:
        True if updated successfully
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE papers SET
                title_cn = %s,
                abstract_cn = %s,
                creators_cn = %s,
                subjects_cn = %s
            WHERE id = %s
        """, (
            title_cn,
            abstract_cn,
            json.dumps(creators_cn) if creators_cn else None,
            json.dumps(subjects_cn) if subjects_cn else None,
            paper_id
        ))

        conn.commit()
        return cursor.rowcount > 0

    except Exception as e:
        conn.rollback()
        log(f"Error updating Chinese metadata for {paper_id}: {e}")
        raise
    finally:
        if close_conn:
            conn.close()
