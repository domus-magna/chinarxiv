# 🚨 CRITICAL: USE BD AT START OF EVERY TASK! 🚨
#
# BEFORE YOU DO ANYTHING ELSE - STOP AND RUN: `bd ready`
# This shows you EXACTLY what to work on next. Dependencies matter!
# Don't guess - let bd tell you what's actually ready. This prevents:
# ❌ Wasted time on blocked tasks
# ❌ Missing critical dependencies
# ❌ Context switching chaos
# ✅ Crystal clear priorities
# ✅ Smooth dependency flow
# ✅ Organized, predictable progress
#
# Make this your unbreakable habit: TASK → `bd ready` → WORK → `bd update`
# ======================================================================
#
# 📣 Non-negotiable BD Workflow (no exceptions)
# - Run `bd ready` before touching any file. If it reports a block, stop—pushing forward creates rework.
# - Run `bd update` the moment you finish so the next agent inherits fresh context.
# - We log BD misses; repeat offenders trigger remediation because they break dependency planning and waste API credits.
#
# Repository Guidelines

## 🎯 Critical Development Philosophy (Read First!)

### Simplicity-First Design Philosophy (Critical)

**Always seek out the simplest and most maintainable implementation first.** This is a core principle that must guide all technical decisions and proposals.

**Overengineering Prevention Checklist:**
- Before proposing any solution, ask: "What is the simplest approach that solves this problem?"
- Challenge every component: "Is this really necessary, or can we achieve the same result with less complexity?"
- Prefer in-place modifications over new services, classes, or modules
- Choose hardcoded values over configuration when the complexity isn't justified
- Use basic error handling over sophisticated retry mechanisms unless proven necessary
- Implement monitoring and alerting only when the problem justifies the infrastructure

**Complexity Red Flags to Avoid:**
- Separate services for single-purpose functionality
- Multiple configuration parameters for simple features
- Sophisticated state management for straightforward operations
- Circuit breakers, retry mechanisms, or monitoring for basic functionality
- Context-aware logic when simple rules suffice
- Multiple validation layers when one is sufficient

**When Complexity is Justified:**
- The problem genuinely requires sophisticated solutions (e.g., distributed systems, high availability)
- The complexity provides measurable value that outweighs maintenance costs
- The solution is proven to be necessary through real-world usage
- The complexity is isolated and doesn't affect other parts of the system

## 📋 Essential Commands (Quick Reference)

### Daily Development Workflow
- **Start work**: `bd ready` (check what tasks are unblocked)
- **Environment**: `python -m venv .venv && source .venv/bin/activate`
- **Install deps**: `pip install -r requirements.txt`
- **Run tests**: `python -m pytest tests/ -v`
- **Self-review**: `make self-review` (run before marking tasks complete)
- **Local preview**: `python -m http.server -d site 8001`

### Admin CI Dashboard (Local)
- **Start admin**: `make admin`
  - Reads `.env` first so checks see values.
  - Required in `.env`: `ADMIN_PASSWORD_HASH` (preferred) or `ADMIN_PASSWORD` (legacy), `GH_TOKEN` (repo+workflow), `GH_REPO` (e.g., `owner/repo`).
  - Generate a hash: `python - <<'PY'\nfrom werkzeug.security import generate_password_hash; print(generate_password_hash(input('Password: ')))\nPY` then set `ADMIN_PASSWORD_HASH=...` in `.env`.
- **Endpoints** (password-protected; any username + `ADMIN_PASSWORD`):
  - `/admin` — Home: quick links, basic metrics (stacked), recent runs with timestamps and durations.
  - `/admin/ci/workflows` — Workflows list with natural‑language descriptions; dispatch UI for `workflow_dispatch` (Quick Actions). Non-dispatchable workflows listed read-only.
  - `/admin/ci/runs` — Recent runs.
  - `/admin/ci/run/<id>` — Run details: jobs, artifacts, JSON previews for gate reports. Shows local times and durations.
  - `/favicon.ico` — 204 (no 404 noise).
  - Root `/` redirects to `/admin`.
  - Claude-related automation is hidden from lists.

