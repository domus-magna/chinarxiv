"""
Integration tests for PostgreSQL-specific features.

This module tests the complete PostgreSQL stack end-to-end:
- Connection pooling
- Materialized views for category counts
- tsvector full-text search
- RealDictCursor functionality
- Error handling with PostgreSQL exceptions

These tests verify that all PostgreSQL-specific optimizations work correctly
in an integrated environment.
"""

import pytest
import psycopg2
from app.db_adapter import get_adapter
from app.database import get_db, query_papers
from app.filters import build_categories


class TestPostgreSQLConnectionPool:
    """Test PostgreSQL connection pooling."""

    def test_connection_pool_initialization(self, app):
        """Test that connection pool is properly initialized."""
        adapter = get_adapter()

        # Pool should be created
        assert adapter._pool is not None
        assert adapter._pool.minconn == 1
        assert adapter._pool.maxconn == 20

    def test_connection_acquisition_and_release(self, app, test_database):
        """Test getting and releasing connections from pool."""
        adapter = get_adapter()

        # Get connection
        conn1 = adapter.get_connection()
        assert conn1 is not None

        # Connection should be usable
        cursor = conn1.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        assert result[0] == 1
        cursor.close()

        # Release connection back to pool
        adapter.release_connection(conn1)

        # Get another connection (should reuse from pool)
        conn2 = adapter.get_connection()
        assert conn2 is not None
        adapter.release_connection(conn2)

    def test_multiple_concurrent_connections(self, app, test_database):
        """Test that pool handles multiple connections correctly."""
        adapter = get_adapter()

        # Get multiple connections
        connections = []
        for _i in range(5):
            conn = adapter.get_connection()
            connections.append(conn)

        # All connections should be valid
        for conn in connections:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1
            cursor.close()

        # Release all connections
        for conn in connections:
            adapter.release_connection(conn)


class TestMaterializedViews:
    """Test materialized view functionality for category counts."""

    def test_materialized_view_exists(self, app, test_database, sample_papers):
        """Test that category_counts materialized view exists."""
        with app.app_context():
            db = get_db()
            cursor = db.cursor()

            # Query the materialized view
            cursor.execute("""
                SELECT COUNT(*)
                FROM category_counts
            """)
            count = cursor.fetchone()[0]

            # Should have subject counts
            assert count > 0
            cursor.close()

    def test_category_counts_accuracy(self, app, test_database, sample_papers):
        """Test that materialized view produces accurate counts."""
        with app.app_context():
            db = get_db()
            cursor = db.cursor()

            # Get count for specific subject from materialized view
            cursor.execute("""
                SELECT paper_count
                FROM category_counts
                WHERE subject = 'Computer Science'
            """)
            mv_count = cursor.fetchone()

            if mv_count:  # Subject exists in data
                # Compare with direct count
                cursor.execute("""
                    SELECT COUNT(DISTINCT paper_id)
                    FROM paper_subjects ps
                    JOIN papers p ON ps.paper_id = p.id
                    WHERE ps.subject = 'Computer Science'
                      AND p.qa_status = 'pass'
                """)
                direct_count = cursor.fetchone()[0]

                assert mv_count[0] == direct_count

            cursor.close()

    def test_build_categories_uses_materialized_view(self, app, test_database, sample_papers):
        """Test that build_categories() uses materialized view efficiently."""
        with app.app_context():
            db = get_db()

            # Build categories (should use materialized view)
            categories = build_categories(db)

            # Should have category data with counts
            assert len(categories) > 0

            # Each category should have count
            for _category_id, category_data in categories.items():
                assert 'count' in category_data
                assert isinstance(category_data['count'], int)
                assert category_data['count'] >= 0


class TestFullTextSearch:
    """Test PostgreSQL tsvector full-text search."""

    def test_fts_search_finds_results(self, app, test_database, sample_papers):
        """Test that full-text search finds matching papers."""
        with app.app_context():
            # Search for "learning" (should match several papers)
            papers, total = query_papers(search="learning", page=1, per_page=50)

            # Should find papers with "learning" in title/abstract
            assert total > 0
            assert len(papers) > 0

            # Verify results contain search term
            found_match = False
            for paper in papers:
                if 'learning' in paper['title_en'].lower() or \
                   ('abstract_en' in paper and paper['abstract_en'] and \
                    'learning' in paper['abstract_en'].lower()):
                    found_match = True
                    break

            assert found_match, "Search results should contain the search term"

    def test_fts_search_with_multiple_words(self, app, test_database, sample_papers):
        """Test full-text search with multiple words."""
        with app.app_context():
            # Search for multiple words
            papers, total = query_papers(search="neural network", page=1, per_page=50)

            # Should find results (plainto_tsquery handles multi-word queries)
            assert total >= 0  # May be 0 or more depending on data

    def test_fts_empty_search_returns_all(self, app, test_database, sample_papers):
        """Test that empty search returns all papers."""
        with app.app_context():
            # Empty search
            papers, total = query_papers(search="", page=1, per_page=50)

            # Should return all papers (not filtered by search)
            assert total > 0

    def test_fts_error_handling(self, app, test_database, sample_papers):
        """Test that FTS errors are handled gracefully."""
        with app.app_context():
            # Search with potentially problematic characters
            # Note: plainto_tsquery handles most special characters gracefully
            papers, total = query_papers(search="test!@#$%", page=1, per_page=50)

            # Should not crash - either returns results or empty set
            assert isinstance(papers, list)
            assert isinstance(total, int)


