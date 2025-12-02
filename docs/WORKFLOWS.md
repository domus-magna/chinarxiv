# GitHub Actions Workflows

This guide describes all 25 workflows in the repo, grouped by purpose with quick expectations, required secrets, and example invocations. Defaults favor the simplest reliable path: B2 is always the source of truth, PDFs are mandatory before translation, and figure translation runs where enabled.

## Required Secrets (by usage)

- **Translation**: `OPENROUTER_API_KEY`
- **Figures**: `GEMINI_API_KEY`, `MOONDREAM_API_KEY`
- **Storage (B2)**: `BACKBLAZE_KEY_ID`, `BACKBLAZE_APPLICATION_KEY`, `BACKBLAZE_S3_ENDPOINT`, `BACKBLAZE_BUCKET`, optional `BACKBLAZE_PREFIX`
- **Harvesting / PDF fetch**: `BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE`, optional `BRIGHTDATA_UNLOCKER_ZONE`, `BRIGHTDATA_UNLOCKER_PASSWORD`
- **Deploy / alerts**: `CF_API_TOKEN` (Cloudflare Pages), `DISCORD_WEBHOOK_URL`
- **ChatOps / review**: `CLAUDE_CODE_OAUTH_TOKEN` (for `claude*.yml`)

## Production Pipelines

### Daily Pipeline (`.github/workflows/daily-pipeline.yml`)
- **Trigger**: 03:00 UTC schedule + manual; `skip_harvest` input to run render-only.
- **Purpose**: End-to-end prod run (harvest current+previous month, select, download PDFs, translate text+figures on non-PR, publish to B2, hydrate from B2, render/search/PDF, deploy to Cloudflare).
- **What to expect**: 25–45 minutes. Fails early if B2 or OpenRouter keys are missing. PRs skip secrets, figures, and B2 persistence.
- **Inputs**: `skip_harvest` (bool).
- **Example**: `gh workflow run daily-pipeline.yml -f skip_harvest=false`.

### Month Backfill (`.github/workflows/backfill.yml`)
- **Trigger**: Manual.
- **Purpose**: Full harvest→select→PDF download→translate→publish→render for one `YYYYMM`.
- **What to expect**: 30–90 minutes depending on month size. Requires BrightData + B2 secrets. Deploy toggled by `deploy`.
- **Inputs**: `month` (required), `workers` (default 20), `deploy` (default true), `no_latest` (skip latest pointer).
- **Example**: `gh workflow run backfill.yml -f month=202510 -f workers=30 -f deploy=true`.

### Rebuild from B2 (`.github/workflows/rebuild-from-b2.yml`)
- **Trigger**: Manual (optional schedule commented out).
- **Purpose**: Hydrate validated translations from B2, render/search/PDF, deploy; no harvest/translate.
- **What to expect**: ~10–20 minutes; fails if B2 has zero translations.
- **Example**: `gh workflow run rebuild-from-b2.yml`.

## Figure & PDF Workflows

### PDF Backfill (`.github/workflows/pdf-backfill.yml`)
- **Trigger**: Manual.
- **Purpose**: Download missing PDFs (single month or all) and push to B2.
- **What to expect**: Time scales with gaps; harvests records if missing. Requires BrightData + B2 secrets.
- **Inputs**: `month` (`YYYYMM` or `all`), `limit` (0 = no limit).
- **Example**: `gh workflow run pdf-backfill.yml -f month=all`.

### Figure Backfill (`.github/workflows/figure-backfill.yml`)
- **Trigger**: Manual.
- **Purpose**: Translate figures for validated papers (defaults to CS/AI filter).
- **Automatic PDF acquisition**: If PDFs are missing from B2, the workflow automatically harvests fresh metadata and downloads them. This adds ~5-15 minutes but makes the workflow self-contained. BrightData credentials are required for auto-acquisition; if missing, fails with a clear error naming the missing secrets.
- **What to expect**: 30–90 minutes per month (up to 2 hours if PDFs need acquisition). Requires Gemini + Moondream + B2. BrightData credentials optional (only needed if PDFs missing).
- **Inputs**: `month` (required), `workers` (default 8), `figure_concurrent` (default 8), `limit` (0 = all), `cs_ai_only` (default true).
- **Example**:
  ```bash
  # Single command - automatically acquires missing PDFs
  gh workflow run figure-backfill.yml -f month=202510 -f limit=10 -f cs_ai_only=true
  ```