### Durable Storage (Backblaze B2)
- We persist pipeline outputs to a private Backblaze B2 bucket via the S3‑compatible API.
- Phase A (enabled): JSON only — harvest records, per-paper translations, and per-run selection sets.
- Phase B (optional): PDFs (archival only; never publicly served). Controlled by a workflow input/flag when enabled.
- Bucket layout (keys):
  - `records/chinaxiv_YYYYMM.json`
  - `translations/{paper_id}.json`
  - `selections/{run_id}/selected.json`
  - `pdfs/{paper_id}.pdf` (Phase B only)

### Required GitHub Secrets (CI)
- Existing: `CF_API_TOKEN`, `OPENROUTER_API_KEY`, `BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE`, `BRIGHTDATA_UNLOCKER_ZONE`, `BRIGHTDATA_UNLOCKER_PASSWORD`, `DISCORD_WEBHOOK_URL`.
- New (Backblaze B2):
  - `BACKBLAZE_KEY_ID` — B2 S3 key ID
  - `BACKBLAZE_APPLICATION_KEY` — B2 S3 application key
  - `BACKBLAZE_S3_ENDPOINT` — e.g., `https://s3.us-west-004.backblazeb2.com`
  - `BACKBLAZE_BUCKET` — e.g., `chinaxiv-pipeline`
  - `BACKBLAZE_PREFIX` — optional prefix like `prod/`

Notes
- We still avoid committing large artifacts to git. Only `data/seen.json` is committed for cross-run dedupe.
- GitHub Artifacts remain enabled as a safety net for harvest JSONs and selections (90‑day retention), but B2 is the durable source of truth.

### Pipeline Operations
- **Harvest**: `python -m src.harvest_chinaxiv_optimized --month $(date -u +"%Y%m")`
- **Translate**: `python -m src.translate --dry-run`
- **Render**: `python -m src.render && python -m src.search_index`
- **Background tasks**: `nohup command &` (see Background Task Guidelines)
- **Seed validation fixtures**: `python scripts/prepare_gate_fixtures.py` (populates sample harvest/translation artifacts when `data/` is empty so the CI gates never pass on empty input)
- **Regenerate OCR fixture PDF** (when real data prevents auto-seeding): 
  ```bash
  python - <<'PY'
  from scripts.prepare_gate_fixtures import generate_scanned_pdf, REPO_ROOT
  generate_scanned_pdf(REPO_ROOT / "data/pdfs/sample.pdf")
  PY
  rm -f data/pdfs/chinaxiv-202501.00001.pdf
  ```
  This recreates the synthetic `sample.pdf` so `src.tools.prepare_ocr_report` has a valid local asset even when production PDFs are absent or broken.

### Translation defaults (Dec 2025)
- Default model `openai/gpt-5.1`; Grok is disabled. Formatting falls back to `google/gemini-2.5-flash-preview-09-2025`.
- Whole-paper only; paragraph-level fallback is forbidden. Large docs auto-switch to macro chunks (~20 paras, max 8 chunks, 1 retry) with strict `###PARA i###...###END###` numbering.
- Timeouts: 10s connect / 900s read (15m) to avoid self-imposed timeouts; we prefer reliability over latency. If a chunk still fails count checks after retries, we pad blanks and continue so the run completes. Per-paper wall-clock guard ~40m.
- QA must pass zero-Chinese and paragraph-count gates; residual Chinese/metadata is stripped before QA with one targeted retry. QA failures are stored in `reports/raw_translations/`.
- Default PDF fetch prefers Unlocker; headless is optional. Leave `BRIGHTDATA_BROWSER_WSS` unset unless you need headless. Order: direct → Unlocker → headless.
- DeepSeek via OpenRouter is currently unreliable (malformed/non-JSON responses); keep it off by default. If you test it, gate behind canary+immediate fallback to GPT-5.1.
- Daily canary CI (`translation-canary.yml`) runs 2–3 papers with QA enforcement; alerts via workflow failure if PDF fetch/QA breaks. Keep `NO_PROXY=openrouter.ai,api.openrouter.ai` in env.

