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


# English keywords (lowercase for matching)
ENGLISH_KEYWORDS = [
    'machine learning', 'deep learning', 'neural network', 'neural net',
    'artificial intelligence', 'computer vision', 'nlp',
    'natural language processing', 'transformer', 'attention mechanism',
    'large language model', 'llm', 'gpt', 'bert', 'diffusion model',
    'generative model', 'generative ai', 'reinforcement learning',
    'knowledge graph', 'embedding', 'classification', 'segmentation',
    'pretrained', 'pre-trained', 'fine-tuning', 'finetuning',
    'convolutional', 'recurrent', 'lstm', 'rnn', 'cnn',
    'object detection', 'image recognition', 'speech recognition',
    'text generation', 'language model', 'chatbot', 'recommendation system',
    'graph neural', 'autoencoder', 'variational', 'adversarial',
    'bayesian neural', 'physics-informed neural',
]

# Chinese keywords
CHINESE_KEYWORDS = [
    '机器学习', '深度学习', '神经网络', '人工智能',
    '大语言模型', '大模型', '预训练', '计算机视觉',
    '自然语言处理', '知识图谱', '强化学习', '卷积',
    '生成对抗', '图神经网络', '注意力机制', '目标检测',
    '图像识别', '语义分割', '文本生成', '语音识别',
    '推荐系统', '循环神经', '长短期记忆',
]

# Subject patterns (partial match)
SUBJECT_PATTERNS = [
    '计算机', '信息科学', '信息技术',
    'computer', 'computing', 'informatics',
]


def is_cs_ai_paper(paper: Dict) -> Tuple[bool, Optional[str]]:
    """
    Check if a paper is CS/AI related.

    Args:
        paper: Paper dict with title, abstract, subjects fields

    Returns:
        Tuple of (is_cs_ai, matched_keyword)
    """
    # Get text fields
    title = (paper.get('title') or paper.get('title_en') or '').lower()
    abstract = (paper.get('abstract') or paper.get('abstract_en') or '').lower()
    subjects = paper.get('subjects') or paper.get('subjects_en') or []
    if isinstance(subjects, str):
        subjects = [subjects]
    subjects_text = ' '.join(str(s).lower() for s in subjects)

    # Also check original Chinese fields if present
    title_zh = paper.get('title_zh') or paper.get('title') or ''
    abstract_zh = paper.get('abstract_zh') or paper.get('abstract') or ''

    # Combined text for English keyword search
    text_en = f"{title} {abstract} {subjects_text}"

    # Combined text for Chinese keyword search
    text_zh = f"{title_zh} {abstract_zh}"

    # Check subject patterns first (fastest)
    for pattern in SUBJECT_PATTERNS:
        if pattern.lower() in subjects_text:
            return True, f"subject:{pattern}"

    # Check English keywords
    for kw in ENGLISH_KEYWORDS:
        if kw in text_en:
            return True, f"en:{kw}"

    # Check Chinese keywords
    for kw in CHINESE_KEYWORDS:
        if kw in text_zh:
            return True, f"zh:{kw}"

    return False, None


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
