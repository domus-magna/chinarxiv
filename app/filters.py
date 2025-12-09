"""
Filter helpers for ChinaRxiv English web server.

This module provides functions for working with the category taxonomy
and building filter-related data structures.
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

    Args:
        db_connection: Optional SQLite connection for counting papers

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

    return categories
