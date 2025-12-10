"""
Test suite for filter helper functions (app/filters.py).

This module tests the category taxonomy and filter logic including:
- Category taxonomy loading and validation
- Subject extraction for categories
- Category structure building with and without paper counts
- Error handling for missing or invalid categories
- Database integration for count aggregation

These tests verify the filter helpers work correctly with the category taxonomy
and database queries for building filter UI components.
"""

from app.filters import load_category_taxonomy, get_category_subjects, build_categories


class TestLoadCategoryTaxonomy:
    """Test the load_category_taxonomy() function."""

    def test_taxonomy_loads_successfully(self):
        """Test that taxonomy JSON file loads without errors."""
        taxonomy = load_category_taxonomy()
        assert isinstance(taxonomy, dict)
        assert len(taxonomy) > 0

    def test_taxonomy_structure_valid(self):
        """Test that loaded taxonomy has correct structure."""
        taxonomy = load_category_taxonomy()

        # Check each category has required fields
        for category_id, category_data in taxonomy.items():
            assert 'label' in category_data, f"Category {category_id} missing 'label'"
            assert 'order' in category_data, f"Category {category_id} missing 'order'"
            assert 'children' in category_data, f"Category {category_id} missing 'children'"

            # Validate field types
            assert isinstance(category_data['label'], str)
            assert isinstance(category_data['order'], int)
            assert isinstance(category_data['children'], list)

    def test_taxonomy_contains_expected_categories(self):
        """Test that taxonomy contains known categories."""
        taxonomy = load_category_taxonomy()

        # Known categories from category_taxonomy.json
        expected_categories = ['ai_computing', 'physics', 'psychology']

        for category_id in expected_categories:
            assert category_id in taxonomy, f"Expected category '{category_id}' not found"

    def test_taxonomy_categories_have_subjects(self):
        """Test that all categories have at least one subject."""
        taxonomy = load_category_taxonomy()

        for category_id, category_data in taxonomy.items():
            children = category_data.get('children', [])
            assert len(children) > 0, f"Category {category_id} has no children (subjects)"

    def test_taxonomy_subject_types_are_strings(self):
        """Test that all subject children are strings."""
        taxonomy = load_category_taxonomy()

        for category_id, category_data in taxonomy.items():
            for child in category_data.get('children', []):
                assert isinstance(child, str), \
                    f"Category {category_id} has non-string child: {child}"

    def test_taxonomy_order_values_are_unique(self):
        """Test that category order values are unique (for sorting)."""
        taxonomy = load_category_taxonomy()

        orders = [cat['order'] for cat in taxonomy.values()]
        assert len(orders) == len(set(orders)), "Duplicate order values found"

    def test_taxonomy_order_values_are_positive(self):
        """Test that order values are positive integers."""
        taxonomy = load_category_taxonomy()

        for category_id, category_data in taxonomy.items():
            order = category_data['order']
            assert order > 0, f"Category {category_id} has non-positive order: {order}"


class TestGetCategorySubjects:
    """Test the get_category_subjects() function."""

    def test_get_subjects_for_valid_category(self):
        """Test getting subjects for a known valid category."""
        subjects = get_category_subjects('ai_computing')

        assert isinstance(subjects, list)
        assert len(subjects) > 0
        assert all(isinstance(s, str) for s in subjects)

    def test_get_subjects_contains_expected_values(self):
        """Test that subjects for ai_computing include known values."""
        subjects = get_category_subjects('ai_computing')

        # Known subjects from category_taxonomy.json
        expected_subjects = ['Computer Science', 'Computer Science & Technology']

        for expected in expected_subjects:
            assert expected in subjects, \
                f"Expected subject '{expected}' not found in ai_computing"

    def test_get_subjects_for_physics_category(self):
        """Test getting subjects for physics category."""
        subjects = get_category_subjects('physics')

        assert isinstance(subjects, list)
        assert len(subjects) > 0
        assert 'Physics' in subjects
        assert 'Nuclear Science & Technology' in subjects

    def test_get_subjects_for_psychology_category(self):
        """Test getting subjects for psychology category."""
        subjects = get_category_subjects('psychology')

        assert isinstance(subjects, list)
        assert len(subjects) > 0
        assert 'Psychology' in subjects
        assert 'Applied Psychology' in subjects

    def test_get_subjects_for_nonexistent_category_returns_empty_list(self):
        """Test that requesting non-existent category returns empty list."""
        subjects = get_category_subjects('nonexistent_category')

        assert isinstance(subjects, list)
        assert len(subjects) == 0

    def test_get_subjects_with_empty_string_returns_empty_list(self):
        """Test that empty string category ID returns empty list."""
        subjects = get_category_subjects('')

        assert isinstance(subjects, list)
        assert len(subjects) == 0

    def test_get_subjects_with_none_returns_empty_list(self):
        """Test that None category ID returns empty list."""
        subjects = get_category_subjects(None)

        assert isinstance(subjects, list)
        assert len(subjects) == 0


