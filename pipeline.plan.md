<!-- 9aea2a18-a710-4463-bc29-c57ec16a053a 151ab6e4-83ba-46d8-8801-1edda1c46ec8 -->
# ChinaXiv pipeline audit, remediations, run environment, and staged validation gates

## Executive recommendation (run environment recap)

- **Primary execution**: GitHub Actions on `ubuntu-latest` with explicit apt provisioning of Tesseract + OCR dependencies during each workflow run. Container image pinning remains a stretch goal once we wire up registry publishing.
- **Supporting services**: Cloudflare Pages continues to host the static site; Cloudflare R2 remains the preferred durable store for heavy artifacts (OCR’d PDFs, intermediate JSON) once we wire it in.
- **Fallback decision gate**: If OCR/translation throughput hits GitHub’s 6‑hour limit even after batching, spin up a neutral container runner (Fly.io/Railway) triggered from GHA; defer until metrics prove it necessary.

## Work completed to date (2025‑10‑15)

### Containerisation & CI plumbing
- Removed stale `Dockerfile.ci`/build workflow; for now we rely on `ubuntu-latest` runners with per-job apt installs covering Tesseract + OCR dependencies.
- Updated `preflight.yml`, `harvest-gate.yml`, and `translation-gate.yml` to install system packages inline before invoking the Python validators. This removes “missing binary” failures without introducing registry drift.
- Introduced `pipeline-orchestrator.yml` as a top-level dispatcher (workflow_call) that sequences preflight → harvest → OCR → translation → QA → render. Current version triggers stage workflows and sets the stage for matrix parallelism; polling and gating still TODO.
- Added reusable workflow `validation-gate.yml` so individual gate workflows share setup/install logic instead of duplicating steps.
- Replaced the orchestrator's fixed `sleep 60` with API-backed polling so manual dispatches block until each gate workflow finishes (and fail fast on non-success conclusions).

### Environment validation (Stage 0)
- `src/tools/env_diagnose.py`
  - Added `--preflight` command that batches env/secrets/binary/disk checks.
  - Verifies `OPENROUTER_API_KEY`, `BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE`; confirms connectivity to Bright Data (200 response) and OpenRouter header availability.
  - Checks binary presence (`tesseract`, `ocrmypdf`, `pandoc`) and disk space headroom.
  - Emits machine JSON+Markdown reports (`reports/preflight_report.json|md`) and mirrors summary to `site/stats/validation/preflight_report.json`.
- `preflight.yml` installs binaries inline on the runner and then runs `python -m src.tools.env_diagnose --preflight`, uploading reports.

### Harvest stabilization & QA (Stage 1)
- `src/harvest_audit.py`
  - New audit CLI: iterates every `data/records/chinaxiv_*.json` and emits per-file stats (schema pass/fail, duplicate IDs, PDF reachability) plus aggregate metrics.
  - Records per-paper issues, resolves redirect PDFs, and reports to `reports/harvest_audit*.json`. Used to diagnose legacy IA data vs. new BrightData harvests.
- `src/file_service.py`
  - `write_json` now supports `Path` objects (fixes atomic write failures when scrapers passed `Path`).
- `src/harvest_chinaxiv_optimized.py`
  - Added exponential backoff and retry for 429/5xx/timeouts and log clarity for Bright Data responses.
- `src/pdf_pipeline.py`
  - PDF downloads now validate content-type or `%PDF-` magic, enforce ≥1 KB size, and remove bogus files.
  - OCR detection heuristics now persist to unified `reports/ocr_report.json` (foundation for Stage 2 gate).
- `src/validators/harvest_gate.py`
  - Enforces strict schema (`id/title/abstract/creators/subjects/date/source_url/pdf_url`) with explicit error messaging.
  - Automatically resolves PDFs from landing pages when the saved URL fails (`BeautifulSoup` + relative link handling).
  - Streams PDF head bytes to confirm validity and records resolved URLs and issue lists per paper.
  - Outputs structured `reports/harvest_report.json|md` and mirrors summary to `site/stats/validation/harvest_report.json`.
  - Gate now hard-fails when no records are present or thresholds are missed, eliminating false greens on empty input.
- Harvest/QA thresholds are now configurable via `validation_thresholds.harvest` in `src/config.yaml`.
- `harvest-gate.yml`
  - Runner-based job triggers `python -m src.validators.harvest_gate` (optional `records_path` input) after installing OCR tooling, then uploads artifacts.
  - `scripts/prepare_gate_fixtures.py` seeds representative records and PDFs when no harvested data exists, so the gate always exercises non-empty inputs in CI.
- **Outcome**: Latest audits for 2025-02 BrightData harvest show 0% PDF failures; remaining schema misses are limited to old IA identifiers lacking full metadata.