### Maintenance helpers
- Prune macro chunk cache: `python scripts/prune_macro_cache.py --days 7` (default 7 days).
- Backfill runner with logging: `scripts/run_backfill_batch.sh <paper_id...>` writes logs to `reports/backfill_runs/backfill_<timestamp>.log`.

### Pipeline Workflow Reference (GitHub Actions)
Each CI/CD workflow below must stay in sync with our Backblaze-first source of truth and Cloudflare deploy target. If a workflow diverges from the description, fix the workflow (preferred) or immediately update this section (minimum) so later agents do not inherit stale guidance.

**Production build flows**
- `build-and-deploy` (`.github/workflows/build.yml`): Nightly 03:00 UTC plus push/PR/manual triggers. Installs deps, optionally harvests current + previous month, merges/ selects records, persists selections to B2 (including `latest.json` pointer), runs translation pipeline with QA, publishes validated + flagged translations/PDF manifests to B2, hydrates validated translations back before running render/search-index/PDF, and deploys to Cloudflare Pages.
- `backfill-month` (`backfill.yml`): Manual month-specific run. Harvests the requested `YYYYMM` if records missing, selects unseen items, uploads the selection to B2 (optionally skipping `latest`), runs translation with configurable worker count, publishes artifacts to B2, hydrates validated translations, and (when `deploy=true`) renders/search-indexes/PDFs before deploying.
- `rebuild-from-b2` (`rebuild-from-b2.yml`): Manual (optional scheduled) workflow that wipes `data/translated`, hydrates validated translations from B2, renders/search indexes/makes PDFs, then deploys via Wrangler without running harvest or translation.

**Gate + orchestrator flows**
- `validation-gate.yml`: Reusable workflow call template that installs OCR tooling, runs optional `pre_command`, executes the gate command, uploads artifacts, and optionally runs `post_command`. All gate workflows below delegate here.
- `preflight` (`preflight.yml`): Runs `python -m src.tools.env_diagnose --preflight`, uploading JSON/MD diagnostics. Fails fast when required secrets/binaries are missing.
- `harvest-gate` (`harvest-gate.yml`): Seeds fixtures, optionally accepts `records_path`, then runs `src.validators.harvest_gate`, uploading harvest reports and posting a Discord summary via `scripts/post_harvest_summary.py`.
- `ocr-gate` (`ocr-gate.yml`): Seeds fixtures, runs `src.tools.prepare_ocr_report --limit 3` to generate execution data, and validates OCR health via `src.validators.ocr_gate`.
- `translation-gate` (`translation-gate.yml`): Seeds fixtures and executes `src.validators.translation_gate`; supports matrix inputs for orchestrated load.
- `render-gate` (`render-gate.yml`): Seeds fixtures, optionally hydrates validated translations from B2 before rendering/search-index/PDF, then runs `src.validators.render_gate`.
- `pipeline-orchestrator` (`pipeline-orchestrator.yml`): Manual dispatcher that sequences any subset of stages (`preflight,harvest,ocr,translate,qa,render`). It invokes the gate workflows above (and translation gate in matrix mode), tracks completion, and notifies Discord on success/failure.

**Translation execution & queue health**
- `batch_translate` (`batch_translate.yml`): Cloud-mode worker that installs OCR deps, validates OpenRouter, pulls queue state, runs `src.pipeline --cloud-mode --with-qa` for a configurable batch/worker count, commits queue progress, and uploads flagged outputs.
- `test_batch_trigger` (`test_batch_trigger.yml`): Manual 50-paper batch run on 8-core runners; mirrors `batch_translate` but with fixed limits to validate queue settings.
- `batch_test` (`batch_test.yml`): Lightweight workflow for verifying dispatch inputs only; prints the requested batch size.
- `smoke-translate` (`smoke-translate.yml`): Manual smoke test that can harvest a month if missing, runs `scripts/smoke_translate.py` for a small sample, and optionally deploys the rendered site.
- `integration-translate` (`integration-translate.yml`): Manual targeted translator that selects IDs from `data/records/<run_id>.json`, prepares `data/selected.json`, and runs `src.pipeline --with-qa` for a bounded sample while collecting artifacts (including failure logs).
- `translate_orchestrator` (`translate_orchestrator.yml`): CLI-driven loop that repeatedly dispatches `batch_translate.yml` runs until the cloud queue empties (or a fixed number of batches completes), polling queue stats between runs and optionally triggering QA report + site rebuild at the end.
- `translation-orchestrator` (`translation-orchestrator.yml`): Backfill dispatcher that iterates over a month range and fires `backfill.yml` for each via the GitHub API, respecting requested parallelism, worker counts, and deploy toggles.
- `queue-maintenance` (`queue-maintenance.yml`): Nightly job that runs `src.tools.compact_cloud_queue --retain-completed 100`, committing updates to `data/cloud_jobs*.json` to keep queue files manageable.

