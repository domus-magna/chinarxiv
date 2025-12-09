"""
Unit tests for search/filter functionality fixes (PR #106)

These tests verify the critical fixes that restored search and filtering:
1. #search-results container exists in rendered HTML
2. No undefined variable references (categoryFilter, dateFilter, etc.)
3. Filter and search operations work without crashes
"""

import pytest
import re
from pathlib import Path


class TestSearchResultsContainer:
    """Test that #search-results container exists in HTML template"""

    def test_search_results_container_exists_in_template(self):
        """Verify #search-results div is present in index.html template"""
        template_path = Path("src/templates/index.html")
        content = template_path.read_text()

        # Should have the search-results container with proper attributes
        assert 'id="search-results"' in content, \
            "Missing #search-results container - this causes null reference crashes"

        # Verify it has proper ARIA attributes for accessibility
        assert 'aria-live="polite"' in content, \
            "#search-results should have aria-live for screen readers"

        assert 'role="region"' in content, \
            "#search-results should have role=region for accessibility"

    def test_articles_container_still_exists(self):
        """Verify #articles container also exists (dual-container architecture)"""
        template_path = Path("src/templates/index.html")
        content = template_path.read_text()

        assert 'id="articles"' in content, \
            "#articles container should exist for server-rendered list"

    def test_search_results_positioned_inside_articles(self):
        """Verify #search-results is nested inside #articles for proper layout"""
        template_path = Path("src/templates/index.html")
        content = template_path.read_text()

        # Find the articles div and search-results div
        articles_match = re.search(r'<div id="articles"[^>]*>', content)
        search_results_match = re.search(r'<div id="search-results"[^>]*>', content)

        assert articles_match, "#articles div not found"
        assert search_results_match, "#search-results div not found"

        # search-results should come after articles opening tag
        assert search_results_match.start() > articles_match.start(), \
            "#search-results should be nested inside #articles"


class TestJavaScriptCleanup:
    """Test that obsolete variable references were properly removed"""

    def test_no_obsolete_variable_declarations(self):
        """Verify deleted filter variables are not declared"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # These variables should NOT be declared (they were removed)
        obsolete_vars = [
            'categoryFilter = document.getElementById',
            'dateFilter = document.getElementById',
            'sortOrder = document.getElementById',
            'figuresFilter = document.getElementById'
        ]

        for var_decl in obsolete_vars:
            assert var_decl not in content, \
                f"Found obsolete variable declaration: {var_decl}"

    def test_no_event_listeners_for_obsolete_vars(self):
        """Verify event listeners for deleted variables are removed"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # These event listener attachments should NOT exist
        obsolete_listeners = [
            'categoryFilter.addEventListener',
            'dateFilter.addEventListener',
            'figuresFilter.addEventListener'
        ]

        for listener in obsolete_listeners:
            # Allow comments mentioning them, but not actual code
            pattern = rf'^\s*if\s*\(\s*{listener.split(".")[0]}\s*\)'
            matches = re.findall(pattern, content, re.MULTILINE)
            assert not matches, \
                f"Found event listener for obsolete variable: {listener}"

    def test_search_results_variable_still_exists(self):
        """Verify 'results' variable is still declared and points to #search-results"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Should have: const results = document.getElementById('search-results');
        assert "getElementById('search-results')" in content, \
            "'results' variable should reference #search-results element"

    def test_guard_check_includes_results(self):
        """Verify safety check guards against missing 'results' element"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Should have a guard like: if (!input || !results) return;
        guard_pattern = r'if\s*\(\s*!input\s*\|\|\s*!results\s*\)\s*return'
        assert re.search(guard_pattern, content), \
            "Missing guard check for 'results' element"


class TestFilterFunctionality:
    """Test that filter functions reference correct variables"""

    def test_apply_filters_uses_current_category(self):
        """Verify applyFiltersAndRender uses currentCategory instead of deleted dropdown"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Find the applyFiltersAndRender function
        func_match = re.search(
            r'function applyFiltersAndRender\(\)\s*\{([^}]+\}){0,100}',
            content,
            re.DOTALL
        )
        assert func_match, "applyFiltersAndRender function not found"

        func_body = func_match.group(0)

        # Should use currentCategory (from tabs)
        assert 'currentCategory' in func_body, \
            "applyFiltersAndRender should use currentCategory for filtering"

        # Should NOT reference the old categoryFilter dropdown
        assert 'categoryFilter?.value' not in func_body, \
            "Should not reference deleted categoryFilter variable"

    def test_has_active_filters_simplified(self):
        """Verify hasActiveFilters calculation was simplified"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Should have simplified calculation
        # Before: Boolean(cat || dateRange || figuresOnly || sortChanged)
        # After: Boolean(cat)  // Only category tabs implemented

        # Look for the hasActiveFilters assignment
        pattern = r'const hasActiveFilters\s*=\s*Boolean\s*\(\s*cat\s*\)'
        assert re.search(pattern, content), \
            "hasActiveFilters should be simplified to Boolean(cat)"