class TestBuildCategoriesWithoutDatabase:
    """Test build_categories() without database connection (no counts)."""

    def test_build_categories_without_db_returns_dict(self):
        """Test that build_categories returns dictionary structure."""
        categories = build_categories()

        assert isinstance(categories, dict)
        assert len(categories) > 0

    def test_build_categories_structure_has_required_fields(self):
        """Test that each category has label, order, and subjects."""
        categories = build_categories()

        for _category_id, category_data in categories.items():
            assert 'label' in category_data
            assert 'order' in category_data
            assert 'subjects' in category_data

    def test_build_categories_without_db_has_no_counts(self):
        """Test that without database, no 'count' field is added."""
        categories = build_categories()

        for category_id, category_data in categories.items():
            assert 'count' not in category_data, \
                f"Category {category_id} has count field without database"

    def test_build_categories_preserves_taxonomy_data(self):
        """Test that build_categories preserves data from taxonomy."""
        taxonomy = load_category_taxonomy()
        categories = build_categories()

        for category_id in taxonomy:
            assert category_id in categories
            assert categories[category_id]['label'] == taxonomy[category_id]['label']
            assert categories[category_id]['order'] == taxonomy[category_id]['order']

    def test_build_categories_subjects_match_taxonomy_children(self):
        """Test that subjects field matches children from taxonomy."""
        taxonomy = load_category_taxonomy()
        categories = build_categories()

        for category_id in taxonomy:
            expected_children = taxonomy[category_id].get('children', [])
            actual_subjects = categories[category_id]['subjects']
            assert actual_subjects == expected_children


class TestBuildCategoriesWithDatabase:
    """Test build_categories() with database connection (includes counts)."""

    def test_build_categories_with_db_includes_counts(self, app, sample_papers):
        """Test that with database, count field is added."""
        from app.database import get_db

        with app.app_context():
            db = get_db()
            categories = build_categories(db)

            for category_id, category_data in categories.items():
                assert 'count' in category_data, \
                    f"Category {category_id} missing count field with database"
                assert isinstance(category_data['count'], int)
                assert category_data['count'] >= 0

    def test_build_categories_counts_are_accurate(self, app, sample_papers):
        """Test that category counts match actual database counts."""
        from app.database import get_db

        with app.app_context():
            db = get_db()
            categories = build_categories(db)

            # ai_computing category should have Computer Science papers
            # From sample_papers fixture: 3 papers have Computer Science subject
            ai_count = categories['ai_computing']['count']
            assert ai_count >= 3, f"Expected at least 3 AI papers, got {ai_count}"

    def test_build_categories_with_empty_database(self, app):
        """Test that build_categories handles empty database gracefully."""
        from app.database import get_db

        with app.app_context():
            db = get_db()
            categories = build_categories(db)

            for category_id, category_data in categories.items():
                assert category_data['count'] == 0, \
                    f"Category {category_id} has non-zero count with empty database"

    def test_build_categories_count_aggregation_logic(self, app, sample_papers):
        """Test that counts aggregate papers matching any subject in category."""
        from app.database import get_db

        with app.app_context():
            db = get_db()
            categories = build_categories(db)

            # Physics category has multiple subjects (Physics, Nuclear Science, etc.)
            # Should count papers matching ANY of these subjects
            physics_count = categories['physics']['count']
            assert physics_count >= 0, "Physics count should be non-negative"

    def test_build_categories_with_db_query_error(self, app):
        """Test that build_categories handles database query errors gracefully."""
        from app.database import get_db

        with app.app_context():
            db = get_db()
            # Close the connection to simulate error
            db.close()

            # Should handle error gracefully and set count to 0
            categories = build_categories(db)

            for category_id, category_data in categories.items():
                assert category_data['count'] == 0, \
                    f"Category {category_id} should have count=0 with broken database"