### Translation gating groundwork (Stage 3)
- Added `tests/integration/test_pipeline_smoke.py` for an end-to-end smoke that exercises harvest/OCR/translation/render validators plus an OCR improvement regression case.
- `translation-gate.yml`
  - Extended with workflow inputs (`batch_size`, `workers`, `matrix_index`) and provisions OCR tooling directly on the runner prior to executing the validator.
  - Translation gate now fails fast if no translated artifacts exist or QA flags beyond configurable thresholds (`validation_thresholds.translation`).
  - Uses the same fixture seeding helper to ensure gating covers real-looking translations even on fresh clones.
- Translation queue hardening is WIP: `data/cloud_jobs.json` inspected; need resumable workers + cost tracking adjustments.
- Added integration smoke tests under `tests/integration/test_pipeline_smoke.py` to exercise harvest → OCR → translation → render flow plus OCR improvement checks.

### Stage 2 OCR enhancements
- `src/pdf_pipeline.py` now records alpha/character distribution metrics and consults config-driven thresholds before accepting OCR output.
- `src/validators/ocr_gate.py` enforces quality via alpha ratio and dominant-character ceilings alongside character gains (`validation_thresholds.ocr`).

### Scale benchmarking
- Added `scripts/run_scale_benchmark.py` to generate synthetic workloads or benchmark existing records, capturing metrics under `reports/scale_benchmark/`.
- Synthetic 3,411-record run (native fixture PDFs) completes in ~0.1s locally; schedule a BrightData-backed run once full harvest data is staged to capture network-bound timing.

### Reporting & artifacts
- Reports stored under `reports/` and mirrored to `site/stats/validation/` (preflight + harvest). Need to add similar mirroring for OCR/translation/render once complete.
- Added `reports/harvest_audit_202502.json` + aggregate `reports/harvest_audit.json` for dashboards.
- Introduced `src/reporting.py` helpers so validation gates share markdown/JSON generation and site mirroring logic.

## Outstanding work by stage

### Stage 0 – Preflight
- [x] Containerised check
- [ ] Hook report surfacing into `site/monitor.html` auto-refresh

### Stage 1 – Harvest & PDF
- [x] Schema validator + PDF verification
- [x] Seed deterministic fixtures for CI gating and document usage
- [ ] Implement Bright Data retry policy in `harvest_chinaxiv.py` (legacy path)
- [ ] Persist harvest audit summaries to R2 for cross-run comparison

### Stage 2 – OCR detection & execution
- [x] Need detection + report scaffold (in `src/pdf_pipeline.py`)
- [x] Establish OCR accuracy benchmark (`scripts/run_ocr_benchmark.py` now ships English + Chinese fixtures and runs clean with Ghostscript/qpdf + chi_sim data)
- [x] Wire gate module (`src/validators/ocr_gate.py`) to assert coverage improvements and fail on missing execution or empty detection reports
- [ ] Add OCR execution retry metrics + fallback API (stored credentials TBD)

### Stage 3 – Translation queue, backoff, idempotency
- [ ] Upgrade `cloud_job_queue` to durable DB or R2 + lock semantics
- [ ] Introduce worker heartbeats, stuck-job auto retry, JSONL logs per job
- [ ] Track per-job cost via `cost_tracker`; emit `reports/costs_summary.json`
- [ ] Harden `_call_openrouter` fallback list + jitter backoff once queue fails after multiple attempts

### Stage 4 – Translation QA & completeness
- [ ] Implement English-language detection (fastText/CLD3 snippet) and length checks
- [ ] Emit `reports/translation_qa_report.{json,md}` and attach to CI
- [ ] Update gate workflow to fail on QA regression and block downstream stages

### Stage 5 – Render & index
- [ ] Gate render/index workflows on QA PASS artifact
- [ ] Validate `search-index.json` schema + counts vs QA pass count
- [ ] Surface site preview comparisons (before/after) for flagged items

### Stage 6 – Observability & ops hygiene
- [ ] Structured JSON logging (stage, record_id, attempt, latency, error)
- [ ] Discord alert pipeline for repeated failures / nightly summary
- [ ] Dashboard synthesis (R2 + Cloudflare Pages) for stage metrics

### Cross-cutting items
- [ ] Unit tests for harvest parser variants, OCR detection, translation QA heuristics
- [x] Basic unit tests for harvest/translation gates (pass/fail scenarios)
- [ ] Smoke E2E harness translating 2 papers end-to-end inside container
- [ ] Documentation updates (README + developer guide for new workflows)
- [ ] Revisit container pinning only if runner provisioning proves flaky; reintroduce image build pipeline at that point

## Change log (chronological)

