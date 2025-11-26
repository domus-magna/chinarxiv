"""
Cost tracking for ChinaXiv English translation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .file_service import ensure_dir, read_json, write_json


def compute_cost(
    model: str, in_tokens: int, out_tokens: int, cfg: Dict[str, Any]
) -> float:
    """
    Compute translation cost based on token usage.

    Args:
        model: Model name
        in_tokens: Input tokens
        out_tokens: Output tokens
        cfg: Configuration dictionary

    Returns:
        Cost in USD
    """
    table = ((cfg or {}).get("cost", {}) or {}).get("pricing_per_mtoken", {})
    prices = table.get(model)
    if not prices:
        return 0.0

    cost = (in_tokens / 1_000_000.0) * float(prices.get("input", 0)) + (
        out_tokens / 1_000_000.0
    ) * float(prices.get("output", 0))
    return round(cost, 8)


def append_cost_log(
    item_id: str,
    model: str,
    in_tokens: int,
    out_tokens: int,
    cost: float,
    when_iso: Optional[str] = None,
) -> str:
    """
    Append cost log entry to daily cost file.

    Args:
        item_id: Item identifier
        model: Model name
        in_tokens: Input tokens
        out_tokens: Output tokens
        cost: Cost in USD
        when_iso: ISO timestamp (defaults to now)

    Returns:
        Path to cost log file
    """
    day = (datetime.now(timezone.utc).date()).isoformat()
    path = os.path.join("data", "costs", f"{day}.json")
    ensure_dir(os.path.dirname(path))

    payload = {
        "time": when_iso or now_iso(),
        "id": item_id,
        "model": model,
        "in_tokens": in_tokens,
        "out_tokens": out_tokens,
        "cost_estimate_usd": cost,
    }

    items: List[dict] = []
    if os.path.exists(path):
        try:
            items = read_json(path)
        except (json.JSONDecodeError, OSError):
            items = []

    items.append(payload)
    write_json(path, items)
    return path


def now_iso() -> str:
    """Get current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()
