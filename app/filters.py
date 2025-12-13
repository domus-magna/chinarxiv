"""
Filter helpers for ChinaRxiv English web server.

This module provides functions for working with the category taxonomy
and building filter-related data structures.

Performance:
- PostgreSQL: Uses materialized view for category counts (single query, 10-20ms)
- Taxonomy loading is cached in memory after the first call
- Category counts are cached for 5 minutes to reduce DB queries
"""

import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

# Time-based cache for category counts (5 minutes)
_category_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 300,  # 5 minutes
}
_category_cache_lock = Lock()

# Time-based cache for available filters (5 minutes)
_filters_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 300,  # 5 minutes
}
_filters_cache_lock = Lock()


def clear_category_caches():
    """
    Clear all time-based caches. Useful for testing and after
    data imports that might change category counts.
    """
    global _category_cache, _filters_cache
    with _category_cache_lock:
        _category_cache['data'] = None
        _category_cache['timestamp'] = 0
    with _filters_cache_lock:
        _filters_cache['data'] = None
        _filters_cache['timestamp'] = 0


@lru_cache(maxsize=None)
def load_category_taxonomy():
    """
    Load and cache the category taxonomy from JSON file.

    The taxonomy is cached in memory after the first call, preventing
    repeated file I/O operations.

    Returns:
        dict: Category taxonomy with structure:
            {
                "category_id": {
                    "label": "Display Name",
                    "order": 1,
                    "children": ["Subject 1", "Subject 2", ...]
                },
                ...
            }
    """
    taxonomy_path = Path(__file__).parent.parent / 'src' / 'category_taxonomy.json'
    with open(taxonomy_path) as f:
        return json.load(f)


def get_category_subjects(category_id):
    """
    Get the list of subjects for a given category.

    Args:
        category_id: Category identifier (e.g., 'ai_cs')

    Returns:
        list: List of subject strings for this category.
              Returns empty list if category not found.

    Example:
        >>> get_category_subjects('ai_cs')
        ['Computer Science', 'Computer Science & Technology', ...]
    """
    taxonomy = load_category_taxonomy()
    category = taxonomy.get(category_id, {})
    return category.get('children', [])


def build_categories(db_connection=None, max_tabs=5):
    """
    Build category data structure with dynamic ranking.

    Categories are ranked by paper count, with pinned categories (AI/CS)
    always appearing first. Returns only top N categories to fit on screen
    without scrolling.

    Performance optimization:
    - PostgreSQL: Queries materialized view (single query, 15-25x faster)
    - Results cached for 5 minutes to reduce database load

    Args:
        db_connection: Optional database connection for counting papers
        max_tabs: Maximum number of category tabs to show (default 5)

    Returns:
        dict: Category data with structure:
            {
                "category_id": {
                    "label": "Display Name",
                    "order": 1,
                    "count": 42,
                    "pinned": True/False,
                    "subjects": ["Subject 1", "Subject 2", ...]
                },
                ...
            }
    """
    # Check time-based cache first (thread-safe)
    now = time.time()
    with _category_cache_lock:
        if (_category_cache['data'] is not None and
                now - _category_cache['timestamp'] < _category_cache['ttl']):
            return _category_cache['data']

    taxonomy = load_category_taxonomy()

    # Build all categories with their data
    categories = {
        cat_id: {
            'label': data['label'],
            'order': data['order'],
            'pinned': data.get('pinned', False),
            'subjects': data.get('children', []),
            'count': 0  # Default count, updated below if db_connection provided
        }
        for cat_id, data in taxonomy.items()
    }

    # Add paper counts if database connection provided
    if db_connection:
        from .db_adapter import get_adapter
        adapter = get_adapter()
        _build_category_counts(db_connection, adapter, categories, taxonomy)

    # Select top categories: pinned first, then by count
    result = _select_top_categories(categories, max_tabs)

    # Update cache (thread-safe)
    with _category_cache_lock:
        _category_cache['data'] = result
        _category_cache['timestamp'] = now

    return result