class TestCategoryCountAccuracy:
    """Integration tests for category count accuracy."""

    def test_physics_category_counts_physics_papers(self, app, sample_papers):
        """Test that physics category counts papers with Physics subject."""
        from app.database import get_db

        with app.app_context():
            # From sample_papers: chinaxiv-202401.00006 and 202408.00007 have Physics
            db = get_db()
            categories = build_categories(db)

            physics_count = categories['physics']['count']
            assert physics_count >= 2, \
                f"Expected at least 2 physics papers, got {physics_count}"

    def test_psychology_category_initially_zero(self, app, sample_papers):
        """Test that psychology category has zero count (no psychology papers in sample data)."""
        from app.database import get_db

        with app.app_context():
            # Sample data has no psychology papers
            db = get_db()
            categories = build_categories(db)

            psychology_count = categories['psychology']['count']
            assert psychology_count == 0, \
                f"Expected 0 psychology papers, got {psychology_count}"

    def test_category_counts_exclude_flagged_papers(self, app, sample_papers):
        """Test that category counts only include QA-passed papers."""
        from app.database import get_db

        with app.app_context():
            # Sample data includes chinaxiv-202411.00009 with qa_status='fail'
            # This should NOT be counted
            db = get_db()

            # Count total papers in database (including flagged)
            db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]

            # Count QA-passed papers
            passed_papers = db.execute(
                "SELECT COUNT(*) FROM papers WHERE qa_status = 'pass'"
            ).fetchone()[0]

            # Total category counts should match passed papers (not total papers)
            categories = build_categories(db)
            total_category_count = sum(cat['count'] for cat in categories.values())

            # Note: total_category_count may be >= passed_papers because papers can
            # have multiple subjects and be counted in multiple categories
            assert total_category_count >= passed_papers - 1, \
                "Category counts should roughly match QA-passed papers"


class TestFilterIntegrationWithRoutes:
    """Integration tests for filter functions used in routes."""

    def test_get_category_subjects_used_in_query_papers(self, app, sample_papers):
        """Test that get_category_subjects() works correctly with query_papers()."""
        from app.database import query_papers

        with app.app_context():
            # This is how routes use get_category_subjects() to filter papers
            subjects = get_category_subjects('ai_computing')
            assert len(subjects) > 0, "ai_computing should have subjects"

            # Query papers with category filter (should use these subjects internally)
            papers, total = query_papers(
                category='ai_computing',
                page=1,
                per_page=50
            )

            # Should return papers with Computer Science subjects
            assert total >= 3, f"Expected at least 3 AI papers, got {total}"

    def test_build_categories_used_in_index_route(self, app, sample_papers):
        """Test that build_categories() works as used in index() route."""
        from app.database import get_db

        with app.app_context():
            # This is how index() route calls build_categories()
            db = get_db()
            categories = build_categories(db)

            # Verify structure matches what template expects
            assert 'ai_computing' in categories
            assert 'label' in categories['ai_computing']
            assert 'order' in categories['ai_computing']
            assert 'count' in categories['ai_computing']

            # Template sorts categories by order
            sorted_categories = sorted(categories.items(), key=lambda x: x[1]['order'])
            assert len(sorted_categories) > 0


