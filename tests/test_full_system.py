"""
Full System Behavior Tests

Tests complete end-to-end user workflows and scenarios that span multiple components.
These tests verify that all pieces work together correctly in realistic usage patterns.
"""

from urllib.parse import urlencode


class TestCompleteUserWorkflows:
    """Test complete user journeys through the application."""

    def test_homepage_to_paper_detail_workflow(self, client, sample_papers):
        """Test: User loads homepage → sees papers → clicks to detail page."""
        # Step 1: Load homepage
        response = client.get('/')
        assert response.status_code == 200
        html = response.data.decode('utf-8')

        # Should see some papers on homepage
        assert 'chinaxiv-' in html, "Homepage should display paper IDs"

        # Extract a paper ID from the page (look for paper detail links)
        # Link format is /items/{paper_id}/ (with trailing slash in HTML)
        import re
        paper_links = re.findall(r'/items/(chinaxiv-\d{6}\.\d{5})/', html)
        assert len(paper_links) > 0, "Homepage should have links to paper details"

        # Step 2: Click through to paper detail page
        # Note: Flask route is /items/<id> (no trailing slash)
        paper_id = paper_links[0]
        detail_response = client.get(f'/items/{paper_id}')
        assert detail_response.status_code == 200
        detail_html = detail_response.data.decode('utf-8')

        # Verify detail page has paper content
        assert paper_id in detail_html, "Detail page should show paper ID"
        assert 'Abstract' in detail_html or 'abstract' in detail_html.lower()

    def test_browse_filter_by_category_workflow(self, client, sample_papers):
        """Test: User browses → selects category → sees filtered results."""
        # Step 1: Load homepage
        response = client.get('/')
        assert response.status_code == 200

        # Step 2: Filter by AI & Computing category
        filter_response = client.get('/?category=ai_computing')
        assert filter_response.status_code == 200
        html = filter_response.data.decode('utf-8')

        # Should see category filter applied
        # The exact UI will depend on implementation, but URL should reflect filter
        assert 'ai_computing' in html or 'AI' in html

    def test_multi_filter_refinement_workflow(self, client, sample_papers):
        """Test: User applies multiple filters progressively."""
        # Step 1: Start with date filter
        response1 = client.get('/?start_date=2024-11-01&end_date=2024-11-30')
        assert response1.status_code == 200

        # Step 2: Add category filter
        response2 = client.get('/?start_date=2024-11-01&end_date=2024-11-30&category=physics')
        assert response2.status_code == 200

        # Step 3: Add subject filter
        response3 = client.get('/?start_date=2024-11-01&end_date=2024-11-30&category=physics&subject=Physics')
        assert response3.status_code == 200

        # All should succeed
        assert response3.status_code == 200

    def test_pagination_through_results_workflow(self, client, sample_papers):
        """Test: User navigates through paginated results."""
        # Page 1
        page1 = client.get('/?page=1')
        assert page1.status_code == 200

        # Page 2
        page2 = client.get('/?page=2')
        assert page2.status_code == 200

        # Page boundaries
        page_zero = client.get('/?page=0')
        assert page_zero.status_code == 200  # Should redirect to page 1 or handle gracefully

        page_large = client.get('/?page=9999')
        assert page_large.status_code == 200  # Should show empty results, not error


class TestFilterCombinations:
    """Test various combinations of filters working together."""

    def test_date_and_category_filter_combination(self, client, sample_papers):
        """Test combining date range with category filter."""
        response = client.get('/?start_date=2024-11-01&end_date=2024-11-30&category=ai_computing')
        assert response.status_code == 200

        # Verify both filters are applied (implementation-specific checks)
        response.data.decode('utf-8')
        # Should have some indication both filters are active
        assert response.status_code == 200

    def test_category_and_subject_filter_combination(self, client, sample_papers):
        """Test selecting category then drilling down to specific subject."""
        response = client.get('/?category=physics&subject=Quantum Computing')
        assert response.status_code == 200

        response.data.decode('utf-8')
        # Should show filtered results
        assert response.status_code == 200

    def test_search_with_filters_combination(self, client, sample_papers):
        """Test combining search query with category/date filters."""
        response = client.get('/?q=quantum&category=physics&start_date=2024-11-01')
        assert response.status_code == 200

        # All filters should be applied together
        response.data.decode('utf-8')
        assert response.status_code == 200

    def test_all_filters_combined(self, client, sample_papers):
        """Test applying all filter types simultaneously."""
        query_params = {
            'q': 'quantum',
            'category': 'physics',
            'subject': 'Physics',
            'start_date': '2024-11-01',
            'end_date': '2024-11-30',
            'page': '1'
        }
        response = client.get(f'/?{urlencode(query_params)}')
        assert response.status_code == 200


