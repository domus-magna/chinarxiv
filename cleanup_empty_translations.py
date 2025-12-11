#!/usr/bin/env python3
"""
Script to find and delete empty translation files in the data/translated directory.
Empty translation files are those where:
- title_en is an empty string ""
- abstract_en is an empty string ""
- body_en is either an empty array [] or null
"""

import json
import os
from pathlib import Path

def is_empty_translation(file_path):
    """Check if a translation file contains only empty translations."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Check if all translation fields are empty
        title_empty = data.get('title_en') == ""
        abstract_empty = data.get('abstract_en') == ""
        body_empty = data.get('body_en') in [[], None]

        return title_empty and abstract_empty and body_empty

    except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
        print(f"Error processing {file_path}: {e}")
        return False

def find_empty_translation_files():
    """Find all empty translation files in the data/translated directory."""
    translated_dir = Path("data/translated")
    empty_files = []

    if not translated_dir.exists():
        print(f"Directory {translated_dir} does not exist")
        return empty_files

    # Find all JSON files in the translated directory
    json_files = translated_dir.glob("*.json")

    for file_path in json_files:
        if is_empty_translation(file_path):
            empty_files.append(file_path)

    return empty_files

def delete_empty_files(file_paths):
    """Delete the specified files."""
    deleted_count = 0
    for file_path in file_paths:
        try:
            os.remove(file_path)
            print(f"Deleted: {file_path}")
            deleted_count += 1
        except OSError as e:
            print(f"Error deleting {file_path}: {e}")

    return deleted_count

def main():
    print("Searching for empty translation files...")

    empty_files = find_empty_translation_files()
    total_count = len(empty_files)

    if total_count == 0:
        print("No empty translation files found.")
        return

    print(f"Found {total_count} empty translation files.")

    # Ask for confirmation before deleting (skip if non-interactive)
    try:
        confirm = input(f"Are you sure you want to delete {total_count} files? (y/N): ")
        if confirm.lower() != 'y':
            print("Operation cancelled.")
            return
    except EOFError:
        # Non-interactive mode, proceed with deletion
        print("Running in non-interactive mode, proceeding with deletion...")
        pass

    print("Deleting empty translation files...")
    deleted_count = delete_empty_files(empty_files)

    print(f"Successfully deleted {deleted_count} out of {total_count} files.")

    if deleted_count == total_count:
        print("All empty translation files have been removed.")
    else:
        print(f"Warning: {total_count - deleted_count} files could not be deleted.")

if __name__ == "__main__":
    main()
