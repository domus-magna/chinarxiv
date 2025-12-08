#!/usr/bin/env python3
"""
Aggregate figure translation requests by paper ID.

Reads figure translation requests from data/figure_requests.jsonl (or Cloudflare KV)
and provides statistics on the most requested papers.

Usage:
    python scripts/aggregate_figure_requests.py
    python scripts/aggregate_figure_requests.py --days 30  # Last 30 days only
    python scripts/aggregate_figure_requests.py --top 50   # Top 50 papers
"""
# TODO(v2, after server-side logging moves to a Durable Object): add a KV/R2 fetch path that
# reads per-day batches the DO flushes, with graceful backoff on missing days and pagination to
# avoid pulling giant blobs into memory.
# TODO(v3, when requests exceed a few hundred/day): consume a Queue- or DO-produced R2 manifest
# (e.g., daily JSONL/Parquet) instead of raw KV; emit rollup summaries and basic anomaly alerts
# (spikes, parse errors) so downstream prioritization scripts stay reliable.
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def aggregate_from_jsonl(filepath: Path, days: int | None = None) -> Counter:
    """
    Aggregate requests from JSONL file.

    Args:
        filepath: Path to JSONL file
        days: If specified, only count requests from last N days

    Returns:
        Counter of paper_id -> request count
    """
    if not filepath.exists():
        print(f"Warning: {filepath} does not exist")
        return Counter()

    requests = []
    with open(filepath, encoding='utf-8') as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                requests.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Warning: Line {line_num} is not valid JSON: {e}", file=sys.stderr)
                continue

    print(f"Loaded {len(requests)} total requests from {filepath}")

    # Filter by date if requested
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        original_count = len(requests)
        filtered = []
        skipped = 0

        for r in requests:
            try:
                if 'timestamp' not in r or 'paper_id' not in r:
                    print(f"Warning: Record missing required field: {r}", file=sys.stderr)
                    skipped += 1
                    continue

                ts = datetime.fromisoformat(r['timestamp'].replace('Z', '+00:00'))
                if ts > cutoff:
                    filtered.append(r)
            except (ValueError, TypeError, AttributeError) as e:
                print(f"Warning: Invalid record: {r} - {e}", file=sys.stderr)
                skipped += 1
                continue

        requests = filtered
        print(f"Filtered to {len(requests)} requests from last {days} days (removed {original_count - len(requests)}, skipped {skipped} malformed)")

    # Count by paper
    paper_counts = Counter(
        r['paper_id'] for r in requests
        if 'paper_id' in r and r['paper_id']
    )
    return paper_counts


def main():
    parser = argparse.ArgumentParser(
        description='Aggregate figure translation requests'
    )
    parser.add_argument(
        '--input',
        type=Path,
        default=Path('data/figure_requests.jsonl'),
        help='Path to JSONL file (default: data/figure_requests.jsonl)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=None,
        help='Only count requests from last N days (default: all time)'
    )
    parser.add_argument(
        '--top',
        type=int,
        default=20,
        help='Number of top papers to show (default: 20)'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='Write paper IDs to file (one per line, ordered by request count)'
    )

    args = parser.parse_args()

    # Aggregate requests
    paper_counts = aggregate_from_jsonl(args.input, args.days)

    if not paper_counts:
        print("\nNo requests found.")
        return 0

    # Display statistics
    time_period = f"Last {args.days} Days" if args.days else "All Time"
    print(f"\n{'=' * 70}")
    print(f"Figure Translation Requests - {time_period}")
    print(f"{'=' * 70}")
    print(f"\nTotal unique papers requested: {len(paper_counts)}")
    print(f"Total requests: {sum(paper_counts.values())}")
    print(f"\nTop {args.top} Most Requested Papers:")
    print(f"{'-' * 70}")
    print(f"{'Rank':<6} {'Paper ID':<30} {'Requests':<10}")
    print(f"{'-' * 70}")

    for rank, (paper_id, count) in enumerate(paper_counts.most_common(args.top), start=1):
        print(f"{rank:<6} {paper_id:<30} {count:<10}")

    print(f"{'-' * 70}")

    # Write output file if requested
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, 'w') as f:
            for paper_id, _ in paper_counts.most_common():
                f.write(f"{paper_id}\n")
        print(f"\nWrote {len(paper_counts)} paper IDs to {args.output}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
