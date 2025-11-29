# Translation Push Plan — Queue-Based Batches, QA, Deploy (2025-10-12)

## Overview
This plan details how to complete the large-scale translation backfill using our existing queue-based batch workflows, keep QA gating enforced, and deploy the site via Cloudflare Pages. It includes exact file paths, commands, small code adjustments (optional), test recommendations, acceptance criteria, rollback, and timelines.

Design principle: Prefer the simplest working path that maximizes throughput with minimal changes. We use the existing GitHub Actions batch worker + orchestrator, and only add a tiny UI polish (dynamic footer date) while deferring non-essential telemetry infrastructure.

## Goals
- Translate and QA-gate the full backlog (ChinaXiv) efficiently and safely.
- Keep CI simple and transparent; rely on current Actions workflows and artifacts.
- Maintain site quality: canonical URLs, sitemap coverage, client search, and /abs aliases.
- Provide minimal observability for failures (keys, rate limits) without new infra.

## Decision Summary
- Primary path: Queue-based batches (fastest, already wired)
  - Workflows: `.github/workflows/batch_translate.yml`, `batch-queue-orchestrator.yml`, `qa_report.yml`, `build.yml`.
  - QA gating: enabled in batch worker via `--with-qa` (keeps flagged output out of site).
- Fallback path: Month-by-month backfill (simpler mental model, slower)
  - Workflow: `.github/workflows/backfill.yml` (translates + builds per month).
- Minimal polish: dynamic footer “Last updated” date in `src/templates/base.html` passed by `src/render.py`.
- Telemetry: Use existing `src.monitor` and Discord webhook only; defer Prometheus/Loki until needed.

## Prerequisites and Assumptions
- Required secrets set in GitHub: `OPENROUTER_API_KEY`, `CF_API_TOKEN`. Optional (for harvest): `BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE`.
- Queue file present (or will be created): `data/cloud_jobs.json`.
- Build output: `site/` (Cloudflare Pages project `chinaxiv-english`).
- Cost: ~ $0.0013/paper (DeepSeek via OpenRouter) — monitor via OpenRouter dashboard.

## Implementation Plan

### Phase 1 — Preflight Checks (local + CI)
- Verify API key locally:
  - `python -m src.tools.env_diagnose --check && python -m src.tools.env_diagnose --validate`
- Verify CI preflight (already in workflows): `build.yml` and `backfill.yml` run env diagnostics.
- Optional: start local monitor to sanity check endpoints during dev:
  - `python -m src.monitor &` (then open `http://localhost:5000`)

### Phase 2 — Initialize Queue (one-time)
- Command (local):
  - `python scripts/init_cloud_queue.py`
  - Inspect: `python -m src.cloud_job_queue stats`
  - Commit: `git add data/cloud_jobs.json && git commit -m "feat(queue): initialize cloud job queue" && git push`
- Expected: `data/cloud_jobs.json` enumerates all paper IDs to translate.

### Phase 3 — Validate Batch Worker and Orchestrator Configuration
- Batch worker (already enabled with QA): `.github/workflows/batch_translate.yml`
  - Translation command:
    - `python -m src.pipeline --cloud-mode --with-qa --batch-size "$BATCH_SIZE" --workers "$WORKERS" --worker-id "$WORKER_ID" --skip-selection`
  - Commits progress to `data/cloud_jobs.json`, and saves outputs to `data/translated/` and `data/flagged/`.
- Orchestrator: `.github/workflows/batch-queue-orchestrator.yml`
  - Triggers sequential batches until the queue is empty (or a configured limit), then kicks `qa_report.yml` and `build.yml`.

### Phase 4 — Dry Run (small batch)
- Actions → Batch Translation Worker → Run workflow with:
  - `batch_size=300`, `workers=60`, `runner_type=ubuntu-latest-8-cores`.
- Verify outputs:
  - `python -m src.cloud_job_queue stats` shows decreasing pending count.
  - Artifacts: flagged translations uploaded.
  - Repo: `data/translated/*.json` committed by the job.

### Phase 5 — Scale Up (orchestrator or manual parallelization)
- Recommended (sequential orchestration):
  - Actions → Translation Orchestrator → Run workflow with:
    - `total_batches=0` (run until queue empty), `batch_size=500`, `workers=80`, `runner_type=ubuntu-latest-8-cores`, `delay_between_batches=60`.
- Optional (manual speed-up): trigger 3–5 Batch Worker runs in parallel; each claims a different batch from the queue.
- Tuning notes:
  - If rate limits or costs rise: reduce `workers` and/or parallel runs.
  - If commit conflicts occur frequently: keep sequential orchestration; it already includes retries.

### Phase 6 — Rebuild and Deploy Site
- Orchestrator ends by triggering:
  - QA report (`qa_report.yml`) — summarizes QA pass rate and flags.
  - Site rebuild (`build.yml`) — renders `site/`, builds `search-index.json(.gz)`, deploys to Cloudflare Pages.
- Manual rebuild (optional): Actions → build-and-deploy → `skip_harvest=true`.

### Phase 7 — Minimal Observability
- Monitoring (no new infra):
  - Ensure `DISCORD_WEBHOOK_URL` is set; monitoring alerts already integrated in `src/monitoring.py`.
  - Optional: enable error budget alerts with env in Actions: `ENABLE_ERROR_BUDGET_ALERTS=true`.
- Cloudflare and Actions logs: use their dashboards for run and deploy status.
- OpenRouter dashboard: track spend and error codes.

### Phase 8 — Fallback Path (Month-by-Month Backfill)
- Actions → backfill-month → inputs:
  - `month=YYYYMM`, `workers=20`, `deploy=true`.
