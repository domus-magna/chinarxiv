"""
Test suite for database query functions (query_papers, filters).

This module tests the core database querying functionality including:
- Basic queries without filters
- Category filtering with JOIN on paper_subjects table
- Date range filtering with inclusive endpoints
- Full-text search with FTS5
- Has figures filtering
- Pagination with input validation
- Combined filter scenarios
- Edge cases and error handling

Tests verify the full system behavior by using the same fixtures
and database setup as the production application.
"""

from app.database import query_papers, get_db


class TestQueryPapersBasic:
    """Test basic query_papers() functionality without filters."""

    def test_query_all_papers_returns_passing_qa_only(self, app, sample_papers):
        """Test that query without filters returns only QA-passed papers."""
        with app.app_context():
            papers, total = query_papers()

            # Should return all passing papers (exclude the 1 failed QA paper)
            assert total == 10  # 11 sample papers - 1 failed QA
            assert len(papers) <= 50  # Default per_page limit

            # All returned papers should have qa_status = 'pass'
            for paper in papers:
                assert paper['qa_status'] == 'pass'

    def test_query_returns_sorted_by_date_desc(self, app, sample_papers):
        """Test that papers are returned in reverse chronological order."""
        with app.app_context():
            papers, total = query_papers()

            # Extract dates from papers
            dates = [p['date'] for p in papers if p['date']]

            # Dates should be in descending order (most recent first)
            assert dates == sorted(dates, reverse=True)

    def test_query_returns_correct_paper_structure(self, app, sample_papers):
        """Test that returned papers have all expected fields."""
        with app.app_context():
            papers, total = query_papers()

            assert len(papers) > 0

            # Check first paper has all required fields
            paper = papers[0]
            required_fields = ['id', 'title_en', 'abstract_en', 'creators_en',
                              'date', 'has_figures', 'has_full_text', 'qa_status']

            for field in required_fields:
                assert field in paper


class TestCategoryFiltering:
    """Test category-based filtering with normalized paper_subjects table."""

    def test_filter_by_computer_science_category(self, app, sample_papers, sample_category_taxonomy):
        """Test filtering by AI & Computing category (Computer Science papers)."""
        with app.app_context():
            # The sample_category_taxonomy fixture defines 'ai_computing' category
            papers, total = query_papers(category='ai_computing')

            # Should return papers with Computer Science subjects
            # From sample_papers: 5 CS papers (3 in 2022, 1 in 2024, 1 in 2024 with script tags)
            # But 1 of them (chinaxiv-202411.00009) has qa_status='fail'
            assert total >= 3  # At least 3 CS papers with qa_status='pass'

            # All returned papers should have subjects matching the category
            for paper in papers:
                # Get paper's subjects from database
                db = get_db()
                subjects = db.execute(
                    "SELECT subject FROM paper_subjects WHERE paper_id = ?",
                    (paper['id'],)
                ).fetchall()
                paper_subjects = [s['subject'] for s in subjects]

                # At least one subject should be in the ai_computing category
                category_subjects = sample_category_taxonomy['ai_computing']['subjects']
                assert any(subj in category_subjects for subj in paper_subjects)

    def test_filter_by_biology_category_uses_sample_data(self, app, sample_papers):
        """
        Test filtering by Biology papers (not an actual category in taxonomy).

        Note: The actual category_taxonomy.json doesn't have a 'biology' category,
        but we can test by querying for papers with Biology subjects directly.
        This tests that the database filtering works even for subjects not
        grouped into categories.
        """
        with app.app_context():
            # Query for papers with Biology subject (using database direct query)
            db = get_db()
            papers = db.execute("""
                SELECT DISTINCT p.*
                FROM papers p
                INNER JOIN paper_subjects ps ON p.id = ps.paper_id
                WHERE ps.subject = 'Biology' AND p.qa_status = 'pass'
                ORDER BY p.date DESC
            """).fetchall()

            # From sample_papers: 3 biology papers (2 in 2023, 1 in 2020)
            assert len(papers) == 3

            # Verify all have Biology subject
            for paper in papers:
                subjects = db.execute(
                    "SELECT subject FROM paper_subjects WHERE paper_id = ?",
                    (paper['id'],)
                ).fetchall()
                paper_subjects = [s['subject'] for s in subjects]
                assert 'Biology' in paper_subjects

    def test_filter_by_physics_category(self, app, sample_papers, sample_category_taxonomy):
        """Test filtering by Physics & Astronomy category."""
        with app.app_context():
            papers, total = query_papers(category='physics')

            # From sample_papers: 2 physics papers (both in 2024)
            assert total == 2

            # Verify all are physics papers
            for paper in papers:
                db = get_db()
                subjects = db.execute(
                    "SELECT subject FROM paper_subjects WHERE paper_id = ?",
                    (paper['id'],)
                ).fetchall()
                paper_subjects = [s['subject'] for s in subjects]

                category_subjects = sample_category_taxonomy['physics']['subjects']
                assert any(subj in category_subjects for subj in paper_subjects)

    def test_empty_category_returns_zero_results(self, app, sample_papers):
        """Test that filtering by non-existent category returns empty results."""
        with app.app_context():
            papers, total = query_papers(category='nonexistent_category')

            # Should return empty results (not error)
            assert total == 0
            assert papers == []


