"""
Backfill script to detect figures in existing translations.

This script:
1. Scans all translated JSON files in data/translated/
2. Detects figures/tables in body_en and body_md fields
3. Updates JSON with _figures, _has_figures, _figure_count
4. Generates reports/figure_manifest.json

Uses the detect_figures function from body_extract.py for consistent
pattern matching across the codebase.
"""

from __future__ import annotations

import argparse
import glob
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .utils import ensure_dir, log, read_json, write_json
from .body_extract import detect_figures


def process_translation(path: str, dry_run: bool = False) -> Optional[Dict[str, Any]]:
    """
    Process a single translation file to detect figures.

    Uses detect_figures from body_extract.py for consistent pattern matching.

    Args:
        path: Path to translation JSON file
        dry_run: If True, don't write changes to disk

    Returns:
        Figure info dict if figures found, None otherwise
    """
    try:
        data = read_json(path)
    except Exception as e:
        log(f"Failed to read {path}: {e}")
        return None

    paper_id = data.get('id', os.path.basename(path).replace('.json', ''))

    # Collect figures from all text sources
    all_figures: List[Dict[str, Any]] = []
    seen: set = set()

    # Check body_md first (markdown format, usually cleaner)
    body_md = data.get('body_md')
    if body_md and isinstance(body_md, str):
        # Split into paragraphs for detect_figures
        md_paragraphs = [p.strip() for p in body_md.split('\n') if p.strip()]
        for fig in detect_figures(md_paragraphs):
            key = (fig['type'], fig['number'])
            if key not in seen:
                seen.add(key)
                fig['source'] = 'md'
                all_figures.append(fig)

    # Check body_en (list of paragraphs)
    body_en = data.get('body_en')
    if body_en and isinstance(body_en, list):
        for fig in detect_figures(body_en):
            key = (fig['type'], fig['number'])
            if key not in seen:
                seen.add(key)
                fig['source'] = 'en'
                all_figures.append(fig)

    # Also check body_zh for Chinese-only figures
    body_zh = data.get('body_zh')
    if body_zh and isinstance(body_zh, list):
        for fig in detect_figures(body_zh):
            key = (fig['type'], fig['number'])
            if key not in seen:
                seen.add(key)
                fig['source'] = 'zh'
                all_figures.append(fig)

    # Sort by type then number
    import re
    def sort_key(f):
        num_str = f['number'] or '0'
        numeric_match = re.match(r'([A-Za-z]?)(\d+)', num_str)
        if numeric_match:
            prefix = numeric_match.group(1) or ''
            num = int(numeric_match.group(2))
            return (0 if f['type'] == 'figure' else 1, prefix, num)
        return (0 if f['type'] == 'figure' else 1, '', 0)

    figures = sorted(all_figures, key=sort_key)

    # Count by type
    figure_count = sum(1 for f in figures if f['type'] == 'figure')
    table_count = sum(1 for f in figures if f['type'] == 'table')

    # Update the translation data
    data['_figures'] = figures
    data['_has_figures'] = len(figures) > 0
    data['_figure_count'] = figure_count
    data['_table_count'] = table_count

    # Write back unless dry run
    if not dry_run and figures:
        write_json(path, data)

    if figures:
        return {
            'id': paper_id,
            'figure_count': figure_count,
            'table_count': table_count,
            'figures': figures,
            'path': path,
        }

    return None


def run_backfill(
    data_dir: str = 'data/translated',
    output_path: str = 'reports/figure_manifest.json',
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Run figure detection on all translated papers.

    Args:
        data_dir: Directory containing translated JSON files
        output_path: Path for the figure manifest output
        dry_run: If True, don't modify files
        verbose: If True, print detailed progress

    Returns:
        Summary statistics
    """
    pattern = os.path.join(data_dir, '*.json')
    files = sorted(glob.glob(pattern))

    log(f"Scanning {len(files)} translation files for figures...")

    papers_with_figures: List[Dict[str, Any]] = []
    total_figures = 0
    total_tables = 0
    processed = 0
    errors = 0

    for path in files:
        try:
            result = process_translation(path, dry_run=dry_run)
            if result:
                papers_with_figures.append(result)
                total_figures += result['figure_count']
                total_tables += result['table_count']
                if verbose:
                    log(f"  {result['id']}: {result['figure_count']} figures, {result['table_count']} tables")
            processed += 1
        except Exception as e:
            log(f"Error processing {path}: {e}")
            errors += 1

    # Sort by figure count (most figures first)
    papers_with_figures.sort(key=lambda p: -(p['figure_count'] + p['table_count']))

    # Generate manifest
    manifest = {
        'generated': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'total_papers_scanned': len(files),
        'total_papers_with_figures': len(papers_with_figures),
        'total_figures': total_figures,
        'total_tables': total_tables,
        'papers': papers_with_figures,
    }

    # Write manifest
    if not dry_run:
        ensure_dir(os.path.dirname(output_path))
        write_json(output_path, manifest)
        log(f"Wrote figure manifest to {output_path}")

    # Summary
    log("\n=== Figure Detection Summary ===")
    log(f"Papers scanned: {len(files)}")
    log(f"Papers with figures: {len(papers_with_figures)}")
    log(f"Total figures detected: {total_figures}")
    log(f"Total tables detected: {total_tables}")
    if errors:
        log(f"Errors: {errors}")

    return {
        'papers_scanned': len(files),
        'papers_with_figures': len(papers_with_figures),
        'total_figures': total_figures,
        'total_tables': total_tables,
        'errors': errors,
    }


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Backfill figure detection for existing translations.'
    )
    parser.add_argument(
        '--data-dir',
        default='data/translated',
        help='Directory containing translated JSON files',
    )
    parser.add_argument(
        '--output',
        default='reports/figure_manifest.json',
        help='Output path for figure manifest',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Scan without modifying files',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Print detailed progress',
    )

    args = parser.parse_args()

    run_backfill(
        data_dir=args.data_dir,
        output_path=args.output,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


if __name__ == '__main__':
    main()