- For multiple months, run newest to oldest; dedupe via `data/seen.json` prevents rework.

### Phase 9 — Tiny UI Polish (Dynamic Footer Date)
- Rationale: Show freshness during long push; minimal, safe change.
- Files:
  - `src/render.py` — pass `build_date` to templates (UTC today).
  - `src/templates/base.html` — render dynamic date instead of hardcoded string.
- Pseudocode:
  - In `src/render.py` (near existing Jinja render setup):
    ```python
    from datetime import datetime
    build_date = datetime.utcnow().strftime("%Y-%m-%d")
    # pass build_date into all template.render(...) calls (index, monitor, donations, item)
    tmpl_index.render(..., build_version=build_version, build_date=build_date)
    ```
  - In `src/templates/base.html` footer section:
    ```html
    <p class="footer-meta">
      Last updated: {{ build_date }}<br>
      <a href="https://github.com/seconds-0/chinaxiv-english" target="_blank" rel="noopener">Source Code</a><br>
      <a href="{{ root }}/donation.html">Support Us</a>
    </p>
    ```
- Rollback: revert the two edits (template and render param) if needed.

## Test Recommendations

### Unit / Integration
- Render metadata (new tests): `tests/test_render_metadata.py`
  - Asserts canonical URLs are absolute (already implemented) and OG tags exist.
  - Asserts `site/sitemap.xml` contains each `/items/<id>/` and `/abs/<id>/` entry.
- Search index excludes flagged: `tests/test_search_excludes_flagged.py`
  - Create a flagged translation JSON and a passing one in a temp dir; run `src/search_index.py`; assert only passing item is indexed; assert `.gz` exists.
- Dynamic footer date: `tests/test_footer_date.py`
  - Monkeypatch date to a constant, run `render_site` with one item to temp `site/`, open an output page (index.html or an item page) that includes the base template; assert the date string is present.
- Queue stats smoke test: `tests/test_cloud_queue_stats.py`
  - Call `python -m src.cloud_job_queue stats` via subprocess; assert exit code 0 and parse key fields.

### E2E Smoke (local)
- Run a small pipeline locally with bypass or a tiny queue (≤2 items), then:
  - `python -m src.render && python -m src.search_index`
  - `python -m http.server -d site 8001 &` and verify `/items/<id>/`, `/abs/<id>/`, search loads (`search-index.json.gz`).

## Acceptance Criteria
- Batch translation completes for the defined backlog window:
  - Queue statistics show `pending == 0` and `completed` == total expected.
- QA Report runs and shows pass rate ≥ 90% (tunable via `src/qa_filter.py`).
- Site rebuild completes and deploys to Cloudflare Pages without errors.
- Production checks:
  - `https://chinaxiv-english.pages.dev/sitemap.xml` contains all items and `/abs` aliases.
  - A random sample loads quickly (<3s) and search works (index gz present).
  - Footer shows a current date from the build day (UTC).
- No critical alerts (401/402 auth/payment) and error budget not exceeded (if enabled).

## Rollback / Escape Hatches
- If orchestrator/queue friction occurs (conflicts, runner limits): switch to `backfill-month` workflow.
- If QA is over-restrictive: temporarily relax thresholds in `src/qa_filter.py` and re-run only flagged items.
- If costs spike: reduce `workers`, `batch_size`, or parallel runs; pause orchestrator (actions UI).
- UI polish rollback: revert template and render param changes in a single commit.

## Risks and Mitigations
- Rate limits / costs: Throttle workers; enable error budget alerts; watch OpenRouter dashboard.
- Git commit conflicts: Orchestrator and batch worker already retry; keep sequential when needed.
- Incorrect sitemap or canonical: Covered by unit tests; can roll forward with a fix without re-translation.
- Search performance: Index is gz-compressed and small per item; defer heavier changes until needed.

## Timeline (estimate)
- Day 0: Queue init + Dry run batch (2–4 hours total with verification).
- Day 1–3: Orchestrator running (sequential) or 3–5 parallel workers (faster). Monitor intermittently.
- After completion: QA report + rebuild + spot checks (~1–2 hours).
- UI footer change + tests: ~30–45 minutes.

## File Map (touched/used)
- Workflows:
  - `.github/workflows/batch_translate.yml`
  - `.github/workflows/batch-queue-orchestrator.yml`
  - `.github/workflows/qa_report.yml`
  - `.github/workflows/build.yml`
  - `.github/workflows/backfill.yml` (fallback)
- Pipeline & queue:
  - `src/pipeline.py`
  - `src/cloud_job_queue.py`
  - `src/qa_filter.py`
- Rendering & search:
  - `src/render.py`, `src/templates/base.html`, `src/templates/item.html`
  - `src/search_index.py`, `assets/site.js`
- Monitor (optional):
  - `src/monitor.py`, `src/monitoring.py`
- Scripts:
  - `scripts/init_cloud_queue.py`

## Command Reference (local)
- Start monitor (dev): `python -m src.monitor &`
- Initialize queue: `python scripts/init_cloud_queue.py`
- Queue stats: `python -m src.cloud_job_queue stats`
- Local pipeline (tiny): `python -m src.pipeline --cloud-mode --with-qa --batch-size 5 --workers 2 --worker-id local-dev --skip-selection`
- Render+index: `python -m src.render && python -m src.search_index`
- Preview site: `python -m http.server -d site 8001 &`

---

Notes:
- We defer adding Prometheus/Loki (docs/todo/telemetry.md) to keep this push simple. If issues recur (sustained rate limits, unknown failures), we can add file-based JSONL logs and basic metrics next.
- The batch worker already includes `--with-qa`; no workflow changes needed to enforce QA gating.

