#!/usr/bin/env python3
"""
Prune macro chunk caches older than N days to keep disk usage low.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path


def prune_cache(days: int, cache_dir: Path = Path("data/cache/macro_chunks")) -> int:
    if not cache_dir.exists():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for path in cache_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except Exception:
            continue
    # Remove empty parent dirs
    if removed:
        try:
            if not any(cache_dir.iterdir()):
                cache_dir.rmdir()
        except Exception:
            pass
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune macro chunk caches")
    parser.add_argument("--days", type=int, default=7, help="Remove cache files older than N days (default: 7)")
    args = parser.parse_args()
    removed = prune_cache(args.days)
    print(f"Removed {removed} cache files older than {args.days} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