**Quality reporting + ad-hoc testing**
- `qa_report` (`qa_report.yml`): Daily/manual job that counts passed vs flagged translations, raises GitHub issues if pass rate <90%, writes `data/qa_report.md`, and uploads the report artifact.
- `test_dispatch` (`test_dispatch.yml`): Minimal “hello world” dispatcher used to verify workflow dispatch plumbing.
- `smoke-translate` artifacts plus `qa_report` outputs feed manual QA reviews; always capture run links.

**Automation assist**
- `claude-code-review` (`claude-code-review.yml`): Runs Anthropics’ CLAUDE review action on PR open/update events when `CLAUDE_CODE_ENABLED` and tokens are present.
- `claude` (`claude.yml`): ChatOps hook that lets contributors summon Claude via `@claude` mentions across issues, PR comments, and reviews; ensures the bot can read CI status when granted `actions: read`.

Keep `.github/workflows/validation-gate.yml` in sync with any new system dependencies; every gate inherits its install block, so mismatched packages there manifest everywhere.

### Troubleshooting
- **API keys**: `python -m src.tools.env_diagnose --check`
- **Check status**: `python scripts/monitor.py`
- **View logs**: `tail -f data/*.log`

## 🏗️ Project Structure & Module Organization
- Root: docs/PRD.md (product spec), README.md.
- Source: `src/` (e.g., `harvest_oai.py`, `licenses.py`, `translate.py`, `render.py`, `search_index.py`, `utils.py`).
- Data: `data/` (e.g., `raw_xml/`, `seen.json`). Do not commit secrets or large artifacts.
- Site output: `site/` (static HTML, assets, search-index.json).
- Assets: `assets/` (CSS, JS, logos, MathJax, MiniSearch/Lunr).

## Agent Communication Standards

### Response Style & Depth (Required)

This project requires agents to communicate in full, detailed prose that prioritizes clarity over brevity. Use complete sentences and cohesive paragraphs to explain decisions, call out assumptions, and describe tradeoffs with practical impact. Bulleted summaries are welcome for scanability, but they must be supported by descriptive prose. The goal is for a teammate to understand not only what will be done, but why it is the right choice given our constraints.

**Implementation Guidelines:**
- Start with the simplest possible solution
- Add complexity only when the simple solution fails
- Document why each piece of complexity is necessary
- Provide clear rollback paths for any complex features
- Test simple solutions thoroughly before considering complexity

**Self-Review Process (Required):**
Before marking any task as complete, run `make self-review` to apply structured overengineering prevention:
- Review solutions for unnecessary complexity
- Identify simpler approaches that solve 90% of the problem
- Check for potential bugs and edge cases
- Look for optimization opportunities
- Validate against simplicity principles

**Automatic Trigger:**
The self-review process is automatically enforced via git pre-push hooks:
- Runs before `git push` if self-review hasn't been completed in the last hour
- Prompts to run self-review if needed, or allows skipping for CI/CD
- Use `make self-review-status` to check if review is current
- Use `make self-review-skip` for manual override when needed

**CI/CD Integration:**
For automated systems, use: `./scripts/git-push-ci.sh` to skip self-review checks

This process catches overengineering before it becomes technical debt.

**What to include in most responses:**

1) **Context and assumptions** - Briefly restate the problem in your own words and list any assumptions, constraints, or prerequisites that shape the solution (e.g., cost ceilings, CI limits, data availability, external API quotas).

