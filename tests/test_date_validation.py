"""
Test suite for date validation functions (parse_date and parse_date_end).

This module tests the critical parse_date_end() bug fix that ensures
date ranges are INCLUSIVE. Without this fix, queries like "to=2022"
would incorrectly exclude all papers from 2022.

Tests cover:
- YYYY, YYYY-MM, YYYY-MM-DD format parsing
- Start-of-period logic (parse_date)
- End-of-period logic (parse_date_end) - CRITICAL BUG FIX
- Leap year handling
- Whitespace handling
- Invalid format handling
"""

from app.routes import parse_date, parse_date_end


class TestParseDateStart:
    """Test parse_date() function for start-of-period dates."""

    def test_full_date_format(self):
        """Test YYYY-MM-DD format returns start of day."""
        result = parse_date('2022-01-15')
        assert result == '2022-01-15T00:00:00'

    def test_year_month_format(self):
        """Test YYYY-MM format returns first day of month."""
        result = parse_date('2022-06')
        assert result == '2022-06-01T00:00:00'

    def test_year_only_format(self):
        """Test YYYY format returns first day of year."""
        result = parse_date('2022')
        assert result == '2022-01-01T00:00:00'

    def test_empty_string_returns_default(self):
        """Test empty string returns None (default)."""
        result = parse_date('')
        assert result is None

    def test_none_returns_default(self):
        """Test None input returns None (default)."""
        result = parse_date(None)
        assert result is None

    def test_whitespace_only_returns_default(self):
        """Test whitespace-only string returns None."""
        result = parse_date('   ')
        assert result is None

    def test_leading_trailing_whitespace_stripped(self):
        """Test whitespace is trimmed before parsing."""
        result = parse_date('  2022-01-15  ')
        assert result == '2022-01-15T00:00:00'

    def test_invalid_format_returns_default(self):
        """Test invalid date format returns None."""
        result = parse_date('invalid-date')
        assert result is None

    def test_malformed_date_returns_default(self):
        """Test malformed date (invalid day) returns None."""
        result = parse_date('2022-02-30')  # February only has 28/29 days
        assert result is None

    def test_custom_default_value(self):
        """Test custom default value is returned on error."""
        result = parse_date('', default='custom_default')
        assert result == 'custom_default'


class TestParseDateEnd:
    """
    Test parse_date_end() function for end-of-period dates.

    CRITICAL BUG FIX: This function ensures date ranges are INCLUSIVE.
    Without it, queries like "to=2022" would exclude all papers in 2022.
    """

    def test_full_date_format_returns_end_of_day(self):
        """Test YYYY-MM-DD format returns end of day (23:59:59)."""
        result = parse_date_end('2022-01-15')
        assert result == '2022-01-15T23:59:59'

    def test_year_month_format_returns_end_of_month(self):
        """Test YYYY-MM format returns last day of month at 23:59:59."""
        # January (31 days)
        result = parse_date_end('2022-01')
        assert result == '2022-01-31T23:59:59'

        # April (30 days)
        result = parse_date_end('2022-04')
        assert result == '2022-04-30T23:59:59'

    def test_year_only_format_returns_end_of_year(self):
        """
        Test YYYY format returns end of year (Dec 31, 23:59:59).

        This is the CRITICAL test for the bug fix. Without parse_date_end(),
        "to=2022" would resolve to 2022-01-01T00:00:00, excluding all papers
        from 2022.
        """
        result = parse_date_end('2022')
        assert result == '2022-12-31T23:59:59'

    def test_february_non_leap_year(self):
        """Test February end date in non-leap year (28 days)."""
        result = parse_date_end('2022-02')
        assert result == '2022-02-28T23:59:59'

    def test_february_leap_year(self):
        """Test February end date in leap year (29 days)."""
        result = parse_date_end('2024-02')
        assert result == '2024-02-29T23:59:59'

    def test_december_last_month(self):
        """Test December end date calculation."""
        result = parse_date_end('2022-12')
        assert result == '2022-12-31T23:59:59'

    def test_empty_string_returns_default(self):
        """Test empty string returns None (default)."""
        result = parse_date_end('')
        assert result is None

    def test_none_returns_default(self):
        """Test None input returns None (default)."""
        result = parse_date_end(None)
        assert result is None

    def test_whitespace_only_returns_default(self):
        """Test whitespace-only string returns None."""
        result = parse_date_end('   ')
        assert result is None

    def test_leading_trailing_whitespace_stripped(self):
        """Test whitespace is trimmed before parsing."""
        result = parse_date_end('  2022-01-15  ')
        assert result == '2022-01-15T23:59:59'

    def test_invalid_format_returns_default(self):
        """Test invalid date format returns None."""
        result = parse_date_end('invalid-date')
        assert result is None

    def test_malformed_date_returns_default(self):
        """Test malformed date (invalid day) returns None."""
        result = parse_date_end('2022-02-30')  # February only has 28/29 days
        assert result is None

    def test_custom_default_value(self):
        """Test custom default value is returned on error."""
        result = parse_date_end('', default='custom_default')
        assert result == 'custom_default'


class TestDateRangeInclusion:
    """
    Integration tests to verify the bug fix works in practice.

    These tests verify that date ranges are truly INCLUSIVE when using
    parse_date() for start dates and parse_date_end() for end dates.
    """

    def test_year_range_is_inclusive(self):
        """
        Test that from=2022&to=2022 includes all papers in 2022.

        This is the PRIMARY test case that would fail without parse_date_end().
        """
        start = parse_date('2022')
        end = parse_date_end('2022')

        # Start should be first moment of 2022
        assert start == '2022-01-01T00:00:00'

        # End should be last moment of 2022
        assert end == '2022-12-31T23:59:59'

        # Test that a paper dated mid-2022 would be included
        paper_date = '2022-06-15T12:00:00'
        assert start <= paper_date <= end

    def test_month_range_is_inclusive(self):
        """Test that from=2022-02&to=2022-02 includes all papers in Feb 2022."""
        start = parse_date('2022-02')
        end = parse_date_end('2022-02')

        assert start == '2022-02-01T00:00:00'
        assert end == '2022-02-28T23:59:59'

        # Test that a paper dated mid-February would be included
        paper_date = '2022-02-15T12:00:00'
        assert start <= paper_date <= end

    def test_day_range_is_inclusive(self):
        """Test that from=2022-01-15&to=2022-01-15 includes papers on that day."""
        start = parse_date('2022-01-15')
        end = parse_date_end('2022-01-15')

        assert start == '2022-01-15T00:00:00'
        assert end == '2022-01-15T23:59:59'

        # Test that a paper dated during the day would be included
        paper_date = '2022-01-15T12:00:00'
        assert start <= paper_date <= end

    def test_cross_year_range(self):
        """Test that from=2020&to=2024 includes papers across multiple years."""
        start = parse_date('2020')
        end = parse_date_end('2024')

        assert start == '2020-01-01T00:00:00'
        assert end == '2024-12-31T23:59:59'

        # Test papers in different years are included
        assert start <= '2020-06-15T12:00:00' <= end
        assert start <= '2022-06-15T12:00:00' <= end
        assert start <= '2024-11-15T12:00:00' <= end

    def test_end_of_february_leap_year_inclusive(self):
        """Test that Feb 29 is included in leap year month ranges."""
        start = parse_date('2024-02')
        end = parse_date_end('2024-02')

        assert start == '2024-02-01T00:00:00'
        assert end == '2024-02-29T23:59:59'  # Leap year has 29 days

        # Paper on Feb 29 should be included
        paper_date = '2024-02-29T23:00:00'
        assert start <= paper_date <= end