1. **System deps on runners** – inline apt installs in translation/harvest/preflight workflows while container publishing remains pending.
2. **Preflight gate** – `src/tools/env_diagnose.py`, `.github/workflows/preflight.yml`, reports + site mirror.
3. **Harvest audit tooling** – `src/harvest_audit.py`, `reports/harvest_audit*.json`.
4. **Write JSON Path fix** – `src/file_service.py` Path support.
5. **Harvest scraper resiliency** – `src/harvest_chinaxiv_optimized.py` retry/backoff.
6. **PDF download hardening** – `src/pdf_pipeline.py` validation + detection telemetry.
7. **Harvest gate rewrite** – `src/validators/harvest_gate.py`, `.github/workflows/harvest-gate.yml`, site mirroring.
8. **Translation gate containerisation** – `.github/workflows/translation-gate.yml` matrix inputs.
9. **Pipeline orchestrator scaffold** – `.github/workflows/pipeline-orchestrator.yml` (needs polling updates).
10. **Reports mirrored to site** – `site/stats/validation/harvest_report.json` etc.
11. **Gate fixtures + tests** – `scripts/prepare_gate_fixtures.py`, CI workflows seed sample data, and unit tests ensure gates fail on empty inputs.

## Next sprint priorities

1. Finish Stage 2 OCR gate (execution report + validation metrics) and hook into orchestrator.
2. Harden translation queue (Stage 3): worker idempotency, stuck-job reset, per-job cost reporting.
3. Implement Translation QA gate (Stage 4) with language detection + length thresholds.
4. Wire orchestrator gating logic (halt downstream stages on previous failure; poll for completion) and add Discord notifications.
5. Begin structured logging and dashboard upload (Stage 6).

## Follow-up implementation plan (2025‑10‑16)

**Sequencing**

1. **Plan consolidation** (`chinaxiv-english-3`) – Expand this document section with scope, assumptions, and validation strategy (complete with this update).
2. **OCR gate artifact generation** (`chinaxiv-english-4`) – Add an execution step to populate `reports/ocr_report.json` before the gate runs, covering both fixture and hydrated data paths.
3. **Render gate hydration & rebuild** (`chinaxiv-english-5`) – Ensure render validation jobs hydrate from Backblaze (or fixtures locally) and rebuild the site prior to counting artifacts.
4. **Orchestrator failure handling** (`chinaxiv-english-6`) – Modify `pipeline-orchestrator.yml` so failures short-circuit later stages and raise Discord alerts.
5. **Queue maintenance automation** (`chinaxiv-english-7`) – Introduce tooling/workflows to compact `data/cloud_jobs.json`, archive completed items, and minimise merge conflicts.
   - Local validation: `python -m src.tools.compact_cloud_queue --queue-file data/cloud_jobs_unit.json --archive-file data/cloud_jobs_unit_archive.json --retain-completed 5` (unit sample proves archival).
   - CI validation pending: trigger `.github/workflows/queue-maintenance.yml` once merged; rollback by restoring `data/cloud_jobs.json` from archive artifacts.
   - Hydration helper: `python scripts/hydrate_from_b2.py --target data/translated` centralises Backblaze sync logic for build and gates.
    - Automated Claude review workflows require repository variable `CLAUDE_CODE_ENABLED=true` and secret `CLAUDE_CODE_OAUTH_TOKEN`; otherwise they skip gracefully.
6. **Local dev guidance without secrets** (`chinaxiv-english-8`) – Document scripts/fixtures so gates can run without BrightData/OpenRouter credentials.
7. **QA & review** (`chinaxiv-english-9`) – Run targeted automation (pytest + gate workflows) and self-review prior to handoff.
8. **OCR toolchain availability** (`chinaxiv-english-17`) – Bundle OCR prerequisites and synthetic fixture generation so gate helpers succeed locally and in CI.

**Key assumptions**

- Backblaze credentials remain available in CI, but local developers may rely on fixtures.
- OCR execution can reuse `src/pdf_pipeline.process_paper` on a limited set of IDs without exhausting time limits.
- GitHub Actions concurrency permits an additional queue-maintenance workflow without hitting rate limits.

**Testing approach**

- Unit/integration coverage: exercise `scripts/prepare_gate_fixtures.py`, new OCR execution helpers, and queue compaction utilities via pytest.
- Workflow validation: run `preflight.yml`, `ocr-gate.yml`, `render-gate.yml`, and orchestrator dry runs using `act` or stub dispatches where practical.
- Manual sanity: inspect updated `reports/*.json` to confirm metrics reflect hydrated assets, and verify queue size reduction on sample data.

## Branching & verification strategy

- Work currently resides on feature branch `ci/validation-gates`. For isolation/testing, create a fresh branch (e.g., `feat/pipeline-harden-20251015`) from the latest main commit and cherry-pick the dated commits or push the current state after rebase.
- Always run `python -m src.tools.env_diagnose --preflight` locally before dispatching workflows.
- Validation order (manual):
  1. `python -m src.tools.env_diagnose --preflight`
  2. `python -m src.harvest_audit --records data/records/<month>.json`
  3. `python -m src.validators.harvest_gate --records …`
  4. (pending) OCR gate
  5. `python -m src.validators.translation_gate`
  6. Render gate (pending)
- For CI: orchestrator should stop at first failing gate; human inspects reports under `reports/` and site mirrors.

---

_Last updated: 2025‑10‑16 by GPT‑5 Codex (pipeline audit agent)._
