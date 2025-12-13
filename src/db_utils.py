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
import re
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from .utils import log

# Cached schema feature flags (set on first use)
_papers_has_license_column: Optional[bool] = None


def _strip_nul(value: Any) -> Any:
    """
    PostgreSQL rejects NUL (0x00) characters in text fields.

    We strip them proactively so a single bad character doesn't drop an entire
    (potentially expensive) translation on the floor.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


_PARA_TAG_RE = re.compile(r"</?\s*para\b[^>]*>", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# Titles should be short. If we store a whole-paper blob here, it breaks UI and search.
_MAX_TITLE_LEN = 300


def _strip_para_tags(text: Any) -> Any:
    """
    Strip XML-ish <PARA ...> wrappers that sometimes leak into translations.

    We only apply this to a small set of DB fields (e.g., title/abstract) where
    these tags are always accidental noise and cause UI artifacts.
    """
    if not isinstance(text, str):
        return text
    cleaned = _PARA_TAG_RE.sub("", text)
    return cleaned.strip()


def _is_likely_english_title(text: str) -> bool:
    """
    Heuristic: treat ASCII-ish titles as safe English fallbacks.

    Some ChinaXiv records already have English titles in `title_cn`.
    If the translated `title_en` is clearly corrupted (e.g., pasted body),
    using the short ASCII `title_cn` is better than showing garbage.
    """
    if not text:
        return False
    if len(text) > _MAX_TITLE_LEN:
        return False
    if _CJK_RE.search(text):
        return False
    # Require at least a couple letters so we don't accept pure punctuation/IDs.
    return sum(ch.isalpha() for ch in text) >= 3


def _normalize_title_for_db(
    conn: psycopg2.extensions.connection, paper_id: str, title_en_raw: Any
) -> str:
    """
    Normalize a title for DB storage.

    Strategy (simplicity-first):
    1) Strip <PARA ...> wrappers and whitespace noise.
    2) If title is too long, try falling back to existing `title_cn` if it looks
       like a reasonable short English title.
    3) Otherwise clamp to a max length so the UI can't be broken by a bad title.
    """
    title = _strip_para_tags(title_en_raw) or ""
    title = " ".join(title.split())

    if len(title) <= _MAX_TITLE_LEN:
        return title

    try:
        cur = conn.cursor()
        cur.execute("SELECT title_cn FROM papers WHERE id = %s", (paper_id,))
        row = cur.fetchone()
        fallback = ""
        if row:
            if isinstance(row, dict):
                fallback = (row.get("title_cn") or "").strip()
            else:
                fallback = (row[0] or "").strip()
        if isinstance(fallback, str):
            fallback = " ".join(fallback.split())
        if isinstance(fallback, str) and _is_likely_english_title(fallback):
            return fallback
    except Exception:
        # If fallback lookup fails, we still clamp the title.
        pass

    # Clamp with a plain ellipsis marker.
    return title[: _MAX_TITLE_LEN - 3].rstrip() + "..."


def _strip_nul_in_list(values: Any) -> Any:
    """
    Strip NULs from string elements in a list.

    This is primarily used for JSON-serialized fields like creators/subjects,
    where a single NUL in one element would otherwise break the DB write.
    """
    if not isinstance(values, list):
        return values
    return [_strip_nul(v) if isinstance(v, str) else v for v in values]


def _strip_para_tags_in_list(values: Any) -> Any:
    """
    Strip accidental <PARA ...> wrappers from each string element in a list.

    This protects creators/subjects fields from model output glitches that can
    break UI rendering and search.
    """
    if not isinstance(values, list):
        return values
    cleaned: list[Any] = []
    for item in values:
        if isinstance(item, str):
            cleaned.append(_strip_para_tags(item))
        else:
            cleaned.append(item)
    return cleaned


def _normalize_qa_status_for_db(raw_status: Any) -> str:
    """
    The database schema currently constrains qa_status to: pass, pending, fail.

    The QA filter can emit more granular statuses (e.g., flag_chinese). We map
    those into the DB's allowed set.
    """
    status = str(raw_status or "pass").strip().lower()
    if status in {"pass", "pending", "fail"}:
        return status
    # Any flagged/unknown QA state is stored as pending (needs review).
    return "pending"


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

        global _papers_has_license_column
        if _papers_has_license_column is None:
            cursor.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'papers' AND column_name = 'license'
            """)
            _papers_has_license_column = cursor.fetchone() is not None

        if _papers_has_license_column:
            cursor.execute("""
                SELECT
                    id,
                    title_cn, abstract_cn, creators_cn, subjects_cn,
                    title_en, abstract_en, creators_en,
                    text_status,
                    date, pdf_url, source_url,
                    license
                FROM papers
                WHERE id = %s
            """, (paper_id,))
        else:
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
        if row.get("license"):
            record["license"] = row.get("license")

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
        creators_en = _strip_nul_in_list(creators_en)
        creators_en = _strip_para_tags_in_list(creators_en)

        title_en = _normalize_title_for_db(
            conn, paper_id, _strip_nul(translation.get("title_en", "") or "")
        )
        abstract_en = _strip_para_tags(_strip_nul(translation.get("abstract_en", "") or ""))
        body_md = _strip_nul(translation.get("body_md", "") or "")
        has_full_text = bool(body_md and len(body_md) > 100)
        qa_status = _normalize_qa_status_for_db(translation.get("_qa_status", "pass"))

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
            title_en,
            abstract_en,
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
            # Sanitize, normalize to Title Case, and de-duplicate.
            # Note: title() capitalizes articles like "and", "of" - this matches
            # PostgreSQL INITCAP() which was used to normalize existing subjects.
            seen: set[str] = set()
            deduped: list[str] = []
            for subject in subjects_en:
                if not subject:
                    continue
                if not isinstance(subject, str):
                    continue
                subject = _strip_nul(subject)
                if not subject:
                    continue
                # Normalize to Title Case for consistency
                subject = subject.strip().title()
                if not subject:
                    continue
                if subject in seen:
                    continue
                seen.add(subject)
                deduped.append(subject)
            subjects_en = deduped

            # Replace existing subjects with translated ones
            cursor.execute(
                "DELETE FROM paper_subjects WHERE paper_id = %s",
                (paper_id,)
            )
            for subject in subjects_en:
                cursor.execute(
                    "INSERT INTO paper_subjects (paper_id, subject) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (paper_id, subject),
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


def refresh_category_counts(conn=None) -> bool:
    """
    Refresh the category_counts materialized view.

    This view pre-computes paper counts per subject for fast category
    filtering. Should be called after papers are translated/imported.

    Args:
        conn: Optional database connection

    Returns:
        True if refresh succeeded, False otherwise
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("REFRESH MATERIALIZED VIEW category_counts;")
        conn.commit()
        log("Refreshed category_counts materialized view")
        return True

    except Exception as e:
        log(f"Error refreshing category_counts: {e}")
        return False

    finally:
        if close_conn:
            conn.close()