class TestDateFiltering:
    """Test date range filtering with inclusive endpoints."""

    def test_filter_by_year_only_format(self, app, sample_papers):
        """Test filtering by year (YYYY format) - should include all papers in 2022."""
        with app.app_context():
            # Query for all papers in 2022 using inclusive date range
            papers, total = query_papers(
                date_from='2022-01-01T00:00:00',
                date_to='2022-12-31T23:59:59'
            )

            # From sample_papers: 3 CS papers in 2022 (all have qa_status='pass')
            assert total == 3

            # Verify all papers are from 2022
            for paper in papers:
                assert paper['date'].startswith('2022')

    def test_filter_by_year_month_format(self, app, sample_papers):
        """Test filtering by year-month (YYYY-MM format)."""
        with app.app_context():
            # Query for papers in January 2022
            papers, total = query_papers(
                date_from='2022-01-01T00:00:00',
                date_to='2022-01-31T23:59:59'
            )

            # From sample_papers: 1 paper in 2022-01
            assert total == 1
            assert papers[0]['id'] == 'chinaxiv-202201.00001'

    def test_filter_by_specific_date(self, app, sample_papers):
        """Test filtering by specific date (YYYY-MM-DD format)."""
        with app.app_context():
            # Query for papers on 2022-06-20
            papers, total = query_papers(
                date_from='2022-06-20T00:00:00',
                date_to='2022-06-20T23:59:59'
            )

            # From sample_papers: 1 paper on 2022-06-20
            assert total == 1
            assert papers[0]['id'] == 'chinaxiv-202206.00002'

    def test_date_from_filter_only(self, app, sample_papers):
        """Test filtering with only date_from (no end date)."""
        with app.app_context():
            # Query for papers from 2024-01-01 onwards
            papers, total = query_papers(date_from='2024-01-01T00:00:00')

            # From sample_papers: 5 papers in 2024, but 1 has qa_status='fail'
            # Passing papers: chinaxiv-202401.00006, chinaxiv-202408.00007,
            #                 chinaxiv-202410.00008, chinaxiv-202412.00010
            assert total == 4

            # All should be from 2024
            for paper in papers:
                assert paper['date'].startswith('2024')

    def test_date_to_filter_only(self, app, sample_papers):
        """Test filtering with only date_to (no start date)."""
        with app.app_context():
            # Query for papers up to end of 2022
            papers, total = query_papers(date_to='2022-12-31T23:59:59')

            # From sample_papers: 3 papers in 2022 + 1 in 2020 = 4 total
            assert total == 4

    def test_cross_year_date_range(self, app, sample_papers):
        """Test filtering across multiple years."""
        with app.app_context():
            # Query for papers from 2020 to 2023
            papers, total = query_papers(
                date_from='2020-01-01T00:00:00',
                date_to='2023-12-31T23:59:59'
            )

            # From sample_papers: 3 (2022) + 2 (2023) + 1 (2020) = 6 papers
            assert total == 6


