#!/usr/bin/env bash
# Thin wrapper around the complete paper processor CLI.

set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 --paper-id <id> [--paper-ids-file path] [--limit N] [--no-figures]" >&2
  exit 1
fi

python -m src.complete_paper_processor "$@"
