# GitHub Actions Workflows

This document describes all GitHub Actions workflows used in the ChinaXiv Translations project.

## Quick Reference

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `daily-pipeline.yml` | Daily 3 AM UTC + manual | Main production pipeline |
| `backfill.yml` | Manual | Single-month backfill |
| `pdf-backfill.yml` | Manual | Download missing PDFs to B2 |
| `figure-backfill.yml` | Manual | Translate figures for CS/AI papers |
| `ci.yml` | Push/PR | Lint and test |

---

## Primary Production Pipelines

### Daily Pipeline
- **File**: `.github/workflows/daily-pipeline.yml`
- **Schedule**: Daily at 3 AM UTC + manual dispatch
- **Purpose**: Main production pipeline that harvests new papers, translates them, renders the site, and deploys to Cloudflare Pages.

**What to expect**: Runs for 15-30 minutes. Harvests current and previous month, selects new papers, downloads PDFs, translates text (with figures on non-PR builds), publishes to B2, hydrates from B2, renders site, and deploys.

**Inputs**:
- `skip_harvest` (bool): Skip harvest and translation, rebuild site only

---

### Backfill (Single Month)
- **File**: `.github/workflows/backfill.yml`
- **Trigger**: Manual dispatch
- **Purpose**: End-to-end backfill for a single month: harvest → download PDFs → select → translate → render → optional deploy.

**What to expect**: Runs for 20-60 minutes depending on paper count. Useful for filling gaps in coverage or reprocessing a specific month.

**Inputs**:
- `month` (required): Month to backfill (YYYYMM format)
- `workers` (default: 20): Parallel translation workers
- `deploy` (default: true): Deploy site after backfill
- `no_latest` (default: false): Don't update selections/latest.json

---

### Batch Translation Worker
- **File**: `.github/workflows/batch_translate.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Cloud-native batch translation that pulls from a queue and processes papers with high parallelism (up to 80 workers).

**What to expect**: Long-running job (hours) that processes large queues. Uses B2 for persistence and supports QA filtering.

**Inputs**:
- `batch_size` (default: 50): Papers per batch
- `workers` (default: 40): Parallel workers
- `min_workers`, `max_workers`: Worker range for adaptive scaling

---

### CI (Continuous Integration)
- **File**: `.github/workflows/ci.yml`
- **Trigger**: Push to main, all PRs
- **Purpose**: Standard CI gate that runs linting (ruff) and tests (pytest) on every push and PR.

**What to expect**: Runs in 2-5 minutes. Must pass before merging to main.

---

## Figure & PDF Workflows

### PDF Backfill
- **File**: `.github/workflows/pdf-backfill.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Download missing PDFs from ChinaXiv and persist to B2. Required before figure translation.

**What to expect**: Duration depends on number of missing PDFs. Downloads from ChinaXiv via BrightData proxy, uploads to B2. Run this before figure-backfill if PDFs are missing.

**Inputs**:
- `month` (required): Month to process (YYYYMM) or "all" for all months
- `limit` (default: 0): Max PDFs to download (0 = no limit)

---

### Figure Backfill
- **File**: `.github/workflows/figure-backfill.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Translate figures in PDFs using Gemini for translation and Moondream for QA validation.

**What to expect**: Runs 30-90 minutes per month. Extracts figures from PDFs, translates Chinese text to English using Gemini, validates with Moondream, uploads to B2. By default filters to CS/AI papers only.

**Inputs**:
- `month` (required): Month to process (YYYYMM)
- `workers` (default: 8): Parallel paper workers
- `figure_concurrent` (default: 8): Concurrent figures per paper
- `limit` (default: 0): Max papers (0 = all)
- `cs_ai_only` (default: true): Filter to CS/AI papers only

---

## Orchestration & Recovery

### Month Range Backfill
- **File**: `.github/workflows/month-range-backfill.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Orchestrates multiple `backfill.yml` runs for a range of months. Useful for bulk historical backfill.

**Inputs**:
- `start_month`, `end_month`: Range in YYYYMM format

---

### Batch Queue Orchestrator
- **File**: `.github/workflows/batch-queue-orchestrator.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Runs sequential batch translation jobs until the queue is empty.

**What to expect**: Long-running orchestration that keeps spawning batch jobs. Good for exhausting a large backlog.

---

### Pipeline Orchestrator
- **File**: `.github/workflows/pipeline-orchestrator.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Multi-stage pipeline orchestration with configurable stages (preflight → harvest → ocr → translate → qa → render).

**What to expect**: Complex multi-stage workflow with Discord notifications between stages. Use for controlled, monitored backfill.

---

