"""Tests for data_utils module."""

from datetime import datetime, timedelta, timezone

import pytest

from src.data_utils import filter_by_timestamp, utc_date_range_str, stable_id_from_oai


class TestFilterByTimestamp:
    """Tests for filter_by_timestamp function."""

    def test_filters_old_items(self):
        """Items older than cutoff are filtered out."""
        now = datetime.now()
        items = [
            {"timestamp": (now - timedelta(days=10)).isoformat(), "data": "old"},
            {"timestamp": (now - timedelta(days=1)).isoformat(), "data": "recent"},
            {"timestamp": now.isoformat(), "data": "now"},
        ]
        cutoff = now - timedelta(days=7)

        result = filter_by_timestamp(items, cutoff)

        assert len(result) == 2
        assert result[0]["data"] == "recent"
        assert result[1]["data"] == "now"

    def test_empty_list_returns_empty(self):
        """Empty input returns empty output."""
        cutoff = datetime.now()
        result = filter_by_timestamp([], cutoff)
        assert result == []

    def test_custom_timestamp_key(self):
        """Custom timestamp key is respected."""
        now = datetime.now()
        items = [
            {"created_at": (now - timedelta(days=10)).isoformat(), "data": "old"},
            {"created_at": now.isoformat(), "data": "now"},
        ]
        cutoff = now - timedelta(days=7)

        result = filter_by_timestamp(items, cutoff, timestamp_key="created_at")

        assert len(result) == 1
        assert result[0]["data"] == "now"

    def test_invalid_timestamps_dropped_by_default(self):
        """Items with invalid timestamps are dropped by default."""
        now = datetime.now()
        items = [
            {"timestamp": "invalid-date", "data": "invalid"},
            {"timestamp": now.isoformat(), "data": "valid"},
        ]
        cutoff = now - timedelta(days=7)

        result = filter_by_timestamp(items, cutoff)

        assert len(result) == 1
        assert result[0]["data"] == "valid"

    def test_invalid_timestamps_kept_when_flag_set(self):
        """Items with invalid timestamps are kept when keep_invalid=True."""
        now = datetime.now()
        items = [
            {"timestamp": "invalid-date", "data": "invalid"},
            {"timestamp": now.isoformat(), "data": "valid"},
        ]
        cutoff = now - timedelta(days=7)

        result = filter_by_timestamp(items, cutoff, keep_invalid=True)

        assert len(result) == 2

    def test_missing_timestamp_key_handled(self):
        """Items missing the timestamp key are handled gracefully."""
        now = datetime.now()
        items = [
            {"data": "no_timestamp"},
            {"timestamp": now.isoformat(), "data": "valid"},
        ]
        cutoff = now - timedelta(days=7)

        result = filter_by_timestamp(items, cutoff)

        assert len(result) == 1
        assert result[0]["data"] == "valid"

    def test_naive_cutoff_with_aware_timestamp(self):
        """Naive cutoff with timezone-aware timestamp works correctly.

        When cutoff is naive and item timestamp is aware, the timezone
        should be stripped from the item timestamp for comparison.
        """
        naive_cutoff = datetime(2024, 1, 1, 12, 0, 0)
        # Item timestamp is UTC-aware and newer than cutoff
        items = [
            {"timestamp": "2024-01-01T14:00:00+00:00", "data": "aware_newer"},
            {"timestamp": "2024-01-01T10:00:00+00:00", "data": "aware_older"},
        ]

        result = filter_by_timestamp(items, naive_cutoff)

        assert len(result) == 1
        assert result[0]["data"] == "aware_newer"

    def test_aware_cutoff_with_naive_timestamp(self):
        """Timezone-aware cutoff with naive timestamp works correctly.

        When cutoff is aware and item timestamp is naive, the item
        timestamp should be assumed to be in the same timezone.
        """
        aware_cutoff = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Item timestamps are naive (no timezone info)
        items = [
            {"timestamp": "2024-01-01T14:00:00", "data": "naive_newer"},
            {"timestamp": "2024-01-01T10:00:00", "data": "naive_older"},
        ]

        result = filter_by_timestamp(items, aware_cutoff)

        assert len(result) == 1
        assert result[0]["data"] == "naive_newer"

    def test_both_aware_same_timezone(self):
        """Both cutoff and timestamps aware with same timezone works."""
        aware_cutoff = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        items = [
            {"timestamp": "2024-01-01T14:00:00+00:00", "data": "newer"},
            {"timestamp": "2024-01-01T10:00:00+00:00", "data": "older"},
        ]

        result = filter_by_timestamp(items, aware_cutoff)

        assert len(result) == 1
        assert result[0]["data"] == "newer"

    def test_item_exactly_equal_to_cutoff_excluded(self):
        """Items exactly equal to cutoff are excluded (uses > not >=)."""
        cutoff = datetime(2024, 1, 1, 12, 0, 0)
        items = [
            {"timestamp": "2024-01-01T12:00:00", "data": "exact"},
            {"timestamp": "2024-01-01T12:00:01", "data": "just_after"},
        ]

        result = filter_by_timestamp(items, cutoff)

        assert len(result) == 1
        assert result[0]["data"] == "just_after"


class TestUtcDateRangeStr:
    """Tests for utc_date_range_str function."""

    def test_returns_tuple(self):
        """Returns a tuple of two strings."""
        start, end = utc_date_range_str(1)
        assert isinstance(start, str)
        assert isinstance(end, str)


class TestStableIdFromOai:
    """Tests for stable_id_from_oai function."""

    def test_extracts_id(self):
        """Extracts the stable ID from OAI identifier."""
        result = stable_id_from_oai("oai:chinaxiv.org:2024-12345")
        assert result == "2024-12345"

    def test_handles_simple_format(self):
        """Handles simple format without prefix."""
        result = stable_id_from_oai("2024-12345")
        assert result == "2024-12345"
