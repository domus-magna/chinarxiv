"""
Database query helpers for ChinaRxiv English web server.

This module provides functions for querying the database with
filtering, pagination, and full-text search capabilities.

Uses PostgreSQL with connection pooling via database adapter.
"""

import logging
import psycopg2
from flask import g
from .filters import get_category_subjects
from .db_adapter import get_adapter

logger = logging.getLogger(__name__)


def get_db():
    """
    Get a database connection from the pool for the current request.

    Uses Flask's g object to store the connection, ensuring it's reused
    within the same request. The connection is released automatically by a
    teardown handler in app/__init__.py.

    Returns:
        PostgreSQL database connection with RealDictCursor factory
        (rows are accessed as dicts)
    """
    if 'db' not in g:
        g.db = get_adapter().get_connection()
    return g.db


def query_papers(category=None, date_from=None, date_to=None, search=None,
                 has_figures=None, page=1, per_page=50):
    """
    Query papers with filters and pagination.

    This function implements all filter types with proper security,
    performance optimizations, and error handling as specified in
    the Codex-reviewed plan.

    Args:
        category: Category ID (e.g., 'ai_computing') or None
        date_from: ISO date string (e.g., '2022-01-01') or None
        date_to: ISO date string (e.g., '2022-12-31') or None
        search: Search query string or None
        has_figures: Boolean filter for papers with figures or None
        page: Page number (1-indexed), max 1000
        per_page: Papers per page, max 100

    Returns:
        tuple: (papers, total_count)
            papers: List of paper dicts
            total_count: Total number of matching papers (for pagination)

    Security:
        - All inputs are validated and sanitized
        - Uses parameterized queries to prevent SQL injection
        - FTS MATCH errors handled gracefully with fallback

    Performance:
        - Uses JOIN on normalized paper_subjects table (not LIKE)
        - Uses composite indexes for common queries
        - Uses DISTINCT only when needed (with JOIN)
    """
    db = get_db()
    adapter = get_adapter()

    # FIX: Input validation (Codex P2 issue - prevent pagination abuse)
    page = max(1, min(page, 1000))  # Limit to 1000 pages
    per_page = max(1, min(per_page, 100))  # Max 100 papers per page

    # Build WHERE clause
    where_clauses = ["p.qa_status = 'pass'"]
    params = []
    needs_join = False

    if category:
        # FIX: Use JOIN on normalized table instead of LIKE (Codex P1 issue)
        subjects = get_category_subjects(category)

        # FIX: Guard against empty category list (Codex P2 issue)
        if not subjects:
            return [], 0

        needs_join = True
        placeholders = ','.join(['%s'] * len(subjects))
        where_clauses.append(f"ps.subject IN ({placeholders})")
        params.extend(subjects)

    if date_from:
        where_clauses.append("p.date >= %s")
        params.append(date_from)

    if date_to:
        where_clauses.append("p.date <= %s")
        params.append(date_to)

    if has_figures is not None:
        # Use boolean for PostgreSQL (TRUE/FALSE not 1/0)
        where_clauses.append("p.has_figures = %s")
        params.append(bool(has_figures))

    if search:
        # FIX: Validate search query (Codex P2 issue)
        search = search.strip()
        if search:  # Only add search if non-empty after strip
            # PostgreSQL native full-text search using tsvector
            where_clauses.append("p.search_vector @@ plainto_tsquery('english', %s)")
            params.append(search)

    where_sql = " AND ".join(where_clauses)

    # Build FROM clause (with JOIN if needed)
    if needs_join:
        from_clause = "papers p INNER JOIN paper_subjects ps ON p.id = ps.paper_id"
        # Need DISTINCT when joining to avoid duplicate papers
        count_sql = f"SELECT COUNT(DISTINCT p.id) FROM {from_clause} WHERE {where_sql}"
        select_distinct = "DISTINCT p.*"
    else:
        from_clause = "papers p"
        count_sql = f"SELECT COUNT(*) FROM {from_clause} WHERE {where_sql}"
        select_distinct = "p.*"

    # Execute queries with error handling
    try:
        cursor = adapter.get_cursor(db)

        # Count total (for pagination)
        cursor.execute(count_sql, params)
        total = cursor.fetchone()[0]

        if total == 0:
            return [], 0

        # Get page of results
        offset = (page - 1) * per_page
        query_sql = f"""
            SELECT {select_distinct}
            FROM {from_clause}
            WHERE {where_sql}
            ORDER BY p.date DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(query_sql, params + [per_page, offset])
        papers = cursor.fetchall()

        # Adapter's cursor factory should already return dict-like rows
        return papers, total

    except psycopg2.Error as e:
        # Query error - log and return empty results
        logger.error(
            f"Database query failed: {e}",
            exc_info=True,
            extra={
                'filters': {
                    'category': category,
                    'date_from': date_from,
                    'date_to': date_to,
                    'search': search,
                    'has_figures': has_figures,
                    'page': page,
                    'per_page': per_page
                }
            }
        )
        return [], 0
