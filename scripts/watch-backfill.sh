#!/usr/bin/env bash
# Backfill Watcher Script
# Polls text backfill jobs and auto-triggers figure-backfill on completion
#
# Usage: caffeinate -i ./scripts/watch-backfill.sh 2>&1 | tee /tmp/backfill-watcher.log

set -euo pipefail

# Configuration
MONTHS="202504 202505 202506 202507 202508 202509"
STATE_FILE="/tmp/backfill-figures.state"
POLL_INTERVAL=600  # 10 minutes
MAX_RETRIES=3
RETRY_DELAY=30

# Logging helper
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# Retry wrapper for gh commands
gh_retry() {
    local attempt=1
    while [ $attempt -le $MAX_RETRIES ]; do
        if output=$("$@" 2>&1); then
            echo "$output"
            return 0
        fi
        log "WARN: gh command failed (attempt $attempt/$MAX_RETRIES): $*"
        log "      Error: $output"
        if [ $attempt -lt $MAX_RETRIES ]; then
            sleep $RETRY_DELAY
        fi
        ((attempt++))
    done
    log "ERROR: gh command failed after $MAX_RETRIES attempts: $*"
    return 1
}

# Initialize state file if not exists
touch "$STATE_FILE"

log "Starting backfill watcher"
log "Watching months: $MONTHS"
log "State file: $STATE_FILE"
log "Poll interval: ${POLL_INTERVAL}s"

# Main polling loop
while true; do
    log "--- Polling cycle start ---"

    # Get all backfill runs
    RUNS_JSON=$(gh_retry gh run list --workflow backfill.yml --limit 20 \
        --json databaseId,displayTitle,status,conclusion) || {
        log "ERROR: Failed to fetch run list, will retry next cycle"
        sleep $POLL_INTERVAL
        continue
    }

    all_done=true

    for month in $MONTHS; do
        # Skip if already processed
        if grep -q "^${month}$" "$STATE_FILE" 2>/dev/null; then
            log "SKIP: $month already triggered (in state file)"
            continue
        fi

        all_done=false

        # Find run for this month (displayTitle contains "backfill-NNNNNN")
        run_info=$(echo "$RUNS_JSON" | jq -r --arg m "$month" '
            .[] | select(.displayTitle | contains("backfill-" + $m)) |
            "\(.status)|\(.conclusion)|\(.databaseId)"
        ' | head -1)

        if [ -z "$run_info" ]; then
            log "WAIT: $month - no matching run found yet"
            continue
        fi

        status=$(echo "$run_info" | cut -d'|' -f1)
        conclusion=$(echo "$run_info" | cut -d'|' -f2)
        run_id=$(echo "$run_info" | cut -d'|' -f3)

        log "CHECK: $month - status=$status, conclusion=$conclusion, run_id=$run_id"

        if [ "$status" != "completed" ]; then
            log "WAIT: $month - still in progress"
            continue
        fi

        if [ "$conclusion" = "success" ]; then
            log "SUCCESS: $month text backfill completed successfully"
            log "TRIGGER: Starting figure-backfill for $month..."

            if gh_retry gh workflow run figure-backfill.yml -f month="$month"; then
                log "TRIGGERED: figure-backfill for $month"
                echo "$month" >> "$STATE_FILE"
            else
                log "ERROR: Failed to trigger figure-backfill for $month"
            fi
        else
            log "FAILED: $month text backfill concluded with: $conclusion"
            log "SKIP: Not triggering figure-backfill for failed month"
            # Record as processed to avoid re-checking
            echo "$month" >> "$STATE_FILE"
        fi
    done

    if $all_done; then
        log "All months processed! Exiting watcher."
        break
    fi

    log "--- Sleeping ${POLL_INTERVAL}s until next poll ---"
    sleep $POLL_INTERVAL
done

log "Watcher complete. Final state:"
cat "$STATE_FILE"
