#!/usr/bin/env python3
"""
Reformat existing translations using the improved LLM formatter.

This script processes all existing translated papers in data/translated/
and applies the improved formatting with better spacing and readability.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.formatting_service import FormattingService
from src.config import get_config
from src.logging_utils import log


def load_translation(file_path: str) -> Dict[str, Any]:
    """Load a translation file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_translation(file_path: str, translation: Dict[str, Any]) -> None:
    """Save a translation file."""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(translation, f, ensure_ascii=False, indent=2)


def reformat_translation(file_path: str, formatting_service: FormattingService) -> bool:
    """Reformat a single translation file."""
    try:
        # Load existing translation
        translation = load_translation(file_path)

        # Check if it already has formatting fields
        if "abstract_md" in translation and "body_md" in translation:
            log(f"Skipping {file_path} - already formatted")
            return True

        # Apply improved formatting
        log(f"Reformatting {file_path}...")
        formatted = formatting_service.format_translation(translation, dry_run=False)

        # Save back to file
        save_translation(file_path, formatted)
        log(f"✅ Reformatted {file_path}")
        return True

    except Exception as e:
        log(f"❌ Failed to reformat {file_path}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Reformat existing translations")
    parser.add_argument("--limit", type=int, help="Limit number of papers to process")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument("--ids", nargs="+", help="Specific paper IDs to process")
    args = parser.parse_args()

    # Get configuration
    config = get_config()

    # Initialize formatting service
    formatting_service = FormattingService(config)

    # Get list of translation files
    translated_dir = Path("data/translated")
    if not translated_dir.exists():
        log("❌ No translated directory found")
        return 1

    # Find all JSON files
    json_files = list(translated_dir.glob("*.json"))
    log(f"Found {len(json_files)} translation files")

    # Filter by specific IDs if provided
    if args.ids:
        json_files = [f for f in json_files if f.stem in args.ids]
        log(f"Filtered to {len(json_files)} files matching IDs: {args.ids}")

    # Apply limit if specified
    if args.limit:
        json_files = json_files[: args.limit]
        log(f"Limited to {len(json_files)} files")

    if args.dry_run:
        log("DRY RUN MODE - No changes will be made")
        for file_path in json_files:
            translation = load_translation(str(file_path))
            has_formatting = "abstract_md" in translation and "body_md" in translation
            status = (
                "✅ Already formatted" if has_formatting else "🔄 Needs reformatting"
            )
            log(f"{status}: {file_path.name}")
        return 0

    # Process files
    success_count = 0
    total_count = len(json_files)

    log(f"🚀 Starting reformatting of {total_count} papers...")

    for i, file_path in enumerate(json_files, 1):
        log(f"[{i}/{total_count}] Processing {file_path.name}")

        if reformat_translation(str(file_path), formatting_service):
            success_count += 1

        # Progress update every 10 files
        if i % 10 == 0:
            log(f"Progress: {i}/{total_count} ({i/total_count*100:.1f}%)")

    log("✅ Reformatting complete!")
    log(f"Successfully reformatted: {success_count}/{total_count} papers")

    if success_count < total_count:
        log(f"⚠️ {total_count - success_count} papers failed to reformat")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