2) **Options considered with tradeoff analysis** - Present realistic alternatives (including "do nothing" when applicable). For each option, explain pros and cons across: correctness/completeness, performance, cost, reliability, maintainability, operational complexity, and risk. Call out edge cases, failure modes, and how we would monitor/mitigate them.

3) **Clear recommendation and rationale** - State your recommended option and why it best fits our goals. Note what would change the decision (decision gates) and how to reverse it (rollback/escape hatch) if needed.

4) **Concrete next steps** - Provide specific commands, files to edit, and checkpoints for verification. For any long-running activity, explicitly run it in the background and show how to monitor it (see Background Task Guidelines).

**When to be brief:** If the user explicitly requests a short or one-line answer, comply but include a single sentence acknowledging key tradeoffs or note that no material tradeoffs exist for the action.

**Formatting guidance:**
- Prefer paragraphs for explanation; use bullets to summarize or enumerate choices.
- Reference concrete file paths, scripts, and commands (e.g., `src/harvest_chinaxiv_optimized.py`, `make dev`).
- Avoid unexplained jargon and shorthand. If you introduce a term (e.g., "smart mode"), define it and explain why it exists.
- If you're changing defaults or behavior, describe impacts on CI, cost, and developer workflow.


## Coding Style & Naming Conventions
- Python 3.11+, 4-space indent, PEP 8 + type hints.
- Names: modules/functions `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE`.
- Formatting: prefer Black (line length 88) + Ruff + isort. Example: `ruff check src && black src`.
- Keep functions small; pure helpers in `utils.py`.
- Preserve math/LaTeX tokens exactly (see PRD “Math/LaTeX preservation”).

## Testing Guidelines
- Framework: pytest (+ pytest-cov).
- Location/pattern: `tests/test_*.py`; mirror module names.
- Targets: unit tests for parsing, masking/unmasking, license gate; smoke test for end-to-end build on 1–2 items.
- Coverage: aim ≥80% on core text/masking utilities.

### Test Commands
- **Run all tests**: `python -m pytest tests/ -v --tb=short`
- **Run specific test file**: `python -m pytest tests/test_translate.py -v`
- **Run with coverage**: `python -m pytest tests/ --cov=src --cov-report=term-missing`
- **Run E2E tests**: `python -m pytest tests/test_e2e_simple.py -v`
- **Quick test run**: `python -m pytest tests/ -q`

## Commit & Pull Request Guidelines
- Commits: Conventional Commits (e.g., `feat:`, `fix:`, `chore:`). Example: `feat(translate): mask inline math tokens`.
- PRs: concise description, linked issue/PRD section, before/after screenshots for HTML changes, notes on perf/cost impact, and manual test steps.
- Keep PRs small and focused; include `requirements.txt`/config updates when relevant.

## Security & Configuration Tips
- Secrets: set `OPENROUTER_API_KEY` and BrightData creds (`BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE`, `BRIGHTDATA_UNLOCKER_ZONE`, `BRIGHTDATA_UNLOCKER_PASSWORD`) in CI; never commit keys.
- Config: `src/config.yaml` defines model slugs, glossary, and optional proxy settings. BrightData creds are read from `.env` or CI env.
- Data hygiene: limit `data/raw_xml/` retention; avoid large diffs in VCS.