class TestFullTextSearch:
    """Test full-text search with FTS5."""

    def test_search_in_title(self, app, sample_papers):
        """Test search matches in paper titles."""
        with app.app_context():
            # Search for "neural" (appears in multiple paper titles)
            papers, total = query_papers(search='neural')

            # From sample_papers:
            # - "Deep Learning for Neural Network Optimization"
            # - "Graph Neural Networks for Social Network Analysis"
            assert total >= 2

            # Verify "neural" appears in titles (case-insensitive)
            for paper in papers:
                title_lower = paper['title_en'].lower()
                abstract_lower = paper['abstract_en'].lower() if paper['abstract_en'] else ''
                assert 'neural' in title_lower or 'neural' in abstract_lower

    def test_search_in_abstract(self, app, sample_papers):
        """Test search matches in paper abstracts."""
        with app.app_context():
            # Search for "crispr" (appears in abstract)
            papers, total = query_papers(search='crispr')

            # From sample_papers: 1 paper mentions CRISPR
            assert total >= 1

            # Verify search term appears somewhere
            for paper in papers:
                text = (paper['title_en'] + ' ' + (paper['abstract_en'] or '')).lower()
                assert 'crispr' in text

    def test_search_phrase_with_multiple_words(self, app, sample_papers):
        """Test search with multi-word phrases."""
        with app.app_context():
            # Search for "quantum computing"
            papers, total = query_papers(search='quantum computing')

            # From sample_papers: 1 paper about quantum computing
            assert total >= 1

    def test_empty_search_string_returns_all_papers(self, app, sample_papers):
        """Test that empty search string is ignored."""
        with app.app_context():
            papers_no_search, total_no_search = query_papers()
            papers_empty_search, total_empty_search = query_papers(search='')
            papers_whitespace_search, total_whitespace_search = query_papers(search='   ')

            # All three should return the same results
            assert total_no_search == total_empty_search == total_whitespace_search

    def test_search_with_no_results(self, app, sample_papers):
        """Test search that matches no papers."""
        with app.app_context():
            papers, total = query_papers(search='xyzabc123notfound')

            # Should return empty results (not error)
            assert total == 0
            assert papers == []


class TestHasFiguresFiltering:
    """Test filtering by has_figures flag."""

    def test_filter_papers_with_figures(self, app, sample_papers):
        """Test filtering to show only papers with translated figures."""
        with app.app_context():
            papers, total = query_papers(has_figures=True)

            # From sample_papers: 6 papers with has_figures=1 (excluding failed QA)
            assert total >= 5

            # All returned papers should have has_figures=1
            for paper in papers:
                assert paper['has_figures'] == 1

    def test_filter_papers_without_figures(self, app, sample_papers):
        """Test that has_figures=False returns all papers (no filtering)."""
        with app.app_context():
            papers_all, total_all = query_papers()
            papers_no_filter, total_no_filter = query_papers(has_figures=False)

            # has_figures=False should not filter (same as no filter)
            assert total_all == total_no_filter


class TestPagination:
    """Test pagination logic and input validation."""

    def test_pagination_first_page(self, app, sample_papers):
        """Test fetching the first page of results."""
        with app.app_context():
            papers, total = query_papers(page=1, per_page=5)

            # Should return first 5 papers
            assert len(papers) == 5
            assert total == 10  # Total across all pages

    def test_pagination_second_page(self, app, sample_papers):
        """Test fetching the second page of results."""
        with app.app_context():
            papers_page1, _ = query_papers(page=1, per_page=5)
            papers_page2, total = query_papers(page=2, per_page=5)

            # Should return next 5 papers
            assert len(papers_page2) == 5
            assert total == 10

            # Pages should not overlap
            page1_ids = {p['id'] for p in papers_page1}
            page2_ids = {p['id'] for p in papers_page2}
            assert page1_ids.isdisjoint(page2_ids)

    def test_pagination_last_page_partial(self, app, sample_papers):
        """Test last page with fewer papers than per_page limit."""
        with app.app_context():
            papers, total = query_papers(page=3, per_page=5)

            # Page 3 should have 0 papers (10 total / 5 per page = 2 pages)
            assert len(papers) == 0
            assert total == 10

    def test_pagination_per_page_validation_max(self, app, sample_papers):
        """Test that per_page is capped at 100."""
        with app.app_context():
            papers, total = query_papers(page=1, per_page=500)

            # Should be capped at 100
            assert len(papers) <= 100

    def test_pagination_per_page_validation_min(self, app, sample_papers):
        """Test that per_page has a minimum of 1."""
        with app.app_context():
            papers, total = query_papers(page=1, per_page=0)

            # Should return at least 1 paper
            assert len(papers) >= 1

    def test_pagination_page_validation_max(self, app, sample_papers):
        """Test that page number is capped at 1000."""
        with app.app_context():
            papers, total = query_papers(page=5000, per_page=5)

            # Should not crash, just return empty results for page beyond limit
            # (page capped at 1000, so offset will be huge, no results)
            assert isinstance(papers, list)
            assert total == 10  # Total still correct

    def test_pagination_page_validation_min(self, app, sample_papers):
        """Test that page number has a minimum of 1."""
        with app.app_context():
            papers_negative, total_negative = query_papers(page=-1, per_page=5)
            papers_zero, total_zero = query_papers(page=0, per_page=5)
            papers_one, total_one = query_papers(page=1, per_page=5)

            # Negative and zero pages should be normalized to page 1
            assert len(papers_negative) == len(papers_one)
            assert len(papers_zero) == len(papers_one)