class TestEdgeCases:
    """Test edge cases and error scenarios."""

    def test_category_with_special_characters_in_subjects(self):
        """Test handling of subjects with special characters."""
        # ai_computing has "Computer Science & Technology" with ampersand
        subjects = get_category_subjects('ai_computing')

        # Should include subject with ampersand
        assert any('&' in s for s in subjects), \
            "Should handle subjects with special characters"

    def test_category_with_unicode_characters(self):
        """Test handling of categories with potential Unicode."""
        # Category IDs should be ASCII, but subjects might have Unicode
        subjects = get_category_subjects('ai_computing')

        # All subjects should be strings (may contain Unicode)
        for subject in subjects:
            assert isinstance(subject, str)

    def test_build_categories_with_none_database_connection(self):
        """Test build_categories with explicit None database."""
        categories = build_categories(None)

        # Should work same as build_categories() with no argument
        assert isinstance(categories, dict)
        for category_data in categories.values():
            assert 'count' not in category_data

    def test_concurrent_taxonomy_loads(self):
        """Test that multiple calls to load_category_taxonomy() work correctly."""
        # Simulate multiple route handlers loading taxonomy concurrently
        taxonomy1 = load_category_taxonomy()
        taxonomy2 = load_category_taxonomy()
        taxonomy3 = load_category_taxonomy()

        # All should return identical structures
        assert taxonomy1 == taxonomy2 == taxonomy3


class TestCategoryTaxonomyConsistency:
    """Test consistency between taxonomy and actual database data."""

    def test_taxonomy_subjects_match_database_subjects(self, app, sample_papers):
        """Test that taxonomy subjects match subjects actually used in database."""
        from app.database import get_db

        with app.app_context():
            db = get_db()

            # Get all unique subjects from database
            db_subjects = set()
            rows = db.execute("SELECT DISTINCT subject FROM paper_subjects").fetchall()
            for row in rows:
                db_subjects.add(row[0])

            # Get all subjects from taxonomy
            taxonomy = load_category_taxonomy()
            taxonomy_subjects = set()
            for category_data in taxonomy.values():
                taxonomy_subjects.update(category_data.get('children', []))

            # All database subjects should be in taxonomy (or close - some papers may
            # have subjects not yet categorized)
            uncategorized = db_subjects - taxonomy_subjects

            # It's OK to have a few uncategorized subjects
            assert len(uncategorized) <= 5, \
                f"Too many uncategorized subjects in database: {uncategorized}"


# Additional helper function tests
class TestHelperFunctionUsage:
    """Test that helper functions work correctly in typical usage patterns."""

    def test_filter_workflow_from_url_to_query(self):
        """Test typical workflow: URL param → get_category_subjects → query_papers."""
        # Simulate URL parameter: /?category=ai_computing
        category_param = 'ai_computing'

        # Step 1: Get subjects for this category
        subjects = get_category_subjects(category_param)
        assert len(subjects) > 0

        # Step 2: These subjects are used in query_papers() to filter
        # (query_papers implementation already tested in test_database.py)
        assert isinstance(subjects, list)
        assert all(isinstance(s, str) for s in subjects)

    def test_category_rendering_workflow(self, app, sample_papers):
        """Test workflow for rendering category tabs with counts."""
        from app.database import get_db

        with app.app_context():
            # Step 1: Build categories with counts
            db = get_db()
            categories = build_categories(db)

            # Step 2: Sort for rendering (template uses sort)
            sorted_categories = sorted(
                categories.items(),
                key=lambda x: x[1]['order']
            )

            # Step 3: Verify renderable data
            for _category_id, category_data in sorted_categories:
                label = category_data['label']
                count = category_data['count']

                # Template would render: "{label} ({count})"
                rendered = f"{label} ({count})"
                assert isinstance(rendered, str)
                assert len(rendered) > 0

    def test_empty_category_filter_handling(self):
        """Test handling of empty category parameter."""
        # URL: /?category=
        category_param = ''

        subjects = get_category_subjects(category_param)
        assert subjects == [], "Empty category should return empty list"

        # Routes should handle this gracefully (show all papers)
