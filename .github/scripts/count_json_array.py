#!/usr/bin/env python3
"""Count items in a JSON array file.

Usage: python count_json_array.py [path]

Returns:
    - Number of items if file contains a JSON array
    - -1 on any error (file not found, invalid JSON, not an array)
"""
import json
import pathlib
import sys


def main() -> None:
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("data/selected.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(len(data) if isinstance(data, list) else -1)
    except Exception:
        print(-1)


if __name__ == "__main__":
    main()