## Orchestration & Queue Management

### Pipeline Orchestrator (`.github/workflows/pipeline-orchestrator.yml`)
- **Trigger**: Manual.
- **Purpose**: Drives gates in order (preflight → harvest → ocr → translate → qa → render) using reusable gate runner.
- **What to expect**: Runs stages serially; failure stops the chain and optionally notifies Discord.
- **Inputs**: `stages` comma list, `batch_size`, `workers`, `matrix_size`.
- **Example**: `gh workflow run pipeline-orchestrator.yml -f stages=preflight,harvest,translate`.

### Month Range Backfill (`.github/workflows/month-range-backfill.yml`)
- **Trigger**: Manual.
- **Purpose**: Dispatches `backfill.yml` for a range of months.
- **What to expect**: Fire-and-forget API dispatches; concurrency set by `parallel`.
- **Inputs**: `start`, `end`, `parallel` (default 3), `workers`, `deploy`.
- **Example**: `gh workflow run month-range-backfill.yml -f start=202401 -f end=202406 -f parallel=2`.

### Batch Queue Orchestrator (`.github/workflows/batch-queue-orchestrator.yml`)
- **Trigger**: Manual.
- **Purpose**: Loops batch translations until the queue empties (or batch limit reached) and then triggers QA report + legacy `build.yml` rebuild.
- **What to expect**: Long-lived control loop; assumes `batch_translate.yml` exists and may no-op on the legacy `build.yml` trigger.
- **Inputs**: `total_batches` (0 = until empty), `batch_size`, `workers`, `runner_type`, `delay_between_batches`.
- **Example**: `gh workflow run batch-queue-orchestrator.yml -f total_batches=0 -f batch_size=300 -f workers=60`.

### Batch Translation Worker (`.github/workflows/batch_translate.yml`)
- **Trigger**: Manual.
- **Purpose**: High-throughput translation worker for queued jobs with QA.
- **What to expect**: Up to 6 hours; installs OCR stack; persists validated/flagged outputs to B2 and commits queue state.
- **Inputs**: `batch_size` (default 500), `workers` (default 80), `runner_type` (default `ubuntu-latest-8-cores`).
- **Example**: `gh workflow run batch_translate.yml -f batch_size=200 -f workers=40`.

### Queue Maintenance (`.github/workflows/queue-maintenance.yml`)
- **Trigger**: 04:00 UTC daily + manual.
- **Purpose**: Compacts `data/cloud_jobs*.json` and pushes updates.
- **What to expect**: Fast (minutes); writes to repo.
- **Example**: `gh workflow run queue-maintenance.yml`.

## Validation Gates

### Gate Template (`.github/workflows/validation-gate.yml`)
- **Trigger**: `workflow_call` only.
- **Purpose**: Shared runner for gate commands with optional pre/post hooks and artifact upload.
- **What to expect**: 60-minute default timeout; installs OCR deps each run.

### Preflight (`.github/workflows/preflight.yml`)
- **Trigger**: Manual.
- **Purpose**: Runs `env_diagnose --preflight` to verify secrets/binaries.
- **What to expect**: ~5 minutes; artifacts are optional.

### Harvest Gate (`.github/workflows/harvest-gate.yml`)
- **Trigger**: Manual or via orchestrator.
- **Purpose**: Seeds fixtures, runs harvest validator, posts Discord summary.
- **What to expect**: ~10–15 minutes; fails if harvest validator reports issues.
- **Inputs**: Optional `records_path`.

### OCR Gate (`.github/workflows/ocr-gate.yml`)
- **Trigger**: Manual or via orchestrator.
- **Purpose**: Seeds fixtures, prepares OCR report, runs OCR validator.
- **What to expect**: ~10–20 minutes; errors if OCR prep finds no data.

### Translation Gate (`.github/workflows/translation-gate.yml`)
- **Trigger**: Manual or via orchestrator (matrix-capable).
- **Purpose**: Runs translation validator with optional matrix fan-out.
- **What to expect**: Up to 3 hours (per job) depending on matrix size.
- **Inputs**: `batch_size`, `workers`, `matrix_index` (used when matrixed).

### Render Gate (`.github/workflows/render-gate.yml`)
- **Trigger**: Manual or via orchestrator.
- **Purpose**: Hydrates from B2 if available, renders site/search/PDFs, then runs render validator.
- **What to expect**: ~15–25 minutes; B2 optional but preferred.