def _select_top_categories(categories, max_tabs):
    """
    Select top N categories for display: pinned first, then by paper count.

    Args:
        categories: Dict of all categories with counts
        max_tabs: Maximum number of tabs to show

    Returns:
        dict: Filtered categories (pinned + top by count)
    """
    # Separate pinned and non-pinned categories
    pinned = {k: v for k, v in categories.items() if v.get('pinned')}
    non_pinned = {k: v for k, v in categories.items() if not v.get('pinned')}

    # Sort non-pinned by count (descending), then by order
    sorted_non_pinned = sorted(
        non_pinned.items(),
        key=lambda x: (-x[1]['count'], x[1]['order'])
    )

    # Calculate how many non-pinned to include (guard against negative if many pinned)
    slots_for_non_pinned = max(0, max_tabs - len(pinned))

    # Build result: pinned first (by order), then top non-pinned
    result = {}

    # Add pinned categories first, sorted by order
    for cat_id, data in sorted(pinned.items(), key=lambda x: x[1]['order']):
        result[cat_id] = data

    # Add top non-pinned categories
    for cat_id, data in sorted_non_pinned[:slots_for_non_pinned]:
        # Only include if it has papers
        if data['count'] > 0:
            result[cat_id] = data

    return result


def _build_category_counts(db_connection, adapter, categories, taxonomy):
    """
    Build category counts using PostgreSQL materialized view (optimized).

    This eliminates the N+1 query problem by querying a pre-computed
    materialized view with all subject counts in a single query.

    Args:
        db_connection: PostgreSQL database connection
        adapter: DatabaseAdapter instance
        categories: Category dict to populate with counts
        taxonomy: Category taxonomy data
    """
    try:
        import psycopg2

        # Single query to get all subject counts from materialized view
        cursor = adapter.get_cursor(db_connection)
        cursor.execute("SELECT subject, paper_count FROM category_counts;")
        subject_counts = {row['subject']: row['paper_count'] for row in cursor.fetchall()}

        # Aggregate counts per category
        for category_id, category_data in taxonomy.items():
            subjects = category_data.get('children', [])
            total_count = sum(subject_counts.get(subject, 0) for subject in subjects)
            categories[category_id]['count'] = total_count

    except (psycopg2.Error, Exception) as e:
        # If materialized view query fails, log error and fall back to zero counts
        logger.error(f"Database error fetching category counts: {e}", exc_info=True)
        # Counts remain at their initialized value of 0


def get_available_filters(db_connection):
    """
    Get available subject filters grouped by category for the Advanced Search modal.

    Returns subjects with their paper counts, organized by category in a format
    ready for rendering in the filter UI.

    Performance optimization:
    - Results cached for 5 minutes to reduce database load

    Args:
        db_connection: Database connection for querying subject counts

    Returns:
        dict: Filter data structure:
            {
                "ai_cs": {
                    "label": "AI / CS",
                    "order": 1,
                    "subjects": [
                        {"name": "Computer Science", "count": 456},
                        {"name": "Artificial Intelligence", "count": 789},
                        ...
                    ]
                },
                ...
            }
    """
    # Check time-based cache first (thread-safe)
    now = time.time()
    with _filters_cache_lock:
        if (_filters_cache['data'] is not None and
                now - _filters_cache['timestamp'] < _filters_cache['ttl']):
            return _filters_cache['data']

    taxonomy = load_category_taxonomy()

    # Import adapter here to avoid circular imports
    from .db_adapter import get_adapter
    adapter = get_adapter()

    # Get all subject counts from materialized view (single query)
    try:
        import psycopg2
        cursor = adapter.get_cursor(db_connection)
        cursor.execute("SELECT subject, paper_count FROM category_counts;")
        subject_counts = {row['subject']: row['paper_count'] for row in cursor.fetchall()}
    except (psycopg2.Error, Exception) as e:
        logger.error(f"Database error fetching subject counts: {e}", exc_info=True)
        subject_counts = {}

    # Build filter structure grouped by category
    filters = {}
    for category_id, category_data in taxonomy.items():
        # Get subjects for this category with their counts
        subjects = []
        for subject_name in category_data.get('children', []):
            count = subject_counts.get(subject_name, 0)
            # Only include subjects that have papers
            if count > 0:
                subjects.append({
                    'name': subject_name,
                    'count': count
                })

        # Only include categories that have subjects with papers
        if subjects:
            filters[category_id] = {
                'label': category_data['label'],
                'order': category_data['order'],
                'subjects': subjects
            }

    # Update cache (thread-safe)
    with _filters_cache_lock:
        _filters_cache['data'] = filters
        _filters_cache['timestamp'] = now

    return filters