### Bright Data Headless Browser Automation
- New env var: `BRIGHTDATA_BROWSER_WSS` points to the Bright Data Scraping Browser websocket. Local `.env` now carries the live endpoint `wss://brd-customer-hl_7f044a29-zone-china_browser1:4gi6qln6j62k@brd.superproxy.io:9222`; `.env.example` documents the placeholder. Keep it synced with GitHub secrets whenever rotated.
- Playwright hookup: the docs at `https://docs.brightdata.com/integrations/playwright` stress launching Chromium with the Bright Data proxy credentials, passing `proxy={'server': 'http://HOST:PORT', 'username': 'USER-session-<id>', 'password': 'PASS'}`, and setting `ignoreHTTPSErrors=True` (or installing their CA) when using residential/Unlocker zones. Our Python helper does the equivalent by connecting over CDP to the websocket and injecting referer/user-agent headers before fetching the PDF.
- Puppeteer hookup mirrors the same flow per `https://docs.brightdata.com/integrations/puppeteer`: add `--proxy-server=HOST:PORT` when launching, then call `page.authenticate({ username, password })`. Both guides emphasize copying Host/Port/User/Pass from the Bright Data dashboard, targeting `https://geo.brdtest.com/welcome.txt` for proxy tests, and using ISP/Data Center zones for browser automation (residential requires compliance review or CA install).
- Session stickiness: docs note that Bright Data rotates IPs by default; add `-session-<paper_id>` to usernames (or pass `session_id` into our downloader) anytime you need per-paper sticky cookies.
- Usage: the downloader now tries native requests → Playwright headless (`BRIGHTDATA_BROWSER_WSS`) → Web Unlocker raw API. When debugging fetches manually, you can also connect from Node via `puppeteer.connect({ browserWSEndpoint: process.env.BRIGHTDATA_BROWSER_WSS })` or from Python via `playwright.sync_api.sync_playwright().chromium.connect_over_cdp(os.environ['BRIGHTDATA_BROWSER_WSS'])` and then run interactive commands against the live browser.

### LLM API Key Troubleshooting (Agents)
- Symptoms:
  - `OPENROUTER_API_KEY not set` raised by code, or OpenRouter `401 User not found` in responses.
- **NEW: Automatic Environment Resolution**
  - The system now automatically detects and resolves shell/.env mismatches
  - Use `python -m src.tools.env_diagnose --check` to detect mismatches
  - Use `python -m src.tools.env_diagnose --resolve` to fix mismatches
  - Use `python -m src.tools.env_diagnose --validate` to test API keys
- **Manual Troubleshooting** (if automatic resolution fails):
  - Shell: `echo $OPENROUTER_API_KEY` should print a non-empty value.
  - Python (within the same shell): `python3 -c "import os; print(os.getenv('OPENROUTER_API_KEY'))"`.
  - If empty, load `.env` or export the key: `export OPENROUTER_API_KEY=...`.
  - Our client auto-loads `.env` via `openrouter_headers()`; ensure you are running from repo root where `.env` resides.
- CI/GitHub Actions:
  - Confirm `OPENROUTER_API_KEY` secret is configured and passed to the job environment.
- If using a proxy or different shells/terminals, make sure the key is present in the active session before running any `src.translate` or `src.tools.formatting_compare` commands.

## Data Source
- Direct ChinaXiv scraping via BrightData is the default. OAI-PMH remains blocked; Internet Archive removed.

## Live Configuration & Deployment

### Current Status
- **Translation Pipeline**: ✅ Working (fixed API key bug in workers)
- **GitHub Actions**: ✅ Configured for Cloudflare Pages deployment
- **Batch Translation**: ✅ Ready for parallel processing
- **Donation System**: ✅ Crypto donation page implemented
- **UI Improvements**: ✅ Cleaner navigation and layout

### GitHub Actions Workflows
- **Daily Build** (`.github/workflows/build.yml`): Runs at 3 AM UTC, harvests current + previous month (optimized), selects unseen items, translates, publishes to B2, then hydrates validated translations from B2 → render → search index → PDFs → deploy. This guarantees the site reflects the canonical data in B2.
- **Configurable Backfill** (`.github/workflows/backfill.yml`): Translates a specific month, publishes to B2, then (when `deploy=true`) hydrates from B2 and rebuilds the site before deploy.
  - Both workflows persist `data/seen.json` by committing it back to the repository, ensuring cross-job deduplication.
- **Rebuild from B2** (`.github/workflows/rebuild-from-b2.yml`): Minimal workflow to hydrate from B2 and redeploy without harvesting/translating. Trigger via `workflow_dispatch` from the Admin UI or GitHub UI.