### Rebuild from B2
- **File**: `.github/workflows/rebuild-from-b2.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Minimal rebuild that hydrates translations from B2, renders the site, and deploys. No harvest or translation.

**What to expect**: Fast rebuild (10-15 min). Use when B2 has correct data but site needs redeployment.

---

## Validation Gates

These workflows use a reusable template (`validation-gate.yml`) to run validation commands for specific pipeline stages.

### Validation Gate (Template)
- **File**: `.github/workflows/validation-gate.yml`
- **Trigger**: Called by other workflows (`workflow_call`)
- **Purpose**: Reusable template for running validation commands.

### Individual Gates
- **`harvest-gate.yml`**: Validates harvest functionality
- **`translation-gate.yml`**: Validates translation functionality (supports matrix jobs)
- **`ocr-gate.yml`**: Validates OCR functionality
- **`render-gate.yml`**: Validates rendering/search/PDF generation

**What to expect**: Quick validation runs (5-15 min) that test specific pipeline stages in isolation.

---

## Testing & Monitoring

### Smoke Translate
- **File**: `.github/workflows/smoke-translate.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Quick smoke test with limited papers (default: 20) to verify translation pipeline works.

**Inputs**:
- `limit` (default: 20): Number of papers to translate
- `workers` (default: 5): Parallel workers
- `month`: Optional specific month

---

### Translation Canary
- **File**: `.github/workflows/translation-canary.yml`
- **Schedule**: Daily at 6 AM UTC + manual dispatch
- **Purpose**: Daily health check that translates a small set of hardcoded paper IDs to verify the pipeline is working.

**What to expect**: Quick (5-10 min) daily check. Alerts if translation fails.

---

### Preflight
- **File**: `.github/workflows/preflight.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Environment validation using `env_diagnose` to check API keys and configuration.

---

### Integration Translate
- **File**: `.github/workflows/integration-translate.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Translation of specific papers via `run_id` and optional `record_ids`. Useful for targeted testing.

---

### QA Report
- **File**: `.github/workflows/qa_report.yml`
- **Schedule**: Daily at 12 PM UTC + manual dispatch
- **Purpose**: Generates QA statistics and creates a GitHub issue if pass rate is low.

**What to expect**: Quick report generation. Creates issues automatically when quality drops.

---

### Queue Maintenance
- **File**: `.github/workflows/queue-maintenance.yml`
- **Schedule**: Daily at 4 AM UTC + manual dispatch
- **Purpose**: Compacts the cloud job queue, retaining only the last 100 completed jobs.

---

## Developer Assistance

### Claude (Issue/PR Interaction)
- **File**: `.github/workflows/claude.yml`
- **Trigger**: @claude mentions in issues/comments
- **Purpose**: Responds to @claude mentions in issues and PR comments for developer assistance.

---

### Claude Code Review
- **File**: `.github/workflows/claude-code-review.yml`
- **Trigger**: PR open/sync, @claude mentions in reviews
- **Purpose**: Automated code review via Claude Code on pull requests.

---

## Required Secrets

### Translation
| Secret | Purpose |
|--------|---------|
| `OPENROUTER_API_KEY` | Text translation via DeepSeek |

### Figure Translation
| Secret | Purpose |
|--------|---------|
| `GEMINI_API_KEY` | Figure translation via Google Gemini |
| `MOONDREAM_API_KEY` | Figure QA validation |

### Storage (B2)
| Secret | Purpose |
|--------|---------|
| `BACKBLAZE_KEY_ID` | B2 authentication |
| `BACKBLAZE_APPLICATION_KEY` | B2 authentication |
| `BACKBLAZE_S3_ENDPOINT` | B2 S3 endpoint (e.g., `https://s3.us-west-004.backblazeb2.com`) |
| `BACKBLAZE_BUCKET` | B2 bucket name |
| `BACKBLAZE_PREFIX` | Optional path prefix |

### Harvesting
| Secret | Purpose |
|--------|---------|
| `BRIGHTDATA_API_KEY` | BrightData proxy for ChinaXiv |
| `BRIGHTDATA_ZONE` | BrightData zone |
| `BRIGHTDATA_UNLOCKER_ZONE` | BrightData unlocker zone (PDF downloads) |
| `BRIGHTDATA_UNLOCKER_PASSWORD` | BrightData unlocker password |

### Deployment
| Secret | Purpose |
|--------|---------|
| `CF_API_TOKEN` | Cloudflare Pages deployment |
| `DISCORD_WEBHOOK_URL` | Notifications (optional) |

---

## Usage Examples

### Run Daily Pipeline Manually
```bash
gh workflow run daily-pipeline.yml
```

### Backfill a Specific Month
```bash
gh workflow run backfill.yml -f month=202410
```

### Download Missing PDFs
```bash
# All months
gh workflow run pdf-backfill.yml -f month=all

# Specific month
gh workflow run pdf-backfill.yml -f month=202410
```

### Translate Figures (CS/AI papers)
```bash
# Pilot test with 10 papers
gh workflow run figure-backfill.yml -f month=202410 -f limit=10

# Full month
gh workflow run figure-backfill.yml -f month=202410 -f limit=0
```

### Check Workflow Status
```bash
gh run list --workflow=daily-pipeline.yml --limit 5
gh run view <run_id>
gh run view <run_id> --log
```

---

## Monitoring

- **GitHub Actions tab**: View workflow runs and logs
- **Discord**: Automated notifications for failures and alerts
- **Cloudflare Pages dashboard**: Deployment status
- **Daily canary**: `translation-canary.yml` runs at 6 AM UTC
- **QA reports**: `qa_report.yml` runs at 12 PM UTC, creates issues for low pass rates
