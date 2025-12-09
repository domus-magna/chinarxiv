"""
Filter helpers for ChinaRxiv English web server.

This module provides functions for working with the category taxonomy
and building filter-related data structures.

Performance:
- PostgreSQL: Uses materialized view for category counts (single query, 10-20ms)
- SQLite: Uses N+1 query pattern (acceptable for development, 50-100ms)
"""

import json
from pathlib import Path


def load_category_taxonomy():
    """
    Load the category taxonomy from JSON file.

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
        category_id: Category identifier (e.g., 'ai_computing')

    Returns:
        list: List of subject strings for this category.
              Returns empty list if category not found.

    Example:
        >>> get_category_subjects('ai_computing')
        ['Computer Science', 'Computer Science & Technology', ...]
    """
    taxonomy = load_category_taxonomy()
    category = taxonomy.get(category_id, {})
    return category.get('children', [])


def build_categories(db_connection=None):
    """
    Build category data structure with metadata.

    This function loads the taxonomy and optionally adds paper counts
    per category if a database connection is provided.

    Performance optimization:
    - PostgreSQL: Queries materialized view (single query, 15-25x faster)
    - SQLite: Uses N+1 query pattern (acceptable for development)

    Args:
        db_connection: Optional database connection for counting papers

    Returns:
        dict: Category data with structure:
            {
                "category_id": {
                    "label": "Display Name",
                    "order": 1,
                    "count": 42,  # Only if db_connection provided
                    "subjects": ["Subject 1", "Subject 2", ...]
                },
                ...
            }
    """
    taxonomy = load_category_taxonomy()
    categories = {}

    for category_id, category_data in taxonomy.items():
        categories[category_id] = {
            'label': category_data['label'],
            'order': category_data['order'],
            'subjects': category_data.get('children', [])
        }

    # Optionally add paper counts if database connection provided
    if db_connection:
        # Import adapter here to avoid circular imports
        from .db_adapter import get_adapter

        adapter = get_adapter()

        if adapter.is_postgres:
            # PostgreSQL: Use materialized view (single query, very fast)
            _build_categories_postgres(db_connection, adapter, categories, taxonomy)
        else:
            # SQLite: Use N+1 query pattern (acceptable for development)
            _build_categories_sqlite(db_connection, categories, taxonomy)

    return categories


def _build_categories_postgres(db_connection, adapter, categories, taxonomy):
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
        # Single query to get all subject counts from materialized view
        cursor = adapter.get_cursor(db_connection)
        cursor.execute("SELECT subject, paper_count FROM category_counts;")
        subject_counts = {row['subject']: row['paper_count'] for row in cursor.fetchall()}

        # Aggregate counts per category
        for category_id, category_data in taxonomy.items():
            subjects = category_data.get('children', [])
            total_count = sum(subject_counts.get(subject, 0) for subject in subjects)
            categories[category_id]['count'] = total_count

    except Exception:
        # If materialized view query fails, fall back to zero counts
        for category_id in taxonomy:
            categories[category_id]['count'] = 0


def _build_categories_sqlite(db_connection, categories, taxonomy):
    """
    Build category counts using SQLite N+1 query pattern.

    This is acceptable for development but would be slow in production
    with many categories. Uses parameterized queries for each category.

    Args:
        db_connection: SQLite database connection
        categories: Category dict to populate with counts
        taxonomy: Category taxonomy data
    """
    for category_id, category_data in taxonomy.items():
        subjects = category_data.get('children', [])
        if subjects:
            placeholders = ','.join(['?'] * len(subjects))
            count_query = f"""
                SELECT COUNT(DISTINCT paper_id)
                FROM paper_subjects
                WHERE subject IN ({placeholders})
            """
            try:
                count = db_connection.execute(count_query, subjects).fetchone()[0]
                categories[category_id]['count'] = count
            except Exception:
                # If query fails, skip count
                categories[category_id]['count'] = 0
        else:
            categories[category_id]['count'] = 0