### Manual Backfill (On Demand)
- **Harvester**: `python -m src.harvest_chinaxiv_optimized --month YYYYMM --resume` (run newest→oldest across months; background long runs with `nohup ... &`).
- **Select**: Merge harvested months to a single records file, then `python -m src.select_and_fetch --records <merged>.json --output data/selected.json`.
- **Translate (parallel)**: `jq -r '.[].id' data/selected.json | xargs -n1 -P 20 -I {} sh -c 'python -m src.translate "{}" || true'`.
- **Render + Index + PDFs**: `python -m src.render && python -m src.search_index && python -m src.make_pdf`.
 - **Persist dedupe**: Commit `data/seen.json` after successful runs to avoid reprocessing in subsequent jobs.

### Required GitHub Secrets
- `CF_API_TOKEN`: Cloudflare API token with Pages:Edit permission
- `CLOUDFLARE_ACCOUNT_ID`: Cloudflare Account ID
- `OPENROUTER_API_KEY`: OpenRouter API key for translations
- `BRIGHTDATA_API_KEY`: BrightData API key (harvest)
- `BRIGHTDATA_ZONE`: BrightData zone name (harvest)
- `BRIGHTDATA_UNLOCKER_ZONE`: BrightData Unlocker zone for downloader fallback
- `BRIGHTDATA_UNLOCKER_PASSWORD`: Unlocker zone password
- `DISCORD_WEBHOOK_URL`: Discord webhook for notifications (optional)

### GitHub Account Policy
**CRITICAL**: Always use the `seconds-0` GitHub account for all operations on this repository.
- Repository: `domus-magna/chinarxiv`
- Account: `seconds-0` (via GH_TOKEN or gh auth)
- Never use `alexanderhuth` or other accounts for commits/PRs

### Cloudflare Pages Configuration
- **Project Name**: `chinarxiv`
- **Build Output Directory**: `site`
- **Production Branch**: `main`
- **Build Command**: (empty - GitHub Actions handles building)
- **Environment Variables**: `OPENROUTER_API_KEY`, `DISCORD_WEBHOOK_URL`

### Translation System
- **Model**: DeepSeek V3.2-Exp via OpenRouter
- **Cost**: ~$0.0013 per paper
- **Full Backfill Cost**: ~$45 for 34,237 papers
- **Workers**: Configurable (10-100 per job)
- **Parallelization**: Up to 20 concurrent jobs

### Donation System
- **Supported Cryptocurrencies**: BTC, ETH, SOL, USDC, USDT, STX
- **Donation Page**: `/donation.html`
- **Integration**: Links in main page and footer
- **Features**: Click-to-copy addresses, QR codes, mobile-friendly

### Performance Metrics
- **Nightly Intake**: All newly harvested items (current + previous month)
- **Parallel Translation**: Tunable; typical 10–30 concurrent workers
- **Backfill Throughput**: 100–2,000 papers/hour depending on concurrency and content
- **Site Performance**: <3 second load times, global CDN

### Monitoring & Maintenance
- **GitHub Actions**: Built-in workflow monitoring
- **Cloudflare Analytics**: Site performance and traffic
- **OpenRouter Dashboard**: API usage and costs
- **Discord Notifications**: Build success/failure alerts

### Custom Domain Setup (When Purchased)
1. **Purchase Domain**: From any registrar (GoDaddy, Namecheap, etc.)
2. **Add to Cloudflare**: Add site to Cloudflare dashboard
3. **Update Nameservers**: Point domain to Cloudflare nameservers
4. **Connect to Pages**: Add custom domain in Cloudflare Pages
5. **SSL Certificate**: Automatically issued by Cloudflare
6. **DNS Configuration**: Automatic CNAME record creation

### Troubleshooting
- **Build Failures**: Check GitHub Actions logs, verify secrets
- **Translation Failures**: Verify OpenRouter API key, check credits
- **Deployment Issues**: Check Cloudflare API token permissions
- **Site Issues**: Check build output directory, verify DNS

### B2 Hydration Flows (New)
- CI steps use Backblaze B2 as source of truth:
  - After translation, outputs are published to `validated/translations/` in B2 along with manifests and per‑paper pointers.
  - Before rendering, CI wipes `data/translated/` and syncs from `s3://$BACKBLAZE_BUCKET/$BACKBLAZE_PREFIX/validated/translations`.
  - If hydration returns zero JSON files, CI sends a throttled Discord alert and fails to avoid deploying an empty site.
