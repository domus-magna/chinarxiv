#!/usr/bin/env bash
set -euo pipefail

# Run translation for a list of paper IDs with logging for monitoring.
# Usage:
#   scripts/run_backfill_batch.sh chinaxiv-202510.00016 chinaxiv-202510.00017 ...
#   or provide a file: scripts/run_backfill_batch.sh $(cat ids.txt)

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <paper_id> [paper_id...]" >&2
  exit 1
fi

log_dir="reports/backfill_runs"
mkdir -p "$log_dir"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="$log_dir/backfill_$ts.log"

echo "Logging to $log_file"

for id in "$@"; do
  echo "=== Translating $id ===" | tee -a "$log_file"
  if PYTHONUNBUFFERED=1 python -m src.translate "$id" 2>&1 | tee -a "$log_file"; then
    echo "OK $id" | tee -a "$log_file"
  else
    echo "FAIL $id (see log)" | tee -a "$log_file"
    exit 1
  fi
done

echo "All done. Logs: $log_file"
