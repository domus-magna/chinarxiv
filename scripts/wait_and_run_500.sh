#!/usr/bin/env bash
set -euo pipefail
REC_DIR="data/records"
LOG="data/wait_and_run_500.log"
mkdir -p "$REC_DIR"
months=(202511 202509)

# Wait for harvest outputs
for m in "${months[@]}"; do
  echo "[$(date -u +%FT%TZ)] waiting for $REC_DIR/chinaxiv_${m}.json" | tee -a "$LOG"
  for i in $(seq 1 720); do # up to ~6 hours polling every 30s
    if [ -f "$REC_DIR/chinaxiv_${m}.json" ]; then
      echo "[$(date -u +%FT%TZ)] found $REC_DIR/chinaxiv_${m}.json" | tee -a "$LOG"
      break
    fi
    sleep 30
  done
  if [ ! -f "$REC_DIR/chinaxiv_${m}.json" ]; then
    echo "[$(date -u +%FT%TZ)] timeout waiting for $REC_DIR/chinaxiv_${m}.json" | tee -a "$LOG"
    exit 1
  fi
  echo "[$(date -u +%FT%TZ)] size: $(jq -r 'length' "$REC_DIR/chinaxiv_${m}.json" 2>/dev/null || echo n/a)" | tee -a "$LOG"
done

# Run the 500 batch
export TRANSLATION_SEGMENTED_FALLBACK=1
CMD=(python -m src.pipeline --records "data/records/chinaxiv_202510.json,data/records/chinaxiv_202511.json,data/records/chinaxiv_202509.json" --limit 500 --with-qa --workers 12)
 echo "[$(date -u +%FT%TZ)] starting: ${CMD[*]}" | tee -a "$LOG"
nohup "${CMD[@]}" > data/batch500.log 2>&1 & echo $! > data/batch500.pid
 echo "[$(date -u +%FT%TZ)] started PID $(cat data/batch500.pid)" | tee -a "$LOG"
