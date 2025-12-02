#!/usr/bin/env python3
"""
Filter papers to CS/AI subset based on keywords in title, abstract, and subjects.

This script identifies computer science, AI, and machine learning papers from
validated translations in B2 or local storage.

Usage:
    # Filter from local translated files
    python scripts/filter_cs_ai_papers.py --input data/translated --output data/cs_ai_paper_ids.txt

    # Filter specific month
    python scripts/filter_cs_ai_papers.py --input data/translated --month 202510

    # Show stats without writing output
    python scripts/filter_cs_ai_papers.py --input data/translated --dry-run

    # Upload filter list to B2 after generating
    python scripts/filter_cs_ai_papers.py --input data/translated --output data/cs_ai_paper_ids.txt --upload-b2
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import from canonical location in src/data_utils.py
from src.data_utils import is_cs_ai_paper


def filter_papers(
    input_dir: Path,
    month: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """
    Filter papers to CS/AI subset.

    Handles two formats:
    1. Individual paper files: chinaxiv-YYYYMM.NNNNN.json (from data/translated/)
    2. Monthly record files: chinaxiv_YYYYMM.json (from data/records/)

    Args:
        input_dir: Directory containing translated JSON files
        month: Optional month filter (YYYYMM format)

    Returns:
        List of (paper_id, matched_keyword) tuples
    """
    results = []
    total_papers = 0

    # Try individual paper files first (data/translated/ format)
    pattern = f"chinaxiv-{month}*.json" if month else "chinaxiv-*.json"
    individual_files = sorted(input_dir.glob(pattern))

    if individual_files:
        print(f"Found {len(individual_files)} individual paper files")
        for filepath in individual_files:
            try:
                with open(filepath, encoding='utf-8') as f:
                    paper = json.load(f)

                paper_id = filepath.stem  # e.g., chinaxiv-202510.00001
                total_papers += 1
                is_cs_ai, keyword = is_cs_ai_paper(paper)

                if is_cs_ai:
                    results.append((paper_id, keyword))

            except Exception as e:
                print(f"Warning: Error reading {filepath}: {e}")
                continue
    else:
        # Try monthly record files (data/records/ format)
        pattern = f"chinaxiv_{month}.json" if month else "chinaxiv_*.json"
        monthly_files = sorted(input_dir.glob(pattern))

        if monthly_files:
            print(f"Found {len(monthly_files)} monthly record files")
            for filepath in monthly_files:
                # Skip merged files
                if '_merged' in filepath.name:
                    continue

                try:
                    with open(filepath, encoding='utf-8') as f:
                        papers = json.load(f)

                    if not isinstance(papers, list):
                        print(f"Warning: {filepath} is not a list, skipping")
                        continue

                    for paper in papers:
                        paper_id = paper.get('id', '')
                        if not paper_id:
                            continue

                        total_papers += 1
                        is_cs_ai, keyword = is_cs_ai_paper(paper)

                        if is_cs_ai:
                            results.append((paper_id, keyword))

                except Exception as e:
                    print(f"Warning: Error reading {filepath}: {e}")
                    continue
        else:
            print(f"No files found matching patterns in {input_dir}")

    print(f"Scanned {total_papers} papers total")
    return results


def upload_to_b2(filepath: Path) -> bool:
    """Upload filter list to B2."""
    try:
        import boto3

        endpoint = os.environ.get('BACKBLAZE_S3_ENDPOINT')
        bucket = os.environ.get('BACKBLAZE_BUCKET', 'chinaxiv')
        key_id = os.environ.get('BACKBLAZE_KEY_ID')
        app_key = os.environ.get('BACKBLAZE_APPLICATION_KEY')

        if not all([endpoint, key_id, app_key]):
            print("Warning: B2 credentials not configured, skipping upload")
            return False

        s3 = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=app_key,
        )

        dest_key = 'selections/cs_ai_paper_ids.txt'
        s3.upload_file(str(filepath), bucket, dest_key)
        print(f"Uploaded to s3://{bucket}/{dest_key}")
        return True

    except Exception as e:
        print(f"Warning: B2 upload failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Filter papers to CS/AI subset')
    parser.add_argument('--input', type=Path, required=True,
                        help='Directory containing translated JSON files')
    parser.add_argument('--output', type=Path, default=None,
                        help='Output file for paper IDs (one per line)')
    parser.add_argument('--month', type=str, default=None,
                        help='Filter to specific month (YYYYMM)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show stats without writing output')
    parser.add_argument('--upload-b2', action='store_true',
                        help='Upload filter list to B2 after generating')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show matched papers and keywords')

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: Input directory {args.input} does not exist")
        sys.exit(1)

    # Filter papers
    results = filter_papers(args.input, args.month)

    # Show stats
    print(f"\n=== CS/AI Filter Results ===")
    print(f"Total files scanned: {len(list(args.input.glob('chinaxiv-*.json')))}")
    print(f"CS/AI papers found: {len(results)}")

    if results:
        # Count by match type
        match_types = {}
        for _, keyword in results:
            match_type = keyword.split(':')[0] if keyword else 'unknown'
            match_types[match_type] = match_types.get(match_type, 0) + 1

        print(f"\nMatch breakdown:")
        for mtype, count in sorted(match_types.items(), key=lambda x: -x[1]):
            print(f"  {mtype}: {count}")

        if args.verbose:
            print(f"\nMatched papers:")
            for paper_id, keyword in results[:50]:  # Show first 50
                print(f"  {paper_id}: {keyword}")
            if len(results) > 50:
                print(f"  ... and {len(results) - 50} more")

    # Write output
    if args.output and not args.dry_run:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, 'w') as f:
            for paper_id, _ in results:
                f.write(f"{paper_id}\n")
        print(f"\nWrote {len(results)} paper IDs to {args.output}")

        # Upload to B2 if requested
        if args.upload_b2:
            upload_to_b2(args.output)

    return 0


if __name__ == '__main__':
    sys.exit(main())