- Local development: `make site-from-b2` pulls validated JSON from B2, rebuilds the site, and serves it on port 8001.

### Documentation
- **Complete Setup Guide**: `docs/archive/old/CLOUDFLARE_COMPLETE_SETUP.md`
- **Wrangler CLI Setup**: `docs/archive/old/WRANGLER_CLI_SETUP.md`
- **Parallelization Strategy**: `docs/archive/old/PARALLELIZATION_STRATEGY.md`
- **Backfill Strategy**: `docs/archive/old/BACKFILL_STRATEGY.md`
- **Donation Setup**: `docs/archive/old/DONATION_SETUP_PLAN.md`

## Pull Request Review Guidelines

### Checking All Review Types
When reviewing pull requests, **ALWAYS check for ALL types of reviews and comments**:

1. **Regular Comments**: `gh pr view --comments` or `gh pr view --json comments`
2. **Review Summaries**: `gh pr view --json reviews` 
3. **Inline Review Comments**: `gh api repos/{owner}/{repo}/pulls/{number}/comments`

### Critical Review Sources
- **mentatbot**: Human-style reviews with detailed analysis
- **chatgpt-codex-connector[bot]**: Codex automated reviews with inline suggestions
- **cursor[bot]**: Cursor IDE automated reviews
- **Manual reviews**: From human contributors

### Review Priority Levels
- **P1 (Critical)**: Fix before merging - causes runtime errors or data corruption
- **Medium**: Significant issues that should be addressed
- **Low**: Minor improvements or style issues

### Common Review Issues
- **Workflow issues**: Hardcoded values, missing setup steps, broken notifications
- **Documentation**: Incorrect paths, references to non-existent files
- **Code quality**: Race conditions, memory issues, API mismatches
- **Security**: Hardcoded secrets, missing validation

### Review Response Process
1. **Check all review types** using the commands above
2. **Prioritize P1 issues** - fix critical problems first
3. **Address documentation issues** - update paths and references
4. **Test fixes** - validate changes work correctly
5. **Add detailed PR comments** explaining what was fixed
6. **Push fixes** and notify reviewers

### GitHub CLI Commands for Reviews
```bash
# Check regular comments
gh pr view --comments

# Check review summaries  
gh pr view --json reviews

# Check inline review comments (CRITICAL - often missed!)
gh api repos/domus-magna/chinarxiv/pulls/{number}/comments

# Get all review data
gh pr view --json comments,reviews
```

**Remember**: Inline review comments are separate from regular comments and require the specific API endpoint to access!

## Translation workflow (current playbook)
- Default model: `openai/gpt-5.1`; deepseek/glm are manual-only until they can pass strict QA. Grok is disabled.
- Whole-paper first; if source >160 paragraphs or exceeds the token guard, switch to balanced macro-chunks (target ~20 paras, max 8 chunks) with strict `###PARA i###...###END###` numbering. Allow 1 retry per chunk for count/format mismatches. Successful chunks are cached to `data/cache/macro_chunks/` for resumability. Paragraph-level translation is forbidden.
- Request timeouts: 10s connect / 900s read (15 minutes) for OpenRouter calls. Per-paper wall-clock guard ~40m to avoid runaway hangs.
- Always run sidecar OCR (`ocrmypdf --force-ocr --language chi_sim+eng --sidecar <pdf>.txt <pdf> <pdf>`) so extraction reads the sidecar and merges short fragments (e.g., ~178 paras for the sample paper vs. 800+ junky paras before).
- Structural QA: fail if counts mismatch or if short-fragment ratio spikes relative to source; QA filter failure (Chinese/formatting) triggers retry/fallback. Residual Chinese characters are stripped before QA; persistent QA failures are saved to `reports/raw_translations/<paper_id>.qa_failed.json` for manual triage.
- Formatting: aggressive Markdown reflow (no content edits); writes `.md` alongside JSON. Micro-fragments (<=3 chars, no letters) are dropped before formatting.
- Run summaries live at `reports/run_summaries/<paper_id>.json` with attempt history and final status; translation JSON records `_model` and `_markdown_path`.
