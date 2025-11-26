"""Tests for data_utils module."""

from datetime import datetime, timedelta

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
