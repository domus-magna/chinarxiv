# ChinaRxiv Translation Pipeline

## CRITICAL: Full Pipeline = Text + Figures

**MANDATORY**: When running ANY translation job (backfill, single paper, batch):
1. **ALWAYS translate BOTH text AND figures** - this is NOT optional
2. If figure pipeline fails, the whole job fails - do not continue text-only
3. Never assume "text-only first, figures later" without EXPLICIT user approval

## Quick Reference

### Full Translation (text + figures)
```bash
# Local (preferred for debugging)
python -m src.pipeline --workers 20 --with-qa --with-figures

# Cloud via GitHub Actions
gh workflow run backfill.yml -f month=202401 -f with_figures=true
```

### Figure-Only Pass (for papers already text-translated)
```bash
python -m src.figure_pipeline --start 202401 --end 202412
```

## Architecture Overview

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  1. HARVEST      │ ──► │  2. TRANSLATE    │ ──► │  3. PUBLISH      │
│  (Download PDFs) │     │  Text + Figures  │     │  (B2 + Site)     │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

### Text Translation
- **Location**: `src/translate.py`, `src/pipeline.py`
- **API**: OpenRouter (DeepSeek V3.2-Exp model)
- **QA**: Chinese leakage check (<0.5%), math preservation

### Figure Translation
- **Location**: `src/figure_pipeline/`
- **Extract**: PyMuPDF extracts images from PDF
- **Translate**: Gemini 3 Pro generates new image with English text
- **QA**: Moondream validates Chinese removed, English present
- **Storage**: B2 for original + translated images

## Environment Variables Required

```bash
# Text Translation
OPENROUTER_API_KEY=xxx

# Figure Translation
GEMINI_API_KEY=xxx          # Google API for figure translation
MOONDREAM_API_KEY=xxx       # Figure QA validation

# Storage
BACKBLAZE_KEY_ID=xxx
BACKBLAZE_APPLICATION_KEY=xxx
BACKBLAZE_S3_ENDPOINT=xxx
BACKBLAZE_BUCKET=xxx

# Harvesting
BRIGHTDATA_API_KEY=xxx
BRIGHTDATA_ZONE=xxx
```

## Fail-Fast Error Handling

The pipeline uses circuit breakers to stop immediately on billing/auth errors:
- `402 Payment Required` → Stop immediately
- `401 Unauthorized` → Stop immediately
- `429 + quota message` → Stop immediately
- Transient errors (5xx, network) → Retry with backoff, then stop after 5 failures

## Running a Backfill

### Step 1: Harvest Papers
```bash
python -m src.harvest_chinaxiv_optimized --start YYYYMM --end YYYYMM
python scripts/download_missing_pdfs.py
```

### Step 2: Translate (TEXT + FIGURES)
```bash
# ALWAYS include --with-figures
python -m src.pipeline --workers 20 --with-qa --with-figures --start YYYYMM --end YYYYMM
```

### Step 3: Publish & Deploy
```bash
python -m src.tools.b2_publish
python scripts/hydrate_from_b2.py
python -m src.render
wrangler pages deploy site --project-name chinarxiv
```

## Common Mistakes to Avoid

1. **Running text-only translation** - NEVER do this unless user explicitly approves
2. **Assuming figures can be added later** - While technically possible, it's extra work
3. **Ignoring circuit breaker errors** - These indicate billing issues, stop and check
4. **Not checking for GEMINI_API_KEY** - Figure translation requires Google API access

## Monitoring

- Discord alerts for circuit breaker trips
- Check `python -m src.batch_translate status` for progress
- B2 manifests in `indexes/validated/manifest-*.csv`

## GitHub Token Management

**IMPORTANT**: The `.env` file contains `GH_TOKEN` which overrides `gh` CLI keyring authentication.

### Problem
When you run `gh auth refresh -s workflow`, it updates the token in macOS keyring, but `.env` still has the OLD token without `workflow` scope. This causes "refusing to allow OAuth App to create or update workflow" errors when pushing workflow files.

### Solution
After refreshing GitHub auth, sync the token to `.env`:
```bash
# Refresh with workflow scope (updates keyring)
gh auth refresh -s workflow

# Sync keyring token to .env
./scripts/sync-gh-token.sh
```

### One-liner alternative:
```bash
# Get fresh token and update .env directly
NEW_TOKEN=$(unset GH_TOKEN && gh auth token) && \
sed -i.bak "s|^GH_TOKEN=.*|GH_TOKEN=$NEW_TOKEN|" .env && \
rm .env.bak && echo "Updated GH_TOKEN in .env"
```

### Verify token has correct scopes:
```bash
unset GH_TOKEN && gh auth status
# Should show: Token scopes: 'gist', 'read:org', 'repo', 'workflow'
```

### Automated Protection
A **pre-push git hook** is installed at `.git/hooks/pre-push` that:
1. Detects if you're pushing workflow files (`.github/workflows/*`)
2. Checks if your `.env` GH_TOKEN matches the keyring token
3. Warns and offers to auto-sync if tokens are stale
4. Blocks push if token doesn't have `workflow` scope