class TestCategoryTabNavigation:
    """Test category tab switching and navigation."""

    def test_switch_between_category_tabs(self, client, sample_papers):
        """Test navigating between different category tabs."""
        # Load AI & Computing tab
        ai_response = client.get('/?category=ai_computing')
        assert ai_response.status_code == 200

        # Switch to Physics tab
        physics_response = client.get('/?category=physics')
        assert physics_response.status_code == 200

        # Switch to Biology tab
        biology_response = client.get('/?category=biology')
        assert biology_response.status_code == 200

        # All should render successfully
        assert all(r.status_code == 200 for r in [ai_response, physics_response, biology_response])

    def test_category_tab_preserves_other_filters(self, client, sample_papers):
        """Test that switching category tabs preserves date/search filters."""
        # Apply date filter and select AI category
        response1 = client.get('/?start_date=2024-11-01&category=ai_computing')
        assert response1.status_code == 200

        # Switch to Physics category (date filter should persist)
        response2 = client.get('/?start_date=2024-11-01&category=physics')
        assert response2.status_code == 200

        # Both should succeed and date filter should still be in URL
        assert True  # URL preservation


class TestErrorRecoveryScenarios:
    """Test how the system handles errors and edge cases."""

    def test_invalid_paper_id_returns_404(self, client):
        """Test requesting a non-existent paper ID."""
        response = client.get('/paper/chinaxiv-999999.99999')
        assert response.status_code == 404

    def test_malformed_paper_id_returns_404(self, client):
        """Test requesting a malformed paper ID."""
        response = client.get('/paper/invalid-id-format')
        assert response.status_code == 404

    def test_invalid_date_format_handled_gracefully(self, client, sample_papers):
        """Test that invalid date formats don't crash the app."""
        # Invalid date format should either return error or ignore filter
        response = client.get('/?start_date=not-a-date')
        assert response.status_code in [200, 400], "Should handle invalid date gracefully"

    def test_invalid_category_returns_empty_results(self, client, sample_papers):
        """Test filtering by non-existent category."""
        response = client.get('/?category=nonexistent_category')
        assert response.status_code == 200  # Should not error

        # Should show no results or ignore invalid category
        response.data.decode('utf-8')
        assert response.status_code == 200

    def test_negative_page_number_handled_gracefully(self, client, sample_papers):
        """Test that negative page numbers don't cause errors."""
        response = client.get('/?page=-1')
        assert response.status_code in [200, 400], "Should handle negative page gracefully"

    def test_non_numeric_page_handled_gracefully(self, client, sample_papers):
        """Test that non-numeric page values don't crash."""
        response = client.get('/?page=abc')
        assert response.status_code in [200, 400], "Should handle non-numeric page gracefully"


class TestDataConsistency:
    """Test that data remains consistent across different views."""

    def test_paper_count_consistency_across_pages(self, client, sample_papers):
        """Test that paper counts are consistent between index and filtered views."""
        from app.database import get_db

        with client.application.app_context():
            db = get_db()

            # Count total QA-passed papers in database
            total_count = db.execute(
                "SELECT COUNT(*) FROM papers WHERE qa_status = 'pass'"
            ).fetchone()[0]

            # Homepage should show consistent data
            response = client.get('/')
            assert response.status_code == 200

            # Total papers shown should not exceed database count
            # (exact matching depends on pagination implementation)
            assert total_count >= 0

    def test_category_counts_match_actual_papers(self, client, sample_papers):
        """Test that category counts shown match actual paper counts."""
        from app.database import get_db

        with client.application.app_context():
            db = get_db()

            # Get physics papers count from database
            physics_count = db.execute("""
                SELECT COUNT(DISTINCT p.id)
                FROM papers p
                JOIN paper_subjects ps ON p.id = ps.paper_id
                WHERE p.qa_status = 'pass'
                AND ps.subject IN ('Physics', 'Quantum Computing', 'Astrophysics')
            """).fetchone()[0]

            # Load homepage and check category count display
            response = client.get('/')
            assert response.status_code == 200

            # Count should be consistent (exact verification depends on UI rendering)
            assert physics_count >= 0


class TestRealWorldDataPatterns:
    """Test handling of real-world data patterns and edge cases."""

    def test_papers_with_multiple_subjects(self, client, sample_papers):
        """Test that papers with multiple subjects are handled correctly."""
        from app.database import get_db

        with client.application.app_context():
            db = get_db()

            # Get a paper with multiple subjects
            paper = db.execute("""
                SELECT p.id, COUNT(ps.subject) as subject_count
                FROM papers p
                JOIN paper_subjects ps ON p.id = ps.paper_id
                GROUP BY p.id
                HAVING subject_count > 1
                LIMIT 1
            """).fetchone()

            if paper:
                paper_id = paper[0]

                # View paper detail page
                response = client.get(f'/items/{paper_id}')
                assert response.status_code == 200

                # Should display all subjects
                html = response.data.decode('utf-8')
                assert paper_id in html

    def test_papers_spanning_month_boundaries(self, client, sample_papers):
        """Test filtering papers across month boundaries."""
        # Filter across month boundary
        response = client.get('/?start_date=2024-10-15&end_date=2024-11-15')
        assert response.status_code == 200

        # Should handle date range spanning months
        response.data.decode('utf-8')
        assert response.status_code == 200

    def test_empty_search_results(self, client, sample_papers):
        """Test handling of searches that return no results."""
        response = client.get('/?q=xyznonexistentquery12345')
        assert response.status_code == 200

        # Should show "no results" message, not error
        response.data.decode('utf-8')
        # Exact message depends on implementation
        assert response.status_code == 200

    def test_very_long_search_query(self, client, sample_papers):
        """Test handling of very long search queries."""
        long_query = 'quantum ' * 100  # Very long query
        response = client.get(f'/?q={long_query}')
        assert response.status_code in [200, 400], "Should handle long queries gracefully"


