#!/usr/bin/env python3
"""
Simple maintenance script for ChinaXiv Translations.
"""

import argparse
import json
import gzip
from datetime import datetime, timedelta
from pathlib import Path


def log(message):
    """Simple logging function."""
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[{timestamp}] {message}")


def cleanup_json_file(file_path: Path, days: int = 7, max_entries: int = 1000):
    """Clean up old entries from a JSON file."""
    if not file_path.exists():
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            entries = json.load(f)

        cutoff = datetime.now() - timedelta(days=days)
        filtered = []

        for entry in entries:
            try:
                entry_time = datetime.fromisoformat(entry.get("timestamp", ""))
                if entry_time > cutoff:
                    filtered.append(entry)
            except ValueError:
                # Keep entries with invalid timestamps
                filtered.append(entry)

        # Keep only recent entries
        if len(filtered) > max_entries:
            filtered = filtered[-max_entries:]

        if len(filtered) != len(entries):
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(filtered, f, indent=2, ensure_ascii=False)
            log(f"Cleaned up {len(entries) - len(filtered)} old entries from {file_path.name}")

    except Exception as e:
        log(f"Failed to cleanup {file_path.name}: {e}")


def cleanup_alerts():
    """Clean up old alerts."""
    log("Cleaning up old alerts...")
    alerts_file = Path("data/alerts.json")
    cleanup_json_file(alerts_file, days=7, max_entries=1000)
    log("Alert cleanup completed")


def cleanup_analytics():
    """Clean up old analytics data."""
    log("Cleaning up old analytics data...")
    analytics_dir = Path("data/analytics")
    if analytics_dir.exists():
        for file_path in analytics_dir.glob("*.json"):
            cleanup_json_file(file_path, days=90, max_entries=10000)
    log("Analytics cleanup completed")


def cleanup_performance():
    """Clean up old performance metrics."""
    log("Cleaning up old performance metrics...")
    performance_dir = Path("data/performance")
    if performance_dir.exists():
        for file_path in performance_dir.glob("*.json"):
            cleanup_json_file(file_path, days=30, max_entries=5000)
    log("Performance cleanup completed")


def optimize_search_index():
    """Optimize search index file."""
    log("Optimizing search index...")
    index_file = Path("site/search-index.json")

    if not index_file.exists():
        log("Search index file not found")
        return

    try:
        # Check if already compressed
        compressed_file = index_file.with_suffix(index_file.suffix + ".gz")
        if compressed_file.exists():
            log("Search index already compressed")
            return

        # Read original file
        with open(index_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Compress
        with gzip.open(compressed_file, "wt", encoding="utf-8") as f:
            f.write(content)

        # Compare sizes
        original_size = index_file.stat().st_size
        compressed_size = compressed_file.stat().st_size
        compression_ratio = (1 - compressed_size / original_size) * 100

        log(f"Search index compressed by {compression_ratio:.1f}% ({original_size} → {compressed_size} bytes)")

    except Exception as e:
        log(f"Failed to optimize search index: {e}")


def generate_stats():
    """Generate basic system statistics."""
    log("Generating system statistics...")

    stats = {
        "generated_at": datetime.now().isoformat(),
        "data_files": {},
        "site_files": {}
    }

    # Check data files
    data_dir = Path("data")
    if data_dir.exists():
        for file_path in data_dir.rglob("*.json"):
            try:
                size = file_path.stat().st_size
                stats["data_files"][str(file_path)] = size
            except OSError:
                pass

    # Check site files
    site_dir = Path("site")
    if site_dir.exists():
        for file_path in site_dir.rglob("*"):
            if file_path.is_file():
                try:
                    size = file_path.stat().st_size
                    stats["site_files"][str(file_path)] = size
                except OSError:
                    pass

    # Save stats
    stats_file = Path("data/system_stats.json")
    stats_file.parent.mkdir(exist_ok=True)
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    log(f"System statistics saved to {stats_file}")
    log(f"Data files: {len(stats['data_files'])}")
    log(f"Site files: {len(stats['site_files'])}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="ChinaXiv Translations Simple Maintenance")
    parser.add_argument("--cleanup", action="store_true", help="Clean up old data")
    parser.add_argument("--optimize", action="store_true", help="Run performance optimizations")
    parser.add_argument("--stats", action="store_true", help="Generate system statistics")
    parser.add_argument("--all", action="store_true", help="Run all maintenance tasks")

    args = parser.parse_args()

    if not any([args.cleanup, args.optimize, args.stats, args.all]):
        parser.print_help()
        return

    log("Starting maintenance tasks...")

    if args.all or args.cleanup:
        cleanup_alerts()
        cleanup_analytics()
        cleanup_performance()

    if args.all or args.optimize:
        optimize_search_index()

    if args.all or args.stats:
        generate_stats()

    log("Maintenance completed successfully")


if __name__ == "__main__":
    main()
