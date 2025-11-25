## Translation pipeline notes (Dec 2025)

- Primary model: `openai/gpt-5.1`; deepseek/GLM are manual-only until they can pass strict QA. Grok is disabled.
- Chunking strategy: whole-paper by default; switch to macro-chunks when paragraph count ≥160 or token budget exceeds the guard. Macro chunks target ~20 paragraphs (max 8 chunks), strict `###PARA i###...###END###` numbering, and permit one retry per chunk for count/format mismatches. Paragraph-level fallback is disabled.
- Timeouts: OpenRouter calls use 10s connect / 900s read (15 minutes) to avoid self-imposed timeouts. If a chunk still exceeds this, it fails and retries once; otherwise the run continues with padded blanks for that chunk.
- OCR/extraction: always run sidecar OCR (`ocrmypdf --force-ocr --language chi_sim+eng --sidecar <pdf>.txt <pdf> <pdf>`) so extraction reads the sidecar and merges short fragments. Sample paper `chinaxiv-202510.00167` yields ~178 paragraphs after cleaning.
- QA: structural gate enforces paragraph counts and short-fragment ratios. QA filter requires zero Chinese characters; residual ideographs are stripped before QA, then a targeted retry runs once. Persistent QA failures are dumped to `reports/raw_translations/<paper_id>.qa_failed.json` for manual review. Formatting runs only after QA pass.
- Artifacts: translations live in `data/translated/<paper_id>.json` plus `.md`; run summaries at `reports/run_summaries/<paper_id>.json`; raw chunk dumps at `reports/raw_translations/`.
- Wall-clock guard: per-paper hard cap (config `paper_wallclock_limit_seconds`, default ~40m). If exceeded, the run stops and logs, to avoid batch hangs.
- DeepSeek is unreliable via OpenRouter (malformed/non-JSON responses). Keep it off by default. If testing for cost, gate behind a canary with immediate fallback to GPT-5.1 on any error/QA fail.
- Canary CI: `translation-canary.yml` runs daily on 2–3 papers and fails if PDF fetch or QA fails. Ensure secrets are present and `NO_PROXY` excludes OpenRouter.
- PDF fetch order: direct → Unlocker → headless. Leave `BRIGHTDATA_BROWSER_WSS` unset unless headless is explicitly needed.
- Maintenance: prune macro chunk cache with `python scripts/prune_macro_cache.py --days 7`; run logged backfills with `scripts/run_backfill_batch.sh <ids...>` (logs to `reports/backfill_runs`).