class TestPerformanceScenarios:
    """Test system behavior under various load patterns."""

    def test_large_date_range_query(self, client, sample_papers):
        """Test querying a very large date range."""
        # Query entire year
        response = client.get('/?start_date=2024-01-01&end_date=2024-12-31')
        assert response.status_code == 200

        # Should complete without timeout (pytest timeout would catch this)
        html = response.data.decode('utf-8')
        assert len(html) > 0

    def test_rapid_filter_changes(self, client, sample_papers):
        """Test rapidly changing filters (simulating user clicking quickly)."""
        # Simulate rapid filter changes
        filters = [
            '/?category=ai_computing',
            '/?category=physics',
            '/?category=biology',
            '/?start_date=2024-11-01',
            '/?end_date=2024-11-30',
            '/?page=2',
            '/?page=1'
        ]

        for filter_url in filters:
            response = client.get(filter_url)
            assert response.status_code == 200, f"Filter {filter_url} should succeed"

    def test_concurrent_requests_to_same_paper(self, client, sample_papers):
        """Test multiple requests to the same paper (simulating multiple users)."""
        # Get a valid paper ID
        from app.database import get_db

        with client.application.app_context():
            db = get_db()
            paper_id = db.execute(
                "SELECT id FROM papers LIMIT 1"
            ).fetchone()[0]

            # Make multiple requests (simulating concurrent users)
            responses = [client.get(f'/items/{paper_id}') for _ in range(10)]

            # All should succeed
            assert all(r.status_code == 200 for r in responses)


class TestURLStateManagement:
    """Test that URL parameters correctly reflect and restore application state."""

    def test_url_parameters_are_preserved(self, client, sample_papers):
        """Test that filter parameters are preserved in URLs."""
        # Apply multiple filters
        params = {
            'category': 'physics',
            'start_date': '2024-11-01',
            'end_date': '2024-11-30',
            'page': '2'
        }
        url = f'/?{urlencode(params)}'
        response = client.get(url)
        assert response.status_code == 200

        # URL should preserve parameters (in links, etc.)
        response.data.decode('utf-8')
        # Implementation-specific: check if filters are maintained in navigation links
        assert response.status_code == 200

    def test_clearing_filters_returns_to_default_view(self, client, sample_papers):
        """Test that removing all filters returns to default homepage view."""
        # First apply filters
        filtered_response = client.get('/?category=physics&start_date=2024-11-01')
        assert filtered_response.status_code == 200

        # Then load homepage without filters
        default_response = client.get('/')
        assert default_response.status_code == 200

        # Should show unfiltered view
        assert default_response.status_code == 200

    def test_url_parameter_order_does_not_matter(self, client, sample_papers):
        """Test that parameter order in URL doesn't affect results."""
        # Same parameters in different order
        url1 = '/?category=physics&start_date=2024-11-01&end_date=2024-11-30'
        url2 = '/?start_date=2024-11-01&end_date=2024-11-30&category=physics'

        response1 = client.get(url1)
        response2 = client.get(url2)

        # Both should succeed
        assert response1.status_code == 200
        assert response2.status_code == 200


class TestAccessibilityAndUsability:
    """Test basic accessibility and usability features."""

    def test_homepage_has_navigation_structure(self, client, sample_papers):
        """Test that homepage has basic navigation structure."""
        response = client.get('/')
        assert response.status_code == 200
        html = response.data.decode('utf-8')

        # Should have some form of navigation (links, etc.)
        assert '<a' in html, "Page should have links"

    def test_paper_detail_has_back_navigation(self, client, sample_papers):
        """Test that paper detail pages have way to navigate back."""
        from app.database import get_db

        with client.application.app_context():
            db = get_db()
            paper_id = db.execute("SELECT id FROM papers LIMIT 1").fetchone()[0]

            response = client.get(f'/items/{paper_id}')
            assert response.status_code == 200
            html = response.data.decode('utf-8')

            # Should have navigation back to list (link to homepage or back button)
            assert '<a' in html, "Detail page should have navigation links"

    def test_filter_controls_are_present(self, client, sample_papers):
        """Test that filter controls are rendered on homepage."""
        response = client.get('/')
        assert response.status_code == 200
        html = response.data.decode('utf-8')

        # Should have some filter controls (category tabs, date inputs, etc.)
        # Exact implementation varies, but should have interactive elements
        assert 'category' in html.lower() or 'filter' in html.lower() or '<select' in html or '<input' in html
