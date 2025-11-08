<!-- plan:production-confidence 2025-11-06 -->
# Production confidence dry-run plans

These beads plans decompose `chinaxiv-english-3` (“verify production pipeline via real integration test”) into repeatable sub-plays. Each section maps to an existing BD issue so we can keep the work tree in sync with the tracker while still iterating locally.

## Plan A — Stage 0: Preflight credential + binary validation (`chinaxiv-english-4`)

- **Goal**: Prove the live environment has all required secrets and OCR binaries before we spend real API credits.
- **Prereqs**: `.env` populated with OpenRouter/BrightData/B2/Cloudflare/admin secrets; local data fixtures in `data/` if secrets are withheld.
- **Execution**:
  1. Run `python -m src.tools.env_diagnose --preflight --json reports/preflight_report.json`.
  2. If OpenRouter key is accessible, also run `python -m src.tools.env_diagnose --validate`.
  3. Archive `reports/preflight_report.*` and, if failures occur, log them via `python -m src.tools.b2_alerts add`.
- **Evidence**: Attach the JSON + Markdown reports plus CLI output to the BD issue comment referencing run id/time.
- **Rollback**: Remove transient reports and rotate any failing credentials before retrying; no data side-effects.

## Plan B — Stage 1: Harvest a tiny production sample (`chinaxiv-english-5`)

- **Goal**: Produce a bounded `data/records/chinaxiv_<month>.json` + `data/selected.json` using BrightData and the production selection logic.
- **Prereqs**: Stage 0 success; BrightData creds available.
- **Execution**:
  1. Choose a quiet month (≤10 items) and run `python -m src.harvest_chinaxiv_optimized --month YYYYMM --resume`.
  2. Run `python -m src.select_and_fetch --records data/records/chinaxiv_YYYYMM.json --output data/selected.json --limit 5`.
  3. Persist the selection to B2 via `python .github/scripts/push_selection_to_b2.py --source data/selected.json --label dry-run`.
- **Evidence**: Upload the records + selection JSON paths to B2 (`selections/dry-run/<ts>.json`) and note the object keys inside the BD issue.
- **Rollback**: Delete the dry-run prefix from B2 and remove local selection files if necessary.

## Plan C — Stage 2/3: Translate the sample via OpenRouter (`chinaxiv-english-6`)

- **Goal**: Exercise `src.pipeline --skip-selection --with-qa` with real API calls but small worker counts so we observe retries/cost logging without incurring large spend.
- **Prereqs**: Stage 1 selection in place; OpenRouter key validated.
- **Execution**:
  1. Run `python -m src.pipeline --skip-selection --workers 2 --limit 3 --with-qa --worker-id dry-run-$(date -u +%Y%m%dT%H%M%SZ)`.
  2. If any IDs fail, retry individually via `python -m src.translate <ID> --with-qa --max-retries 2`.
  3. Capture `data/translated/*.json`, `data/flagged/*.json`, `reports/translation_report.*`, and `data/costs/*.json`.
- **Evidence**: Summaries of `_qa_status`, token counts, and failure logs posted to the BD issue; upload artifacts via `bd update --attach`.
- **Rollback**: Delete flagged translations if they contain sensitive text; otherwise keep for analysis.

## Plan D — Stage 4: Gate checks + local render (`chinaxiv-english-7`)

- **Goal**: Prove harvest/OCR/translation/render gates all pass on the dry-run artifacts and that `src.render` produces a navigable site locally.
- **Prereqs**: Stages 0‑3 complete with artifacts stored.
- **Execution**:
  1. Run `python scripts/prepare_gate_fixtures.py` to ensure non-empty fixtures.
  2. Execute `python -m src.validators.harvest_gate --records data/records/chinaxiv_YYYYMM.json`.
  3. Run `python -m src.tools.prepare_ocr_report --limit 3 && python -m src.validators.ocr_gate`.
  4. Execute `python -m src.validators.translation_gate`.
  5. Run `python -m src.render && python -m src.search_index && python -m src.make_pdf`.
  6. Optionally start a local preview via `python -m http.server -d site 8001`.
- **Evidence**: Gate reports under `reports/` + site screenshots; note any failures and fixes in BD.
- **Rollback**: Remove generated PDFs/site outputs if not needed; failures revert to earlier stages.

## Plan E — Stage 5: Publish to B2 + hydrate (`chinaxiv-english-8`)

- **Goal**: Validate the Backblaze round-trip by uploading translations to the `validated/translations/` prefix, wiping local copies, and hydrating back down.
- **Prereqs**: Stage 3 translations exist; B2 credentials available.
- **Execution**:
  1. Run `python -m src.tools.b2_publish --dry-run` first to confirm manifest, then rerun without `--dry-run`.
  2. `rm -rf data/translated && mkdir -p data/translated`, then `python scripts/hydrate_from_b2.py --target data/translated`.
  3. Count hydrated files and compare checksums vs pre-upload copies.
- **Evidence**: Include S3 object keys, counts, and checksum diff notes in BD; keep CLI logs under `reports/b2_hydration.log`.
- **Rollback**: Delete the dry-run prefix in B2 if contamination occurs; restore local translations from git or backups.

## Plan F — Stage 6: Cloudflare preview, cleanup, and reporting (`chinaxiv-english-9`)

- **Goal**: Deploy the hydrated site to a passworded Cloudflare preview, smoke-test `/admin`, and publish a summary of the entire dry run.
- **Prereqs**: Stage 5 hydration succeeded; `CF_API_TOKEN` available.
- **Execution**:
  1. Run `npm install -g wrangler && wrangler pages deploy site --project-name chinaxiv-english --branch dry-run-$(date -u +%Y%m%d)`.
  2. Visit the preview URL + `/admin` using `ADMIN_PASSWORD_HASH` credentials, capture screenshots, and confirm workflow metadata renders.
  3. Aggregate findings (pass/fail, cost, runtime, anomalies) into `reports/dry_run_summary.md`.
  4. Post `bd update chinaxiv-english-3 --comment "$(cat reports/dry_run_summary.md)"` (trimmed if necessary).
- **Evidence**: Preview URL, admin screenshots, summary doc, and any follow-up issues filed for defects.
- **Rollback**: Delete the preview deployment via `wrangler pages deployment delete` or Cloudflare UI; clean up the dry-run branch artifacts in B2.

Run the plans sequentially; if any stage fails, stop, remediate, and update the corresponding BD issue before advancing. This keeps the integration test reproducible while preserving low blast radius.
