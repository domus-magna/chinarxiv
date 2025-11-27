#!/usr/bin/env bash
#
# Orchestrates PDF re-download + translation refresh + manual QA prep.
# Runs download_missing_pdfs.py (optionally limited to specific months),
# then reruns src.pipeline with QA enabled, and finally exports flagged
# translations for reviewers via scripts/qa_translations.py.

set -euo pipefail

MONTHS=""
DOWNLOAD_LIMIT=0
WORKERS=20
PIPELINE_LIMIT=0
QA_DIR="data/manual_qa"
DRY_RUN=false
PY_BIN=${PY_BIN:-python3}

usage() {
  cat <<'EOF'
Usage: scripts/run_pdf_reprocess.sh [options]

Options:
  --months YYYYMM,YYYYMM   Restrict PDF download retries to these months
  --download-limit N       Stop after N successful PDF downloads (default: unlimited)
  --workers N              Pipeline worker count (default: 20)
  --pipeline-limit N       Limit translation pipeline to first N selections (default: all)
  --qa-dir PATH            Directory for manual QA exports (default: data/manual_qa)
  --dry-run                Print the commands without executing them
  -h, --help               Show this help text

Environment requirements:
  OPENROUTER_API_KEY            Translation API key (required)
  HTTP(S)_PROXY                 Bright Data ISP proxy URL (recommended for PDF fetches)
  BRIGHTDATA_BROWSER_WSS        Bright Data remote browser endpoint (optional headless fallback)
  BRIGHTDATA_UNLOCKER_ZONE/PASSWORD  Unlocker credentials for API fallback (optional but recommended)

The script does not mutate selections; make sure data/selected.json reflects the
papers you want to reprocess before running.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --months)
      MONTHS="${2:-}"
      shift 2
      ;;
    --download-limit)
      DOWNLOAD_LIMIT="${2:-0}"
      shift 2
      ;;
    --workers)
      WORKERS="${2:-20}"
      shift 2
      ;;
    --pipeline-limit)
      PIPELINE_LIMIT="${2:-0}"
      shift 2
      ;;
    --qa-dir)
      QA_DIR="${2:-data/manual_qa}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift 1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: $name is not set" >&2
    exit 1
  fi
}

require_env OPENROUTER_API_KEY

if [[ -z "${HTTP_PROXY:-}" || -z "${HTTPS_PROXY:-}" ]]; then
  echo "⚠️  HTTP(S)_PROXY is not set. Downloads will run without the ISP proxy."
fi

if [[ -z "${BRIGHTDATA_BROWSER_WSS:-}" ]]; then
  echo "⚠️  BRIGHTDATA_BROWSER_WSS is not set. Headless fallback will be skipped."
fi

DOWNLOAD_CMD=("$PY_BIN" scripts/download_missing_pdfs.py)
if [[ -n "$MONTHS" ]]; then
  DOWNLOAD_CMD+=(--months "$MONTHS")
fi
if [[ "$DOWNLOAD_LIMIT" -gt 0 ]]; then
  DOWNLOAD_CMD+=(--limit "$DOWNLOAD_LIMIT")
fi

PIPELINE_CMD=("$PY_BIN" -m src.pipeline --skip-selection --with-qa --workers "$WORKERS")
if [[ "$PIPELINE_LIMIT" -gt 0 ]]; then
  PIPELINE_CMD+=(--limit "$PIPELINE_LIMIT")
fi

QA_CMD=("$PY_BIN" scripts/qa_translations.py --translated-dir data/translated --flagged-dir "$QA_DIR")

echo "📥 Download command: ${DOWNLOAD_CMD[*]}"
echo "🧠 Pipeline command: ${PIPELINE_CMD[*]}"
echo "📝 Manual QA export: ${QA_CMD[*]}"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry run enabled; exiting without executing commands."
  exit 0
fi

echo "==> Downloading missing PDFs..."
"${DOWNLOAD_CMD[@]}"

echo "==> Running translation pipeline with QA..."
"${PIPELINE_CMD[@]}"

echo "==> Exporting flagged translations for manual QA..."
mkdir -p "$QA_DIR"
"${QA_CMD[@]}"

echo "✅ PDF reprocess + QA prep complete."
echo "Flagged translations for review are located in: $QA_DIR"
