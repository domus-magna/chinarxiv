#!/usr/bin/env bash
# Complete Paper Processor - fetch, translate, and upload papers end-to-end

set -euo pipefail

if [ "$#" -eq 0 ]; then
  cat >&2 <<EOF
Complete Paper Processor - fetch, translate, and upload papers end-to-end

Usage: $0 [options]

Options:
  --paper-id ID           Process a single paper (e.g., 202411.00001)
  --paper-ids-file PATH   Process papers from file (one ID per line)
  --limit N               Process at most N papers
  --no-text               Skip text translation
  --no-figures            Skip figure pipeline
  --no-upload             Skip B2 upload
  --force                 Re-process even if already in B2
  --dry-run               Skip API calls where possible
  --continue-on-error     Continue processing after failures
  --workdir DIR           Base working directory (default: .)

Examples:
  $0 --paper-id 202411.00001
  $0 --paper-ids-file papers.txt --limit 10 --no-figures
  $0 --paper-id 202411.00001 --force --no-upload
EOF
  exit 1
fi

python -m src.complete_paper_processor "$@"
