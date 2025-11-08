#!/usr/bin/env python3
"""
Monitor for ChinaXiv harvest progress.
Similar to batch_translate status/watch.
"""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict


def get_harvest_stats() -> Dict:
    """Get harvest progress statistics."""
    stats = {
        "total_months": 7,  # April-Oct 2025
        "months_completed": 0,
        "papers_scraped": 0,
        "current_month": None,
        "status": "unknown",
    }

    # Check completed months
    records_dir = Path("data/records")
    if not records_dir.exists():
        return stats

    completed_files = list(records_dir.glob("chinaxiv_2025*.json"))
    stats["months_completed"] = len(completed_files)

    # Count total papers
    for file in completed_files:
        try:
            with open(file) as f:
                data = json.load(f)
                stats["papers_scraped"] += len(data)
        except Exception:
            pass

    # Check if still running
    pid_file = Path("data/harvest.pid")
    if pid_file.exists():
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            # Check if process is running
            os.kill(pid, 0)
            stats["status"] = "running"
        except (OSError, ValueError):
            stats["status"] = "stopped"
            pid_file.unlink()
    else:
        if stats["months_completed"] == stats["total_months"]:
            stats["status"] = "completed"
        else:
            stats["status"] = "stopped"

    # Determine current month
    months = ["202504", "202505", "202506", "202507", "202508", "202509", "202510"]
    for month in months:
        if not (records_dir / f"chinaxiv_{month}.json").exists():
            stats["current_month"] = month
            break

    return stats


def show_status():
    """Show harvest status."""
    stats = get_harvest_stats()

    print()
    print("=" * 60)
    print("CHINAXIV HARVEST STATUS")
    print("=" * 60)
    print(f"Status:         {stats['status'].upper()}")
    print(
        f"Months:         {stats['months_completed']}/{stats['total_months']} completed"
    )
    print(f"Papers Scraped: {stats['papers_scraped']:,}")

    if stats["current_month"]:
        print(f"Current Month:  {stats['current_month']}")

    print()

    # Show completed months
    records_dir = Path("data/records")
    if records_dir.exists():
        completed_files = sorted(records_dir.glob("chinaxiv_2025*.json"))
        if completed_files:
            print("Completed Months:")
            for file in completed_files:
                month = file.stem.replace("chinaxiv_", "")
                try:
                    with open(file) as f:
                        data = json.load(f)
                        print(f"  {month}: {len(data):,} papers")
                except Exception:
                    print(f"  {month}: error reading file")
            print()

    print("=" * 60)
    print()


def watch_status():
    """Watch status with live updates."""
    print("Watching harvest status (Ctrl+C to stop)...\n")

    try:
        while True:
            os.system("clear" if os.name != "nt" else "cls")
            show_status()
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopped watching")


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Monitor ChinaXiv harvest")
    parser.add_argument("command", choices=["status", "watch"], help="Command to run")
    args = parser.parse_args()

    if args.command == "status":
        show_status()
    elif args.command == "watch":
        watch_status()


if __name__ == "__main__":
    run_cli()
