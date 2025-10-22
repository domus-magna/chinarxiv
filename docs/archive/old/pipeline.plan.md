<!-- Archived 2025-10-21: This planning scratchpad is kept for historical reference. See AGENTS.md for the current planning protocol and docs/WORKFLOWS.md for CI flows. -->
<!-- Original filename: pipeline.plan.md (moved from repo root) -->

<!-- BEGIN ORIGINAL CONTENT -->
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
  - Extended with workflow inputs (`batch_size`, `workers`, `matrix_index`) and provisions OCR tooling directly on the runner prior to translation steps (for combined smoke).
  - Publishes `reports/translation_report.json|md` and composite summaries under `site/stats/validation/translation_report.json`.
  - Current version focuses on functional success/metrics; stricter quality thresholds to be added with language detection.
- `src/validators/translation_gate.py`
  - Validates `title_en`/`abstract_en` present, language detected as English, and minimum token length; flags improbable translations with heuristic checks (ratio and character set).

### Render validation (Stage 5 groundwork)
- `src/validators/render_gate.py` checks for site directory existence, minimum item count, presence of `search-index.json`, and non-empty homepage.
- Adds sanity link validation for per-item pages and 404 detection for asset links.
- `render-gate.yml` runs render then validates outputs and uploads `reports/render_report.*`.

### Admin dashboard & reporting
- Expanded `src/monitor.py` to serve JSON previews of gate reports and list recent runs from `.runs.json`.
- Added a compact HTML dashboard (`src/templates/monitor.html`) summarizing the most recent gate outcomes and linking to per-gate details.
- Introduced `scripts/monitor.py` to tail gate logs and update `.runs.json`.

## Target orchestrated flow (post‑MVP)

1. Preflight (`env_diagnose --preflight`) → fail fast on missing keys/binaries, upload reports.
2. Harvest audit/gate (`harvest_audit`, `harvest_gate`) → resolve PDFs, fix schema issues.
3. OCR gate (Stage 2) → run OCR sampling and fail when coverage below threshold. Persist `reports/ocr_gate_report.*`.
4. Translation gate (Stage 3) → validate language + length + character set; persist `reports/translation_report.*`.
5. Render gate (Stage 5) → validate site integrity and presence of critical files.
6. Orchestrator sequences 1→5, halts on failures, and posts Discord webhook updates (throttled alerts).

## Open items / risks

- OCR gate requires a small curated sample to remain deterministic and cost-effective on CI; must avoid scanning all PDFs.
- Language detection false positives on short abstracts; consider `fasttext` or CLD3 fallback.
- Hardening retry/backoff for Bright Data requests to lower sporadic 429s.
- Avoid exceeding GHA 6‑hour limit; scale via batches/matrix.
- Secret management for Bright Data credentials across fork PRs (avoid secrets exposure).

## Acceptance criteria

1. **Preflight passes/fails deterministically** with clear remediation steps.
2. **Harvest gate** fails on missing/invalid PDFs and fixes URLs.
3. **OCR gate** computes coverage and enforces a floor.
4. **Translation gate** rejects non-English/short/bad translations and outputs quality metrics.
5. **Render gate** assures `site/` has expected artifacts and page integrity.
6. **Orchestrator** blocks downstream stages when upstream fails and reports outcomes.
7. **Discord notifications** reflect success/failure with throttling to avoid spam.
8. **Local reproduction**: all gates runnable locally with sample fixtures, commands documented under `docs/DEVELOPMENT.md`.
9. **Artifacts**: reports uploaded for each gate under `reports/` and accessible via admin dashboard.
10. **Reports mirrored to site** – `site/stats/validation/harvest_report.json` etc.
11. **Gate fixtures + tests** – `scripts/prepare_gate_fixtures.py`, CI workflows seed sample data, and unit tests ensure gates fail on empty inputs.

## Next sprint priorities

1. Finish Stage 2 OCR gate (execution report + validation metrics) and hook into orchestrator.
2. Harden translation queue (Stage 3): worker idempotency, stuck-job reset, per-job cost reporting.
3. Implement Translation QA gate (Stage 4) with language detection + length thresholds.
4. Wire orchestrator gating logic (halt downstream stages on previous failure; poll for completion) and add Discord notifications.
5. Begin structured logging and dashboard upload (Stage 6).

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

<!-- END ORIGINAL CONTENT -->