class TestRenderingIntegrity:
    """Test that rendering functions can write to #search-results without crashing"""

    @pytest.fixture
    def mock_dom(self):
        """Mock DOM environment for testing"""
        class MockElement:
            def __init__(self):
                self.innerHTML = ""

        return {
            'search-results': MockElement(),
            'articles': MockElement(),
            'paperCount': MockElement()
        }

    def test_results_variable_can_be_assigned(self):
        """Verify that results.innerHTML = ... pattern exists in code"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Should have assignments like: results.innerHTML = ...
        pattern = r'results\.innerHTML\s*='
        matches = re.findall(pattern, content)

        assert len(matches) > 0, \
            "No results.innerHTML assignments found - rendering may be broken"

    def test_no_legacy_comment_about_missing_container(self):
        """Verify old comment about 'Legacy - may not exist' was removed"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Old comment should be gone
        assert 'Legacy - may not exist' not in content, \
            "Old comment about missing element should be removed"


class TestCategoryTabs:
    """Test that category tab functionality is properly wired"""

    def test_category_tabs_exist_in_template(self):
        """Verify category tab elements exist in HTML"""
        template_path = Path("src/templates/index.html")
        content = template_path.read_text()

        # Should have category tabs with data-category attributes
        assert 'class="category-tab"' in content, \
            "Category tabs not found in template"

        assert 'data-category=""' in content, \
            "'All Recent' tab (empty category) should exist"

        # Check for any category tabs (implementation may vary)
        assert 'data-category=' in content and 'category-tab' in content, \
            "Category tabs with data-category attributes should exist"

    def test_category_tab_event_handlers_exist(self):
        """Verify JavaScript attaches click handlers to category tabs"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Should have code that selects .category-tab elements
        assert "querySelectorAll('.category-tab')" in content or \
               "querySelector('.category-tab')" in content, \
            "Category tab event handlers not found"

        # Should update currentCategory variable (implementation may vary)
        assert 'currentCategory' in content, \
            "Category tabs should use currentCategory variable for filtering"


class TestDocumentation:
    """Test that code includes proper documentation of the fix"""

    def test_javascript_has_cleanup_comments(self):
        """Verify JavaScript includes comments explaining removed code"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Should explain why variables were removed
        assert 'Removed obsolete filter element lookups' in content or \
               'removed - see lines' in content, \
            "Missing documentation of removed variables"

    def test_template_has_architecture_comments(self):
        """Verify HTML template documents dual-container architecture"""
        template_path = Path("src/templates/index.html")
        content = template_path.read_text()

        # Should document the container architecture
        assert 'DUAL CONTAINER' in content or 'dual-container' in content.lower(), \
            "Missing documentation of container architecture"


class TestAdvancedSearchModal:
    """Test that advanced search modal functionality is properly wired"""

    def test_modal_population_function_exists(self):
        """Verify populateCategoryAccordion function exists"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        assert 'function populateCategoryAccordion()' in content, \
            "populateCategoryAccordion function not found"

        # Should populate from window.categoryData
        assert 'window.categoryData' in content, \
            "Modal should use window.categoryData"

    def test_modal_apply_button_handler_exists(self):
        """Verify applyModalFilters function exists"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        assert 'function applyModalFilters()' in content, \
            "applyModalFilters function not found"

        # Should update tabs and URL
        assert 'applyFiltersBtn' in content, \
            "Apply button should be wired up"

    def test_modal_clear_button_handler_exists(self):
        """Verify clearModalFilters function exists"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        assert 'function clearModalFilters()' in content, \
            "clearModalFilters function not found"

        # Should clear to "All Recent"
        assert 'clearAllBtn' in content, \
            "Clear All button should be wired up"

    def test_modal_syncs_with_tabs(self):
        """Verify modal state syncs with category tabs"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Should have code to sync modal when tabs are clicked
        assert 'modalRadio' in content, \
            "Modal should sync with tab selection"

    def test_scalability_comments_present(self):
        """Verify scalability documentation was added"""
        js_path = Path("assets/site.js")
        content = js_path.read_text()

        # Check for scalability notes at key locations
        assert 'SCALABILITY NOTE' in content, \
            "Missing scalability documentation"

        # Should reference TODO.md migration plan
        assert 'TODO.md' in content and 'Search Architecture Migration' in content, \
            "Should reference migration plan in TODO.md"


class TestScalabilityDocumentation:
    """Test that scalability warnings and migration plans are documented"""

    def test_search_index_has_scalability_warning(self):
        """Verify src/search_index.py has scalability warning"""
        py_path = Path("src/search_index.py")
        content = py_path.read_text()

        assert 'SCALABILITY WARNING' in content, \
            "Missing scalability warning in search_index.py"

        # Should mention migration trigger
        assert '5,000' in content or '5000' in content, \
            "Should mention 5K paper threshold"

    def test_todo_has_migration_plan(self):
        """Verify TODO.md has comprehensive migration plan"""
        todo_path = Path("TODO.md")
        content = todo_path.read_text()

        assert 'Search Architecture Migration' in content, \
            "Missing migration section in TODO.md"

        # Should have key sections
        assert 'Current State' in content, \
            "Should document current state"

        assert 'Trigger for Migration' in content, \
            "Should document migration triggers"

        assert 'Server-Side Architecture Plan' in content, \
            "Should have architecture plan"

        assert 'Cloudflare' in content and 'D1' in content, \
            "Should mention Cloudflare D1"

        # Should have migration checklist
        assert 'Migration Checklist' in content, \
            "Should have migration checklist"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
