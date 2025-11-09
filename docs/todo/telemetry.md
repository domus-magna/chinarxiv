# Telemetry Proposal: Logs, Metrics, Analysis, and UI

## Goals
- Reliable visibility into translation health, OpenRouter errors/costs, and site performance.
- Minimal noise in Discord; primary visualization in Grafana.
- Lightweight implementation that works locally and in CI.

## Scope (MVP → Nice-to-have)
- MVP
  - Structured JSONL logs for key events and OpenRouter errors.
  - Basic Prometheus metrics for counts, latencies, and error rates.
  - Grafana dashboards for overview and OpenRouter health.
  - Alerts in Grafana (email/Slack) for sustained issues; keep Discord for critical auth/payment errors only.
- Next
  - Log shipping via Promtail → Loki for rich log search.
  - Cost tracking panels and rollups by day/model.
  - Error budget visualization (rolling 24h).

## Architecture Overview
- Logs: JSON Lines on disk under `data/telemetry/` with daily rotation.
- Metrics: Prometheus/OpenMetrics exposed by a lightweight `/metrics` endpoint (Flask in `src/monitor.py`).
- Shipping/Storage:
  - Logs → Promtail → Loki (optional locally; production-ready if deployed).
  - Metrics → Prometheus server scrape (optional locally; production-ready if deployed).
- Visualization: Grafana dashboards (local or managed).

## Data Model
### Log Events (JSONL)
Write one JSON per line. Files rotate daily (UTC).
- Path: `data/telemetry/events-YYYY-MM-DD.jsonl`
- Common fields
  - `ts`: ISO timestamp
  - `level`: info|warning|error|critical
  - `evt`: event type (e.g., `openrouter_error`, `pipeline_stage`, `formatting_error`)
  - `source`: module (e.g., `translation_service`, `formatting_service`, `pipeline`)
  - `msg`: human-readable summary
  - `meta`: object with structured details

Examples
```json
{"ts":"2025-10-06T13:43:10Z","level":"error","evt":"openrouter_error","source":"translation_service","msg":"429 Rate limit","meta":{"status":429,"code":"rate_limited","model":"deepseek/deepseek-v3.2-exp"}}
{"ts":"2025-10-06T13:45:02Z","level":"info","evt":"pipeline_stage","source":"pipeline","msg":"Rendered site","meta":{"items":120}}
```

### Metrics (Prometheus)
Expose via `/metrics` in `src/monitor.py`.
- Counters
  - `openrouter_errors_total{status,code,component,model}`
  - `translations_total{component}`
  - `pipeline_runs_total{result}`
- Gauges
  - `queue_pending_jobs`
  - `daily_cost_usd{model}`
- Summaries/Histograms
  - `translation_latency_seconds`
  - `openrouter_call_latency_seconds`

## Implementation Plan
Phase 1 (MVP)
1) Logging utilities
   - Add `telemetry_logger.py` with `log_event(evt, level, source, msg, meta)` to append to JSONL with daily rotation.
   - Call sites:
     - On OpenRouter failures in `translation_service` and `formatting_service`.
     - On pipeline start/end, and render/index completion.
2) Metrics endpoint
   - Add Prometheus client and `/metrics` route to `src/monitor.py`.
   - Increment metrics at same call sites as logs.
3) Dashboards
   - Create a basic Grafana dashboard JSON under `docs/dashboards/` with:
     - OpenRouter Error Rate (per status/code)
     - Translations per minute
     - P95 openrouter_call_latency_seconds
     - Daily cost by model (from metric or cost log rollup)

Phase 2 (Shipping + Storage)
4) Loki/Promtail (optional but recommended)
   - Add `docs/todo/promtail-config-example.yml` showing how to scrape `data/telemetry/*.jsonl`.
   - Label logs with `{job="chinaxiv-telemetry", env="local"}`.
5) Prometheus (optional but recommended)
   - Add `docs/todo/prometheus-scrape-example.yml` scraping `http://localhost:5000/metrics`.

Phase 3 (Cost + Budgets)
6) Cost Rollups
   - Periodic task to aggregate costs from `data/costs/*.json` into metrics: `daily_cost_usd{model}`.
7) Alerts in Grafana
   - Alert on sustained 429 rate_limit > threshold over 15 min.
   - Alert on 401 invalid key or 402 payment required (single-shot, high priority).
   - Alert on translation_latency_seconds P95 > Xs for 15 min.

## Analysis Workflows
- LogQL (Loki)
  - Count rate-limits by model:
    - `{job="chinaxiv-telemetry"} | json | evt="openrouter_error" and code="rate_limited" | unwrap ts | count_over_time(1m)`
  - Trace a failed pipeline window:
    - `{job="chinaxiv-telemetry"} | json | evt="pipeline_stage" | line_format "{{.ts}} {{.msg}} {{.meta}}"`
- PromQL (Prometheus)
  - Error rate per status:
    - `sum(rate(openrouter_errors_total[5m])) by (status)`
  - Latency P95:
    - `histogram_quantile(0.95, sum(rate(openrouter_call_latency_seconds_bucket[5m])) by (le))`
  - Cost by model (daily):
    - `sum(daily_cost_usd) by (model)`

## UI Plan (Grafana)
Dashboards
- Translation Pipeline Overview
  - Panels: Translations/min, OpenRouter error rate by status, P95 latency, Daily cost by model, Queue pending
- OpenRouter Health
  - Panels: Status breakdown (429/5xx), Top error codes, Call latency histogram, Success vs failure rate
- Costs & Budgets
  - Panels: Daily cost over time, Cost by model, Rate-limit budget usage (429), Auth/payment incidents (401/402)

Navigation
- Dashboard links from the admin monitor (`python -m src.monitor`) to Grafana URL.
- Keep Discord for critical auth/payment errors only.

## Local Dev Quickstart
1) Enable metrics endpoint (Flask monitor is already present); add `/metrics`.
2) Start monitor: `python -m src.monitor` (exposes metrics).
3) Optional: run Prometheus (scrape `/metrics`) and Grafana locally via Docker Compose.
4) Optional: run Loki + Promtail to ship `data/telemetry/*.jsonl`.

## Security & Hygiene
- Do not log secrets; redact `Authorization` headers and API keys.
- Keep retention tight locally (e.g., 7–14 days) to avoid large diffs.
- Make shipping optional; default to local files only.

## Risks & Mitigations
- Risk: Over-alerting → Use Grafana alerts with sensible thresholds and durations; keep Discord minimal.
- Risk: Storage growth → Use rotation and retention; compress old logs if needed.
- Risk: Vendor lock-in → Use open standards (JSONL, OpenMetrics, PromQL/LogQL).

## Deliverables
- `telemetry_logger.py` utility.
- `/metrics` endpoint in `src/monitor.py`.
- Example configs: Promtail and Prometheus under `docs/todo/`.
- Grafana dashboard JSON(s) under `docs/dashboards/`.
