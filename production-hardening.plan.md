<!-- plan:production-hardening 2025-11-09 -->
# Production hardening plan

This beads-aligned plan captures the reliability fixes identified during the production readiness audit and ties them back to the two open P1 issues (`chinaxiv-english-3` for end-to-end validation, `chinaxiv-english-10` for automation hygiene). Each segment calls out prerequisites, concrete steps, required evidence, and rollback notes so future runs stay deterministic.

## Plan 1 — Fail closed when translations do not run (`chinaxiv-english-3`)

- **Goal**: Ensure nightly builds fail fast when `src.pipeline` attempts a translation run but produces zero successful outputs (avoids silent stale publishes).
- **Prereqs**: OpenRouter key validated; Backblaze hydrated selection present.
- **Execution**:
  1. Teach `src.pipeline` to emit a summary JSON (count success/fail/qa) to `reports/pipeline_summary.json`.
  2. Update `.github/workflows/build.yml` to export that count and `exit 1` when `SKIP_HARVEST=false` but zero items succeeded.
  3. Wire a matching Discord/B2 alert so misses are visible even when GH notifications are muted.
- **Evidence**: Workflow run log showing the new summary + failure condition; attached `reports/pipeline_summary.json`.
- **Rollback**: Guard the failure with `TRANSLATION_OPTIONAL=true` env so we can temporarily bypass if OpenRouter is degraded.

## Plan 2 — Detect selection regressions instead of writing `[]` (`chinaxiv-english-3`)

- **Goal**: Prevent the selector from silently producing empty output when harvest data exists.
- **Prereqs**: Harvest JSON(s) present in `data/records/`, `python -m src.select_and_fetch` runnable.
- **Execution**:
  1. Add an `--allow-empty-selection` flag to `src.pipeline` (default false).
  2. When the selector fails and the flag is false, raise an exception and call `python -m src.tools.b2_alerts add "selection failed"`.
  3. Only the dry-run/tests set the flag true to keep today’s fixtures unblocked.
- **Evidence**: Unit test covering the hard failure plus workflow snippet showing the alert when no data exists.
- **Rollback**: Set the workflow flag/environment to temporarily allow the empty fallback if we intentionally deploy a shell site.

## Plan 3 — Enforce hydration parity with manifests (`chinaxiv-english-10`)

- **Goal**: Ensure the number of hydrated translations matches what `src.tools.b2_publish` recorded before rendering/deploying.
- **Prereqs**: B2 credentials configured; publish manifest available (e.g., `data/manifests/validated.json`).
- **Execution**:
  1. Extend `scripts/hydrate_from_b2.py` to accept a manifest file (path via env) and verify counts/checksum.
  2. Update `build.yml` to download the manifest artifact and fail the job if counts mismatch; send Discord alert via `b2_alerts`.
  3. Print both manifest + hydrated counts for debugging.
- **Evidence**: CI log excerpt demonstrating the comparison and a failing sample when counts diverge (manually simulate).
- **Rollback**: Use a `--skip-parity-check` option to bypass when intentionally hydrating partial data.

## Plan 4 — Protect local data when running `make dev` (`chinaxiv-english-3`)

- **Goal**: Stop `make dev` from deleting hydrated translations/site assets unintentionally.
- **Prereqs**: Make utility available; `.env` optionally loaded.
- **Execution**:
  1. Add `DEV_ALLOW_CLEAN=1` gate (default off). If not set and `data/translated` or `site/` exist, abort with instructions.
  2. Document the flag in `README.md` (dev section) and `Makefile` comments.
  3. Optionally provide `make dev-clean` alias that sets the flag internally.
- **Evidence**: New make target plus README blurb; local log screenshot showing the guard preventing deletion.
- **Rollback**: Set `DEV_ALLOW_CLEAN=1` in shell or remove the guard if we later adopt a different workflow.

## Plan 5 — Emit nightly run summaries (`chinaxiv-english-10`)

- **Goal**: Publish a compact JSON + Discord line summarizing processed count, failures, QA stats so operators can detect silent stalls.
- **Prereqs**: Existing `tests/test_monitoring_real.py` helper; Discord webhook configured.
- **Execution**:
  1. Promote the helper into `src/monitoring.py` (or reuse existing) to generate `{processed, flagged, qa_pass_rate, duration}`.
  2. After render but before deploy, run a new script (e.g., `python scripts/publish_run_summary.py`) that reads `reports/pipeline_summary.json` and `b2_publish` metadata, uploads JSON to B2 (`reports/run-summary/<run_id>.json`), and posts Discord alert.
  3. Attach summary artifact to the workflow for manual inspection.
- **Evidence**: Workflow log/pasted Discord message plus stored JSON artifact.
- **Rollback**: Hide behind `EMIT_RUN_SUMMARY=false` env to disable quickly if alerts become noisy.

Run these segments sequentially; update the linked BD issues after each stage and capture evidence inside `reports/`. This keeps the implementation tightly scoped while enforcing the audit’s simplicity-first guardrails.

