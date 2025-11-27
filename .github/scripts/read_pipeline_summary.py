#!/usr/bin/env python3
"""Read a field from the pipeline summary JSON.

Usage: python read_pipeline_summary.py [field_name]

Arguments:
    field_name: Field to extract (default: "successes")
                Common fields: successes, qa_passed, failures, skipped

Returns:
    - Value of the field if found
    - 0 on any error (file not found, invalid JSON, missing field)
"""
import json
import pathlib
import sys


def main() -> None:
    field = sys.argv[1] if len(sys.argv) > 1 else "successes"
    path = pathlib.Path("reports/pipeline_summary.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(data.get(field, 0))
    except Exception:
        print(0)


if __name__ == "__main__":
    main()