## Testing, QA, and Sampling

### Translation Canary (`.github/workflows/translation-canary.yml`)
- **Trigger**: 06:00 UTC daily + manual.
- **Purpose**: Daily health check using `complete_paper_processor` for true E2E validation. Tests fresh metadata fetch, PDF download, and translation—catching issues that selection-based workflows would miss.
- **What to expect**: ~15–30 minutes; fails on any QA regression or if ChinaXiv's HTML structure changes.
- **Note**: Uses 3 fixed paper IDs with `--no-upload --no-figures --force` for fast, non-destructive testing.

### Complete Paper Processor (`.github/workflows/complete-paper.yml`)
- **Trigger**: Manual.
- **Purpose**: Process individual papers end-to-end with fresh metadata fetch. Use when harvest data is stale, paper is newly published, or debugging specific papers.
- **What to expect**: 3–5 minutes per paper (text only), 5–10 minutes with figures.
- **Inputs**: `paper_id` (single ID), `paper_ids` (comma-separated), `with_figures` (default false), `upload` (default true), `force` (default false).
- **Note**: Figure translation requires system deps (tesseract, ghostscript, etc.) not installed by this workflow. Use `figure-backfill.yml` for figures.
- **Examples**:
  ```bash
  # Single paper, text only (default) + B2 upload
  gh workflow run complete-paper.yml -f paper_id=202411.00001

  # Multiple papers, no upload (for testing)
  gh workflow run complete-paper.yml \
    -f paper_ids="202411.00001,202411.00002" \
    -f with_figures=false \
    -f upload=false

  # Re-process a paper even if it exists in B2
  gh workflow run complete-paper.yml -f paper_id=202411.00001 -f force=true
  ```

### Smoke Translate (`.github/workflows/smoke-translate.yml`)
- **Trigger**: Manual.
- **Purpose**: Small-batch translation smoke with optional deploy.
- **What to expect**: ~15–30 minutes for default limit; can harvest a month if needed.
- **Inputs**: `limit` (default 20), `workers`, `month`, `deploy`.

### Integration Translate (`.github/workflows/integration-translate.yml`)
- **Trigger**: Manual.
- **Purpose**: Targeted translation for specific `run_id` and optional IDs; writes artifacts only.
- **What to expect**: Up to 6 hours depending on selection size; no deploy.
- **Inputs**: `run_id` (required), `record_ids`, `limit`, `workers`.

### QA Report (`.github/workflows/qa_report.yml`)
- **Trigger**: 12:00 UTC daily + manual.
- **Purpose**: Generates pass/flag counts, creates issue if pass rate <90%, uploads markdown report.
- **What to expect**: ~5–10 minutes; requires repo write/issue perms.

## Developer & ChatOps

### CI (`.github/workflows/ci.yml`)
- **Trigger**: Push to `main` and all PRs.
- **Purpose**: Ruff + pytest.
- **What to expect**: ~3–6 minutes; blocking on main/PR.

### Claude ChatOps (`.github/workflows/claude.yml`)
- **Trigger**: `@claude` mentions on issues/PR comments/reviews.
- **Purpose**: Runs Anthropics’ Claude Code action with read perms and CI visibility.
- **What to expect**: Minutes; depends on Claude API availability.

### Claude Code Review (`.github/workflows/claude-code-review.yml`)
- **Trigger**: PR open/update.
- **Purpose**: Automated review via Claude Code action.
- **What to expect**: ~5–10 minutes; requires `CLAUDE_CODE_OAUTH_TOKEN`.

## Common Commands (quick reference)

- Run tonight's prod pipeline now: `gh workflow run daily-pipeline.yml`
- Backfill one month end-to-end: `gh workflow run backfill.yml -f month=YYYYMM`
- Rebuild site from validated translations in B2: `gh workflow run rebuild-from-b2.yml`
- Fill missing PDFs everywhere: `gh workflow run pdf-backfill.yml -f month=all`
- Translate figures for a month (CS/AI only): `gh workflow run figure-backfill.yml -f month=YYYYMM`
- Drain translation queue with batches: `gh workflow run batch-queue-orchestrator.yml -f total_batches=0`
- Quick health check: `gh workflow run translation-canary.yml`
- Process a single paper (fresh metadata): `gh workflow run complete-paper.yml -f paper_id=YYYYMM.NNNNN`