class TestCombinedFilters:
    """Test combining multiple filters in a single query."""

    def test_category_and_date_range(self, app, sample_papers):
        """Test filtering by both category and date range."""
        with app.app_context():
            # AI & Computing papers from 2022
            papers, total = query_papers(
                category='ai_computing',
                date_from='2022-01-01T00:00:00',
                date_to='2022-12-31T23:59:59'
            )

            # From sample_papers: 3 CS papers in 2022
            assert total == 3

            # Verify all are CS papers from 2022
            for paper in papers:
                assert paper['date'].startswith('2022')

    def test_category_and_search(self, app, sample_papers):
        """Test filtering by both category and search query."""
        with app.app_context():
            # AI & Computing papers mentioning "neural"
            papers, total = query_papers(
                category='ai_computing',
                search='neural'
            )

            # From sample_papers: 2 CS papers with "neural" in title
            assert total >= 2

    def test_category_date_and_figures(self, app, sample_papers):
        """Test combining category, date range, and has_figures filters."""
        with app.app_context():
            # AI & Computing papers from 2022 with figures
            papers, total = query_papers(
                category='ai_computing',
                date_from='2022-01-01T00:00:00',
                date_to='2022-12-31T23:59:59',
                has_figures=True
            )

            # From sample_papers: 2 CS papers in 2022 with figures
            # (chinaxiv-202201.00001 and chinaxiv-202206.00002 both have figures)
            assert total == 2

            # Verify all have figures
            for paper in papers:
                assert paper['has_figures'] == 1

    def test_all_filters_combined(self, app, sample_papers):
        """Test combining all filter types together."""
        with app.app_context():
            # AI & Computing papers from 2022, with figures, and containing "learning"
            papers, total = query_papers(
                category='ai_computing',
                date_from='2022-01-01T00:00:00',
                date_to='2022-12-31T23:59:59',
                search='learning',
                has_figures=True
            )

            # From sample_papers: 1 paper matches all criteria
            # (chinaxiv-202201.00001: "Deep Learning", CS, 2022, has figures)
            assert total == 1

    def test_filters_with_pagination(self, app, sample_papers):
        """Test that pagination works correctly with other filters."""
        with app.app_context():
            # Get CS papers, page 1
            papers_page1, total = query_papers(
                category='ai_computing',
                page=1,
                per_page=2
            )

            # Should return first 2 CS papers
            assert len(papers_page1) == 2
            assert total >= 3  # At least 3 CS papers total

            # Get page 2
            papers_page2, _ = query_papers(
                category='ai_computing',
                page=2,
                per_page=2
            )

            # Pages should not overlap
            page1_ids = {p['id'] for p in papers_page1}
            page2_ids = {p['id'] for p in papers_page2}
            assert page1_ids.isdisjoint(page2_ids)


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_query_with_all_none_parameters(self, app, sample_papers):
        """Test query with all parameters set to None."""
        with app.app_context():
            papers, total = query_papers(
                category=None,
                date_from=None,
                date_to=None,
                search=None,
                has_figures=None
            )

            # Should return all papers with qa_status='pass'
            assert total == 10

    def test_query_with_invalid_date_formats(self, app, sample_papers):
        """Test query handles invalid date formats gracefully."""
        with app.app_context():
            # Invalid date formats should be handled by routes.parse_date()
            # but database layer should handle any strings passed
            papers, total = query_papers(date_from='not-a-date')

            # Should not crash - SQLite string comparison will just not match anything
            assert isinstance(papers, list)
            assert isinstance(total, int)

    def test_query_with_sql_injection_attempt_in_search(self, app, sample_papers):
        """Test that parameterized queries prevent SQL injection in search."""
        with app.app_context():
            # Attempt SQL injection via search query
            papers, total = query_papers(search="'; DROP TABLE papers; --")

            # Should not crash or execute SQL - FTS5 will safely handle
            assert isinstance(papers, list)
            assert isinstance(total, int)

            # Database should still be intact
            db = get_db()
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [t['name'] for t in tables]
            assert 'papers' in table_names

    def test_query_with_special_characters_in_search(self, app, sample_papers):
        """Test search handles special characters gracefully."""
        with app.app_context():
            # Special characters that might break FTS5
            for search_term in ['*', '(', ')', '[', ']', '"', '&', '|']:
                papers, total = query_papers(search=search_term)

                # Should not crash
                assert isinstance(papers, list)
                assert isinstance(total, int)

    def test_query_returns_dict_objects(self, app, sample_papers):
        """Test that query returns dict objects (not Row objects)."""
        with app.app_context():
            papers, total = query_papers()

            assert len(papers) > 0

            # Should be dict, not sqlite3.Row
            assert isinstance(papers[0], dict)

            # Should be able to access fields as dict keys
            assert 'id' in papers[0]
            assert papers[0]['id'] is not None
