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
- Check workflow status: `gh run list --workflow=daily-pipeline.yml --limit 5`
- B2 manifests in `indexes/validated/manifest-*.csv`

## GitHub Token Management

### Architecture: Separate API Token from Git Operations

Git operations and API calls use **different tokens** to avoid workflow scope conflicts:

- **Git operations** (push, pull): Use `gh` CLI keyring (has workflow scope)
- **API calls** (`src/gh_actions.py`): Use token from `.env.github` file

This separation prevents the recurring "refusing to allow OAuth App to create or update workflow" error.

### File Structure

| File | Purpose | Loaded By |
|------|---------|-----------|
| `.env.github` | GH_TOKEN for API calls | `src/gh_actions.py` |
| `.env` | Other secrets (no GH_TOKEN) | Shell, Python dotenv |
| `.envrc` | Auto-unsets GH_TOKEN on repo entry | direnv |
| Keyring | Git authentication | `gh` CLI, git push |

### Layered Defense

This repo uses three layers to prevent GH_TOKEN from breaking git push:

1. **`.envrc`** - direnv auto-unsets GH_TOKEN when entering the repo
2. **Pre-push hook** - blocks push if GH_TOKEN is in environment
3. **Separate files** - `.env.github` for API calls, `.env` for everything else

### Setup (one-time)

1. **Ensure keyring has workflow scope:**
   ```bash
   gh auth refresh -s workflow
   gh auth status  # Should show: Token scopes include 'workflow'
   ```

2. **Create `.env.github` if missing:**
   ```bash
   echo "GH_TOKEN=$(gh auth token)" > .env.github
   echo "GH_REPO=domus-magna/chinaxiv-english" >> .env.github
   ```

3. **Verify `.env` does NOT have GH_TOKEN:**
   ```bash
   grep "^GH_TOKEN=" .env  # Should return nothing
   ```

4. **(Optional) Enable direnv:**
   ```bash
   brew install direnv
   echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc  # or ~/.bashrc
   direnv allow .
   ```

### If Push Fails with Workflow Scope Error

```bash
# Step 1: Unset any GH_TOKEN in your shell
unset GH_TOKEN

# Step 2: Refresh your keyring (if needed)
gh auth refresh -s workflow

# Step 3: Retry push
git push
```

The pre-push hook validates keyring has workflow scope before pushing workflow files.

### Verify Setup

```bash
# Check keyring has workflow scope
gh auth status
# Should show: Token scopes: 'gist', 'read:org', 'repo', 'workflow'

# Check GH_TOKEN is NOT in shell environment
echo $GH_TOKEN  # Should be empty

# Check .env.github exists with GH_TOKEN
cat .env.github | grep GH_TOKEN  # Should show the token
```

## CRITICAL: Backblaze B2 Persistence is MANDATORY

```
+==============================================================================+
|  ALL PIPELINE DATA MUST BE PERSISTED TO B2 - NO EPHEMERAL OPTIONS            |
|                                                                              |
|  GitHub Actions runners are ephemeral. If data is not uploaded to B2,        |
|  it is LOST when the job ends. This is a HARD requirement.                   |
+==============================================================================+
```

### What MUST be persisted to B2

| Data Type | B2 Path | Workflow Step |
|-----------|---------|---------------|
| Downloaded PDFs | `pdfs/{paper_id}.pdf` | After download, before translation |
| Validated translations | `validated/translations/{paper_id}.json` | After QA pass |
| Flagged translations | `flagged/translations/{paper_id}.json` | After QA flag |
| Translated figures | `figures/{paper_id}/` | After figure translation |
| Selection files | `selections/daily/{date}.json` | After selection |
| Records | `records/chinaxiv_{month}.json` | After harvest |

### Workflow Requirements

**EVERY workflow that produces output MUST:**
1. Have B2 credentials as required secrets (not optional)
2. Upload outputs to B2 BEFORE the job ends
3. **FAIL** (not skip) if B2 upload fails
4. **FAIL** (not continue) if B2 credentials are missing

**There are NO ephemeral options:**
- No `--skip-persist` flags
- No "local only" modes for production workflows
- No silent skips when B2 credentials are missing
- PR builds are the ONLY exception (secrets are withheld by GitHub)

### Why This Matters

1. **GitHub runners are ephemeral** - all local data is destroyed after job ends
2. **PDFs cost money to download** - re-downloading wastes BrightData credits
3. **Translations cost money** - re-translating wastes OpenRouter credits
4. **Retries must resume** - without B2 persistence, retries start from scratch

### Verifying B2 Persistence

**Quick status check:**
```bash
python scripts/b2_status.py
```

**Manual checks:**
```bash
# Check if PDFs are in B2
aws s3 ls s3://${BACKBLAZE_BUCKET}/${BACKBLAZE_PREFIX}pdfs/ --endpoint-url ${BACKBLAZE_S3_ENDPOINT}

# Check if translations are in B2
aws s3 ls s3://${BACKBLAZE_BUCKET}/${BACKBLAZE_PREFIX}validated/translations/ --endpoint-url ${BACKBLAZE_S3_ENDPOINT}

# Count files for a specific month
aws s3 ls s3://${BACKBLAZE_BUCKET}/${BACKBLAZE_PREFIX}pdfs/chinaxiv-202401 --endpoint-url ${BACKBLAZE_S3_ENDPOINT} | wc -l
```

## B2 Storage Map

**Bucket:** `chinaxiv` (Backblaze B2)
**Endpoint:** `https://s3.us-west-004.backblazeb2.com`

### Directory Structure

```
s3://chinaxiv/
├── pdfs/                              # Source PDFs (934+ files)
│   └── chinaxiv-YYYYMM.NNNNN.pdf
│
├── validated/translations/            # QA-passed text translations (3,872+ files)
│   └── chinaxiv-YYYYMM.NNNNN.json
│
├── flagged/translations/              # QA-failed translations (need review)
│   └── chinaxiv-YYYYMM.NNNNN.json
│
├── figures/                           # Translated figures (currently EMPTY)
│   └── chinaxiv-YYYYMM.NNNNN/
│       ├── original/
│       │   └── fig_N.png
│       └── translated/
│           └── fig_N.png
│
├── records/                           # Harvested metadata
│   └── chinaxiv_YYYYMM.json
│
├── selections/                        # Daily selection files
│   └── daily/
│       └── YYYY-MM-DD.json
│
└── indexes/                           # Manifests and indices
    └── validated/
        └── manifest-YYYY-MM-DD.csv
```

### What Each Directory Contains

| Path | Contents | Created By |
|------|----------|------------|
| `pdfs/` | Original Chinese PDFs | `harvest_chinaxiv_optimized.py` |
| `validated/translations/` | QA-passed JSON (text only for now) | `batch_translate.yml` workflow |
| `flagged/translations/` | Failed QA, needs manual review | `batch_translate.yml` workflow |
| `figures/` | **EMPTY** - figure pipeline hasn't run | `figure-backfill.yml` (pending) |
| `records/` | Harvested paper metadata | `daily-pipeline.yml` harvest |
| `selections/` | Papers selected for translation | Pipeline selection step |

### Status Summary (as of Dec 2025)

| Data | Count | Status |
|------|-------|--------|
| Text translations | 3,872+ | ✅ Working |
| PDFs | In progress | ✅ PDF backfill running |
| Figures | In progress | ✅ CS/AI filter implemented |
| Records | ~50 months | ✅ Working |

**To check current status:** `python scripts/b2_status.py`
