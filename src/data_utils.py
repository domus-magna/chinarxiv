"""
Data processing utilities for ChinaXiv English translation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple


def utc_date_range_str(days_back: int = 1) -> Tuple[str, str]:
    """
    Get UTC date range string.

    Args:
        days_back: Number of days back from today

    Returns:
        Tuple of (start_date, end_date) in ISO format
    """
    # Yesterday UTC by default
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days_back)).date()
    end = start
    return start.isoformat(), end.isoformat()


def stable_id_from_oai(oai_identifier: str) -> str:
    """
    Extract stable ID from OAI identifier.

    Args:
        oai_identifier: OAI identifier (e.g., oai:chinaxiv.org:YYYY-XXXXX)

    Returns:
        Stable ID (e.g., YYYY-XXXXX)
    """
    # e.g., oai:chinaxiv.org:YYYY-XXXXX -> YYYY-XXXXX
    return oai_identifier.split(":")[-1]


def has_full_body_content(data: Dict[str, Any]) -> bool:
    """
    Determine whether a translation dict contains usable full-text content.

    Prefers explicit _has_full_body metadata when present, but gracefully
    falls back to inspecting body_en for legacy translations.
    """
    if "_has_full_body" in data:
        return bool(data.get("_has_full_body"))

    body_en = data.get("body_en")
    if isinstance(body_en, list):
        return any((para or "").strip() for para in body_en)
    return False


def filter_by_timestamp(
    items: List[Dict[str, Any]],
    cutoff: datetime,
    timestamp_key: str = "timestamp",
    keep_invalid: bool = False,
) -> List[Dict[str, Any]]:
    """
    Filter list of dicts, keeping items newer than cutoff.

    Args:
        items: List of dictionaries with timestamp fields
        cutoff: Datetime cutoff - items older than this are filtered out
        timestamp_key: Key name for the timestamp field (default: "timestamp")
        keep_invalid: If True, keep items with invalid/missing timestamps (default: False)

    Returns:
        Filtered list of items with timestamps after the cutoff

    Note:
        Handles timezone-aware and naive datetime comparison by normalizing
        both to the same type. If cutoff is naive, timezone info is stripped
        from item timestamps. If cutoff is aware, naive item timestamps are
        assumed to be in the same timezone as cutoff.
    """
    result = []
    for item in items:
        try:
            item_time = datetime.fromisoformat(item.get(timestamp_key, ""))
            # Normalize timezone awareness to match cutoff
            if cutoff.tzinfo is None:
                # Naive cutoff - strip timezone from item_time if present
                if item_time.tzinfo is not None:
                    item_time = item_time.replace(tzinfo=None)
            else:
                # Aware cutoff - assume same timezone for naive item_time
                if item_time.tzinfo is None:
                    item_time = item_time.replace(tzinfo=cutoff.tzinfo)
            if item_time > cutoff:
                result.append(item)
        except (ValueError, TypeError):
            if keep_invalid:
                result.append(item)
    return result
