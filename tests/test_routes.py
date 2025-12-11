"""
Tests for route helpers and template preparation functions.

These tests verify that:
- Paper data is correctly prepared for templates
- Figure URLs are parsed from JSON
- Error handling works for malformed data
"""

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.routes import _prepare_paper_for_template  # noqa: E402


class TestPrepareForTemplate:
    """Test _prepare_paper_for_template() function."""

    def test_basic_fields_set(self):
        """Verify basic _has_* fields are set from paper data."""
        paper = {
            'id': 'chinaxiv-202201.00001',
            'has_full_text': True,
            'has_figures': True,
            'figure_urls': '[{"number": 1, "url": "https://example.com/fig1.png"}]'
        }
        result = _prepare_paper_for_template(paper)
        assert result['_has_full_text'] is True
        # _has_translated_figures is derived from figure_urls content, not has_figures flag
        assert result['_has_translated_figures'] is True

    def test_has_fields_default_to_false(self):
        """Verify _has_* fields default to False when not present."""
        paper = {'id': 'test'}
        result = _prepare_paper_for_template(paper)
        assert result['_has_full_text'] is False
        assert result['_has_translated_figures'] is False
        assert result['_has_english_pdf'] is False

    def test_english_pdf_url_set(self):
        """Verify english_pdf_url is mapped to _english_pdf_url."""
        paper = {
            'id': 'test',
            'english_pdf_url': 'https://example.com/test.pdf'
        }
        result = _prepare_paper_for_template(paper)
        assert result['_has_english_pdf'] is True
        assert result['_english_pdf_url'] == 'https://example.com/test.pdf'

    def test_figure_urls_parsed_from_json(self):
        """Verify figure_urls JSON column is parsed to _translated_figures list."""
        paper = {
            'id': 'chinaxiv-202201.00001',
            'has_figures': True,
            'figure_urls': '[{"number": 1, "url": "https://example.com/fig1.png"}]'
        }
        result = _prepare_paper_for_template(paper)
        assert result['_translated_figures'] == [{"number": 1, "url": "https://example.com/fig1.png"}]

    def test_figure_urls_multiple_figures(self):
        """Verify multiple figures are parsed correctly."""
        paper = {
            'id': 'test',
            'has_figures': True,
            'figure_urls': json.dumps([
                {"number": 1, "url": "https://example.com/fig1.png"},
                {"number": 2, "url": "https://example.com/fig2.png"},
                {"number": 3, "url": "https://example.com/fig3.png"}
            ])
        }
        result = _prepare_paper_for_template(paper)
        assert len(result['_translated_figures']) == 3
        assert result['_translated_figures'][0]['number'] == 1
        assert result['_translated_figures'][2]['url'] == "https://example.com/fig3.png"

    def test_empty_figure_urls_returns_empty_list(self):
        """Verify None/empty figure_urls results in empty _translated_figures list."""
        paper = {'id': 'test', 'has_figures': False, 'figure_urls': None}
        result = _prepare_paper_for_template(paper)
        assert result['_translated_figures'] == []

    def test_missing_figure_urls_returns_empty_list(self):
        """Verify missing figure_urls key results in empty _translated_figures list."""
        paper = {'id': 'test', 'has_figures': True}
        result = _prepare_paper_for_template(paper)
        assert result['_translated_figures'] == []

    def test_invalid_json_figure_urls_handled(self):
        """Verify malformed JSON in figure_urls doesn't crash."""
        paper = {'id': 'test', 'has_figures': True, 'figure_urls': 'not valid json'}
        result = _prepare_paper_for_template(paper)
        assert result['_translated_figures'] == []  # Graceful fallback

    def test_empty_string_figure_urls_handled(self):
        """Verify empty string in figure_urls results in empty list."""
        paper = {'id': 'test', 'has_figures': True, 'figure_urls': ''}
        result = _prepare_paper_for_template(paper)
        assert result['_translated_figures'] == []

    def test_empty_array_figure_urls(self):
        """Verify empty JSON array in figure_urls results in empty list."""
        paper = {'id': 'test', 'has_figures': True, 'figure_urls': '[]'}
        result = _prepare_paper_for_template(paper)
        assert result['_translated_figures'] == []

    def test_has_translated_figures_derived_from_figure_urls(self):
        """Verify _has_translated_figures is derived from figure_urls content, not has_figures flag.

        This ensures the gallery displays when figure_urls is populated,
        even if has_figures is False (sync script populates URLs but doesn't
        necessarily update the has_figures flag).
        """
        # Case 1: has_figures=False but figure_urls populated -> should show gallery
        paper_with_urls = {
            'id': 'test1',
            'has_figures': False,  # Flag not set
            'figure_urls': '[{"number": 1, "url": "https://example.com/fig1.png"}]'
        }
        result = _prepare_paper_for_template(paper_with_urls)
        assert result['_has_translated_figures'] is True  # Derived from URLs

        # Case 2: has_figures=True but no figure_urls -> should NOT show gallery
        paper_no_urls = {
            'id': 'test2',
            'has_figures': True,  # Flag set
            'figure_urls': None
        }
        result = _prepare_paper_for_template(paper_no_urls)
        assert result['_has_translated_figures'] is False  # No actual URLs

        # Case 3: has_figures=True with empty array -> should NOT show gallery
        paper_empty_array = {
            'id': 'test3',
            'has_figures': True,
            'figure_urls': '[]'
        }
        result = _prepare_paper_for_template(paper_empty_array)
        assert result['_has_translated_figures'] is False  # Empty array

    def test_creators_en_string_converted_to_list(self):
        """Verify string creators_en is converted to list."""
        paper = {
            'id': 'test',
            'creators_en': '["Zhang Wei", "Li Ming"]'
        }
        result = _prepare_paper_for_template(paper)
        assert result['creators_en'] == ["Zhang Wei", "Li Ming"]

    def test_creators_en_invalid_json_becomes_single_item_list(self):
        """Verify invalid JSON creators_en becomes single-item list."""
        paper = {
            'id': 'test',
            'creators_en': 'John Smith'  # Plain string, not JSON
        }
        result = _prepare_paper_for_template(paper)
        assert result['creators_en'] == ['John Smith']

    def test_creators_en_none_becomes_empty_list(self):
        """Verify None creators_en becomes empty list."""
        paper = {'id': 'test', 'creators_en': None}
        result = _prepare_paper_for_template(paper)
        assert result['creators_en'] == []

    def test_body_md_mapped_to_formatted_body_md(self):
        """Verify body_md is mapped to formatted_body_md."""
        paper = {
            'id': 'test',
            'body_md': '# Section 1\n\nThis is the body.'
        }
        result = _prepare_paper_for_template(paper)
        assert result['formatted_body_md'] == '# Section 1\n\nThis is the body.'

    def test_missing_body_md_defaults_to_empty_string(self):
        """Verify missing body_md defaults to empty string."""
        paper = {'id': 'test'}
        result = _prepare_paper_for_template(paper)
        assert result['formatted_body_md'] == ''


class TestFigureUrlsSecurity:
    """Security tests for figure URL handling."""

    def test_figure_urls_xss_in_url_field(self):
        """Verify XSS in figure URL is preserved (template should escape)."""
        paper = {
            'id': 'test',
            'has_figures': True,
            'figure_urls': json.dumps([
                {"number": 1, "url": "javascript:alert('xss')"}
            ])
        }
        result = _prepare_paper_for_template(paper)
        # The function preserves the URL - template escaping handles XSS
        assert result['_translated_figures'][0]['url'] == "javascript:alert('xss')"

    def test_figure_urls_html_in_number_field(self):
        """Verify HTML in figure number field is preserved."""
        paper = {
            'id': 'test',
            'has_figures': True,
            'figure_urls': json.dumps([
                {"number": "<script>alert('xss')</script>", "url": "https://example.com/fig.png"}
            ])
        }
        result = _prepare_paper_for_template(paper)
        # The function preserves the data - template escaping handles XSS
        assert "<script>" in str(result['_translated_figures'][0]['number'])
