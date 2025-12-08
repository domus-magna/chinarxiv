#!/usr/bin/env python3
"""
Aggregate figure translation requests by paper ID.

Reads figure translation requests from data/figure_requests.jsonl or Cloudflare KV
and provides statistics on the most requested papers.

Usage:
    # Read from local JSONL file
    python scripts/aggregate_figure_requests.py
    python scripts/aggregate_figure_requests.py --days 30  # Last 30 days only
    python scripts/aggregate_figure_requests.py --top 50   # Top 50 papers

    # Read from Cloudflare KV (requires CF_ACCOUNT_ID, CF_API_TOKEN, CF_KV_NAMESPACE_ID env vars)
    python scripts/aggregate_figure_requests.py --kv --days 30
    python scripts/aggregate_figure_requests.py --kv --export-jsonl data/kv_cache.jsonl
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
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


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


def aggregate_from_kv(
    account_id: str,
    api_token: str,
    namespace_id: str,
    days: int | None = None
) -> tuple[Counter, list[dict]]:
    """
    Aggregate requests from Cloudflare KV.

    Args:
        account_id: Cloudflare account ID
        api_token: Cloudflare API token with KV read scope
        namespace_id: KV namespace ID (from wrangler.toml)
        days: If specified, only count requests from last N days

    Returns:
        Tuple of (Counter of paper_id -> request count, list of all request records)
    """
    base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/storage/kv/namespaces/{namespace_id}"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }

    # Determine date range for prefix filtering
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        # Generate list of date prefixes to query
        date_prefixes = []
        current = cutoff.date()
        today = datetime.now(timezone.utc).date()
        while current <= today:
            date_prefixes.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)
    else:
        # Query all keys (no prefix filter)
        date_prefixes = [None]

    all_requests = []

    for prefix in date_prefixes:
        # List keys with pagination
        cursor = None
        key_pattern = f"requests:{prefix}:" if prefix else "requests:"

        while True:
            # List keys matching pattern
            params = {"prefix": key_pattern, "limit": 1000}
            if cursor:
                params["cursor"] = cursor

            list_response = requests.get(
                f"{base_url}/keys",
                headers=headers,
                params=params
            )

            if not list_response.ok:
                print(f"Error listing KV keys: {list_response.text}", file=sys.stderr)
                break

            list_data = list_response.json()
            if not list_data.get("success"):
                print(f"KV API error: {list_data.get('errors')}", file=sys.stderr)
                break

            keys = list_data["result"]

            # Fetch value for each key
            for key_obj in keys:
                key_name = key_obj["name"]

                # Get value
                value_response = requests.get(
                    f"{base_url}/values/{key_name}",
                    headers=headers
                )

                if not value_response.ok:
                    print(f"Warning: Could not fetch key {key_name}: {value_response.text}", file=sys.stderr)
                    continue

                try:
                    entry = value_response.json()
                    all_requests.append(entry)
                except json.JSONDecodeError as e:
                    print(f"Warning: Invalid JSON in key {key_name}: {e}", file=sys.stderr)
                    continue

            # Check for more pages
            cursor = list_data["result_info"].get("cursor")
            if not cursor:
                break

    print(f"Loaded {len(all_requests)} total requests from KV")

    # Apply date filter if requested (keys are already filtered by prefix, but we double-check timestamps)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        original_count = len(all_requests)
        filtered = []
        skipped = 0

        for r in all_requests:
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

        all_requests = filtered
        print(f"Filtered to {len(all_requests)} requests from last {days} days (removed {original_count - len(all_requests)}, skipped {skipped} malformed)")

    # Count by paper (with field validation from earlier fix)
    paper_counts = Counter(
        r['paper_id'] for r in all_requests
        if 'paper_id' in r and r['paper_id']
    )
    return paper_counts, all_requests


def main():
    parser = argparse.ArgumentParser(
        description='Aggregate figure translation requests from JSONL file or Cloudflare KV'
    )

    # Source selection
    source_group = parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument(
        '--input',
        type=Path,
        help='Path to JSONL file (default: data/figure_requests.jsonl)'
    )
    source_group.add_argument(
        '--kv',
        action='store_true',
        help='Read from Cloudflare KV (requires CF_ACCOUNT_ID, CF_API_TOKEN, CF_KV_NAMESPACE_ID env vars)'
    )

    # Common parameters
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
    parser.add_argument(
        '--export-jsonl',
        type=Path,
        default=None,
        help='Export aggregated data to JSONL file (useful for caching KV data)'
    )

    args = parser.parse_args()

    # Determine source and aggregate requests
    all_requests = []  # For export functionality

    if args.kv:
        # Read from KV
        account_id = os.environ.get('CF_ACCOUNT_ID')
        api_token = os.environ.get('CF_API_TOKEN')
        namespace_id = os.environ.get('CF_KV_NAMESPACE_ID')

        if not all([account_id, api_token, namespace_id]):
            print("Error: --kv requires CF_ACCOUNT_ID, CF_API_TOKEN, and CF_KV_NAMESPACE_ID environment variables", file=sys.stderr)
            return 1

        print("Reading from Cloudflare KV...")
        paper_counts, all_requests = aggregate_from_kv(account_id, api_token, namespace_id, args.days)

        # Export to JSONL for caching if requested
        if args.export_jsonl:
            args.export_jsonl.parent.mkdir(parents=True, exist_ok=True)
            with open(args.export_jsonl, 'w', encoding='utf-8') as f:
                for entry in all_requests:
                    f.write(json.dumps(entry) + '\n')
            print(f"Exported {len(all_requests)} requests to {args.export_jsonl}")
    else:
        # Read from local JSONL file
        input_file = args.input or Path('data/figure_requests.jsonl')
        print(f"Reading from local file: {input_file}")
        paper_counts = aggregate_from_jsonl(input_file, args.days)

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
