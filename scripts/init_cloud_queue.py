#!/usr/bin/env python3
"""
Initialize cloud job queue for batch translation.

Merges Internet Archive and ChinaXiv datasets, marks already-translated
papers as completed, and creates the queue file for GitHub Actions workflows.
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path
from typing import List, Set

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cloud_job_queue import cloud_queue


def load_ia_papers() -> List[str]:
    """Load Internet Archive paper IDs."""
    ia_files = sorted(glob.glob("data/records/ia_*.json"))

    if not ia_files:
        print("No IA records found")
        return []

    # Use the largest/most recent IA file
    ia_file = max(ia_files, key=os.path.getsize)
    print(f"Loading IA papers from: {ia_file}")

    with open(ia_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        paper_ids = [p["id"] for p in data if p.get("id")]
        print(f"  Found {len(paper_ids)} IA papers")
        return paper_ids

    return []


def load_chinaxiv_papers() -> List[str]:
    """Load ChinaXiv paper IDs from monthly harvest files."""
    chinaxiv_files = sorted(glob.glob("data/records/chinaxiv_*.json"))

    if not chinaxiv_files:
        print("No ChinaXiv records found")
        return []

    print(f"Loading ChinaXiv papers from {len(chinaxiv_files)} files...")

    all_ids = []
    for file in chinaxiv_files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                ids = [p["id"] for p in data if p.get("id")]
                all_ids.extend(ids)
        except Exception as e:
            print(f"  Warning: Failed to load {file}: {e}")

    print(f"  Found {len(all_ids)} ChinaXiv papers")
    return all_ids


def get_translated_papers() -> Set[str]:
    """Get set of already-translated paper IDs."""
    translated_dir = Path("data/translated")

    if not translated_dir.exists():
        return set()

    translated_files = list(translated_dir.glob("*.json"))
    translated_ids = {f.stem for f in translated_files}

    print(f"Found {len(translated_ids)} already-translated papers")
    return translated_ids


def merge_and_deduplicate(ia_ids: List[str], chinaxiv_ids: List[str]) -> List[str]:
    """Merge and deduplicate paper IDs, prioritizing ChinaXiv."""
    # Use set for deduplication
    all_ids = set()

    # Add ChinaXiv first (higher priority)
    all_ids.update(chinaxiv_ids)

    # Add IA papers
    all_ids.update(ia_ids)

    # Sort for consistent ordering
    merged = sorted(list(all_ids))

    print("\nMerged dataset:")
    print(f"  IA papers: {len(ia_ids)}")
    print(f"  ChinaXiv papers: {len(chinaxiv_ids)}")
    print(f"  Total unique: {len(merged)}")

    return merged


def initialize_queue(
    all_paper_ids: List[str],
    translated_ids: Set[str],
    force: bool = False,
) -> None:
    """
    Initialize cloud job queue.

    Args:
        all_paper_ids: All paper IDs to process
        translated_ids: Already-translated paper IDs
        force: If True, re-initialize even if queue exists
    """
    queue_file = Path("data/cloud_jobs.json")

    if queue_file.exists() and not force:
        print(f"\nQueue file already exists: {queue_file}")
        print("Use --force to re-initialize")
        return

    print(f"\nInitializing queue with {len(all_paper_ids)} papers...")

    # Separate pending and completed
    pending = [pid for pid in all_paper_ids if pid not in translated_ids]
    completed = [pid for pid in all_paper_ids if pid in translated_ids]

    print(f"  Pending: {len(pending)}")
    print(f"  Already completed: {len(completed)}")

    # Add pending jobs
    added = cloud_queue.add_jobs(pending)
    print(f"  Added {added} pending jobs")

    # Mark completed jobs
    if completed:
        # Add them as completed

        for pid in completed:
            cloud_queue.add_jobs([pid])
            cloud_queue.complete_job(pid, qa_passed=True)

        print(f"  Marked {len(completed)} as completed")

    print(f"\nQueue initialized: {queue_file}")
    print("Run 'python -m src.cloud_job_queue stats' to view queue status")


def main():
    parser = argparse.ArgumentParser(
        description="Initialize cloud job queue for batch translation"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-initialization even if queue exists",
    )
    parser.add_argument(
        "--ia-only",
        action="store_true",
        help="Only include Internet Archive papers",
    )
    parser.add_argument(
        "--chinaxiv-only",
        action="store_true",
        help="Only include ChinaXiv papers",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit total papers (for testing)",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("CLOUD QUEUE INITIALIZATION")
    print("=" * 60)

    # Load datasets
    ia_ids = [] if args.chinaxiv_only else load_ia_papers()
    chinaxiv_ids = [] if args.ia_only else load_chinaxiv_papers()

    if not ia_ids and not chinaxiv_ids:
        print("\nError: No papers found in data/records/")
        print("Please run harvest first or check data/records/ directory")
        sys.exit(1)

    # Merge and deduplicate
    all_ids = merge_and_deduplicate(ia_ids, chinaxiv_ids)

    # Apply limit if specified
    if args.limit:
        all_ids = all_ids[:args.limit]
        print(f"\nLimited to {len(all_ids)} papers (--limit {args.limit})")

    # Get translated papers
    translated_ids = get_translated_papers()

    # Initialize queue
    initialize_queue(all_ids, translated_ids, force=args.force)

    print("\n" + "=" * 60)
    print("NEXT STEPS")
    print("=" * 60)
    print("1. Commit the queue file:")
    print("   git add data/cloud_jobs.json")
    print("   git commit -m 'feat: initialize cloud job queue'")
    print("   git push")
    print()
    print("2. Run a test batch (10 papers):")
    print("   python -m src.pipeline --cloud-mode --limit 10 --with-qa")
    print()
    print("3. Start the orchestrator workflow:")
    print("   gh workflow run batch-queue-orchestrator.yml")
    print()


if __name__ == "__main__":
    main()