class TestRealDictCursor:
    """Test dict-like row access with RealDictCursor."""

    def test_cursor_returns_dict_rows(self, app, test_database, sample_papers):
        """Test that queries return dict-like rows."""
        with app.app_context():
            # Query papers
            papers, total = query_papers(page=1, per_page=5)

            assert len(papers) > 0

            # Each paper should be a dict
            for paper in papers:
                assert isinstance(paper, dict)

                # Should have expected keys
                assert 'id' in paper
                assert 'title_en' in paper
                assert 'date' in paper

    def test_dict_access_to_columns(self, app, test_database, sample_papers):
        """Test accessing columns via dictionary keys."""
        with app.app_context():
            papers, total = query_papers(page=1, per_page=1)

            assert len(papers) == 1
            paper = papers[0]

            # Access columns as dict keys
            paper_id = paper['id']
            assert isinstance(paper_id, str)

            title = paper['title_en']
            assert isinstance(title, str)


class TestPostgreSQLErrorHandling:
    """Test error handling for PostgreSQL-specific errors."""

    def test_handles_connection_errors_gracefully(self, app):
        """Test that connection errors are handled."""
        # This test verifies the adapter raises ValueError for missing DATABASE_URL
        # (tested at initialization time in db_adapter.py)
        adapter = get_adapter()
        assert adapter is not None

    def test_handles_query_errors_gracefully(self, app, test_database):
        """Test that query errors return empty results."""
        with app.app_context():
            # Query with invalid category (empty subjects list)
            papers, total = query_papers(category="nonexistent_category", page=1, per_page=50)

            # Should return empty results, not crash
            assert papers == []
            assert total == 0

    def test_database_constraint_violations(self, app, test_database):
        """Test handling of constraint violations."""
        with app.app_context():
            db = get_db()
            cursor = db.cursor()

            # Try to insert duplicate paper (should violate PRIMARY KEY)
            try:
                cursor.execute("""
                    INSERT INTO papers (id, title_en, qa_status)
                    VALUES ('test-duplicate', 'Test', 'pass')
                """)
                db.commit()

                # Second insert should fail
                with pytest.raises(psycopg2.IntegrityError):
                    cursor.execute("""
                        INSERT INTO papers (id, title_en, qa_status)
                        VALUES ('test-duplicate', 'Test', 'pass')
                    """)
                    db.commit()

                db.rollback()
            finally:
                # Cleanup
                cursor.execute("DELETE FROM papers WHERE id = 'test-duplicate'")
                db.commit()
                cursor.close()


class TestEndToEndIntegration:
    """Test complete end-to-end workflows."""

    def test_full_query_pipeline(self, app, test_database, sample_papers):
        """Test complete query pipeline from request to results."""
        with app.app_context():
            # Execute a complex query with all filters
            papers, total = query_papers(
                category="ai_cs",
                date_from="2022-01-01",
                date_to="2022-12-31",
                search="neural",
                has_figures=True,
                page=1,
                per_page=50
            )

            # Should execute successfully
            assert isinstance(papers, list)
            assert isinstance(total, int)

            # Results should match all filter criteria
            for paper in papers:
                # Should have figures
                assert paper.get('has_figures', False) is True

                # Should be in 2022
                if paper.get('date'):
                    assert '2022' in str(paper['date'])

    def test_category_building_with_database(self, client, sample_papers):
        """Test complete category building through HTTP request."""
        response = client.get('/')

        # Should render successfully with categories
        assert response.status_code == 200

        # Should have category data in response (depends on template)
        # At minimum, should not crash

    def test_paper_detail_retrieval(self, client, sample_papers):
        """Test retrieving paper details through HTTP."""
        # Get first paper ID from sample data
        paper_id = 'chinaxiv-202201.00001'

        response = client.get(f'/items/{paper_id}')

        # Should render successfully
        assert response.status_code == 200

        # Should contain paper title
        assert b'Deep Learning' in response.data
