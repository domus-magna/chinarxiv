#!/bin/bash
# AI-Agent Self-Review Hook Wrapper
# Runs automated code analysis instead of arbitrary waits
#
# For AI agents: This performs actual checks and returns actionable findings.
# Exit 0 = all checks pass, Exit 1 = issues found (fix before proceeding).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Run the Python analysis script
python3 "$SCRIPT_DIR/self_review.py" "$@"
