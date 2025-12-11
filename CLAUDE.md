# ChinaRxiv Translation Pipeline

## Architecture Decision-Making Principles

**Lessons Learned: Always Ask About Scale First**

When proposing database architecture, **ALWAYS ask these critical questions BEFORE designing**:

1. **Current scale**: How many records exist today?
2. **Growth projection**: What's the expected growth over 6-12 months?
3. **Team expertise**: What databases does the team know? (Solo vs team? AI-assisted?)
4. **Deployment model**: Single server vs distributed? Hosting platform capabilities?

### Why This Matters: The Dual Database Mistake

In December 2025, we initially implemented **dual database support** (SQLite + PostgreSQL) without asking about scale. This was a mistake because:

- ❌ **Added unnecessary complexity** - 263 lines of adapter code, database-specific branches in 5 files
- ❌ **PostgreSQL code was UNTESTED** - all 692 tests ran on SQLite only (production risk!)
- ❌ **Hidden maintenance burden** - every feature needs testing on both databases
- ❌ **Wrong default assumption** - assumed "dev uses SQLite, production might use PostgreSQL"

### The Correct Approach

After asking the user about scale, we learned:
- **Current**: 700 papers
- **6-month projection**: 40,000 papers (57x growth!)
- **Team**: Solo developer with AI assistance
- **Deployment**: Railway (managed PostgreSQL available)

This revealed the correct architecture: **PostgreSQL-only** from the start.

**Rule**: Don't implement "flexibility" without understanding requirements. Ask about scale, then design the simplest architecture that meets those requirements.

---

## Database Architecture: PostgreSQL Only (Dec 2025)

**STATUS**: ✅ Simplified to PostgreSQL-only (Completed Dec 2025)

The application uses **PostgreSQL exclusively** for both development and production. This decision was made based on:
- Scale requirements: 700 → 40,000 papers in 6 months
- Performance needs: Sub-20ms category queries via materialized views
- Deployment: Railway managed PostgreSQL ($7-15/month)

**Simplification Complete**: Removed ~400 lines of SQLite code, eliminated dual database complexity. All 692 tests now run on PostgreSQL.

### Local Development Setup

**Option 1: Docker Compose** (Recommended)
```bash
docker-compose up -d  # Starts PostgreSQL
export DATABASE_URL="postgresql://postgres:postgres@localhost/chinaxiv_dev"
python scripts/migrate_to_postgres.py
```

**Option 2: Homebrew** (macOS)
```bash
brew install postgresql@15
brew services start postgresql@15
createdb chinaxiv_dev
export DATABASE_URL="postgresql://localhost/chinaxiv_dev"
python scripts/migrate_to_postgres.py
```

**Option 3: Existing PostgreSQL 17** (macOS, if already installed)
```bash
# Add to .env (local password is 'password')
echo 'DATABASE_URL="postgresql://postgres:password@localhost:5432/chinaxiv_dev"' >> .env
echo 'TEST_DATABASE_URL="postgresql://postgres:password@localhost/chinaxiv_test"' >> .env

# Create databases
PGPASSWORD="password" psql -h localhost -U postgres -c "CREATE DATABASE chinaxiv_dev;"
PGPASSWORD="password" psql -h localhost -U postgres -c "CREATE DATABASE chinaxiv_test;"

# Create schema directly (no data migration)
source .venv/bin/activate
pip install psycopg2-binary
PGPASSWORD="password" psql -h localhost -U postgres -d chinaxiv_dev -f <(cat << 'EOF'
CREATE TABLE papers (...); -- See scripts/migrate_to_postgres.py for full SQL
-- Creates papers, paper_subjects tables, indexes, and category_counts materialized view
EOF)

# Start Flask (note: port 5000 is used by macOS AirPlay, use 5001)
source .env
python -m flask --app app run --debug --port 5001
```

Access at: http://localhost:5001

### Production Deployment (Railway)

```bash
railway add postgres  # Provision managed PostgreSQL
railway up            # Deploy app (DATABASE_URL auto-set)
railway run python scripts/migrate_to_postgres.py  # One-time migration
```

### Performance Optimizations

| Operation | Performance | Technique |
|-----------|-------------|-----------|
| Category counts | 10-20ms | Materialized view `category_counts` |
| Full-text search | 20-40ms | tsvector + GIN index |
| Filtered queries | 20-30ms | Composite B-tree indexes |
| Connection reuse | 1-20 pooled | psycopg2 connection pooling |

### Testing Setup

Tests require local PostgreSQL database with authentication:

```bash
# Create test database (if not exists)
PGPASSWORD="password" psql -h localhost -U postgres -c "CREATE DATABASE chinaxiv_test;"

# Run tests with credentials (local password is 'password')
TEST_DATABASE_URL="postgresql://postgres:password@localhost/chinaxiv_test" pytest tests/

# Or add to .env for convenience:
echo 'TEST_DATABASE_URL="postgresql://postgres:password@localhost/chinaxiv_test"' >> .env
source .env
pytest tests/
```

**Local PostgreSQL credentials**: `postgres:password` (user:password)

**Test Database**: All 692 tests use PostgreSQL fixtures (`tests/conftest.py`). Test schema is created using `scripts/migrate_to_postgres.py` functions.

### Materialized View Refresh

```bash
# Refresh category counts (run daily or after data imports)
psql $DATABASE_URL -c "REFRESH MATERIALIZED VIEW category_counts;"
```

### Implementation Files
- `app/db_adapter.py` - PostgreSQL connection wrapper (168 lines, simplified from 263)
- `app/__init__.py` - Connection pooling initialization
- `app/database.py` - Query layer with tsvector search (native %s placeholders)
- `app/routes.py` - Paper detail queries
- `app/filters.py` - Category counts from materialized view (210 lines)
- `scripts/migrate_to_postgres.py` - Schema + materialized views
- `tests/conftest.py` - PostgreSQL test fixtures (rewritten from SQLite)

**Simplification Stats**: Removed ~400 lines of SQLite code, eliminated 3 adapter methods (`adapt_placeholder`, `adapt_fts_query`, `get_exception_class`).

---

## CRITICAL: Full Pipeline = Text + Figures

**MANDATORY**: When running ANY translation job (backfill, single paper, batch):
1. **ALWAYS translate BOTH text AND figures** - this is NOT optional
2. If figure pipeline fails, the whole job fails - do not continue text-only
3. Never assume "text-only first, figures later" without EXPLICIT user approval

## Railway Deployment (Dec 2025)

**STATUS**: ✅ Railway is the primary backend serving all pages dynamically.

The site is deployed on Railway with Flask + Gunicorn serving all pages. There is no static site generation anymore - the database is queried at runtime.

### Production URLs
| Service | URL |
|---------|-----|
| Web App | `https://chinaxiv-web-production.up.railway.app` |
| Health | `https://chinaxiv-web-production.up.railway.app/health` |
| API | `https://chinaxiv-web-production.up.railway.app/api/papers` |

### Critical Configuration Files

**`.python-version`** - REQUIRED
```
3.11
```
Python 3.11 is required because `psycopg2-binary` has no stable wheel for Python 3.13. Without this file, Railway's Nixpacks will auto-detect Python 3.13 and cause:
```
ImportError: undefined symbol: _PyInterpreterState_Get
```

**`nixpacks.toml`** - Railway build config
```toml
[phases.setup]
nixPkgs = ["python311", "postgresql"]

[phases.install]
cmds = ["pip install --upgrade pip", "pip install -r requirements-web.txt"]

[start]
cmd = "gunicorn -w 4 -b 0.0.0.0:$PORT 'app:create_app'"
```

**CRITICAL**: Gunicorn command syntax must be `'app:create_app'` NOT `'app:create_app()'`. Gunicorn calls the factory function itself - adding parentheses causes a parse error and immediate crash.

### Lazy Database Initialization

The app uses lazy DB initialization (`app/db_adapter.py`) to prevent crash loops if PostgreSQL is temporarily unavailable:
- `init_adapter()` is a no-op at startup
- `get_adapter()` creates the connection on first database query
- This allows `/health` to always return 200 even if DB is down

### Deployment Commands
```bash
# Deploy to Railway (uploads code, builds, starts)
railway up --service chinaxiv-web

# View logs
railway logs --service chinaxiv-web

# Check health
curl https://chinaxiv-web-production.up.railway.app/health
```

### Database URLs
- **Internal** (from Railway services): `postgres-ts9y.railway.internal:5432`
- **Public** (external access): `metro.proxy.rlwy.net:52123`

## Quick Reference

> **Skill Available**: Use `chinaxiv-orchestrator` skill for comprehensive pipeline documentation.

### Universal Orchestrator (Recommended)

All pipeline operations now go through `src/orchestrator.py` via `pipeline.yml`:

```bash
# Resume pending/zombie papers (daily operation)
gh workflow run pipeline.yml -f scope=smart-resume

# Translate all papers from a month
gh workflow run pipeline.yml -f scope=month -f target=202501

# Discover new papers (adds to DB, no translation)
gh workflow run pipeline.yml -f scope=discover -f target=202501

# Translate specific papers
gh workflow run pipeline.yml -f scope=list -f target=chinaxiv-202501.00001,chinaxiv-202501.00002

# Retry ALL failed papers
gh workflow run pipeline.yml -f scope=smart-resume -f include_failed=true

# Sync DB state from B2 (weekly, or after manual changes)
gh workflow run reconcile-state.yml
```

### Local Development
```bash
python -m src.orchestrator --scope smart-resume
python -m src.orchestrator --scope month --target 202501 --workers 10 --dry-run
```

### Monitor Progress
```bash
gh run list --workflow=pipeline.yml --limit 5
gh run watch <run-id>
```

## Architecture Overview

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  1. HARVEST      │ ──► │  2. TRANSLATE    │ ──► │  3. PUBLISH      │
│  (Download PDFs) │     │  Text + Figures  │     │  (B2 + Site)     │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```

### PDF Download Architecture

**ChinaXiv IP-Bound UUIDs**: ChinaXiv generates IP-bound UUIDs for PDF downloads:
1. Abstract page contains a link with `uuid=...` parameter
2. This UUID is only valid from the **same IP** that loaded the abstract
3. If PDF request comes from different IP → 404 "页面不存在"

**BrightData Zone Types**:

| Zone | Session Support | Use Case |
|------|-----------------|----------|
| Web Unlocker/SERP (`china_paper_scraper1`) | ❌ No | HTML scraping only |
| Scraping Browser (`china_browser1`) | ✅ `-session-{id}` in WSS | PDF downloads |

**Download Strategy** (`_headless_pdf_fetch()` in `src/pdf_pipeline.py`):

1. **Connect with session ID** - Inject `-session-{id}` into WSS URL for IP stickiness
2. **Navigate to abstract** - Get fresh UUID bound to current IP
3. **Extract PDF link** - Fresh UUID from page (not the stale one passed in)
4. **JS fetch()** - Download PDF within browser context (same IP)
5. **Base64 transfer** - Binary PDF from browser to Python

*Why JS fetch() instead of navigation?* BrightData Browser has navigation limits. Using JavaScript `fetch()` API bypasses this while maintaining same IP context.

**Fallback Chain** (in `download_pdf()`):
```
download_pdf()
    ├── Direct request (fast path)
    ├── _unlocker_raw_fetch() (proxy fallback)
    └── _headless_pdf_fetch() (browser + JS fetch for IP-bound UUIDs)
```

### Text Translation
- **Location**: `src/translate.py`, `src/pipeline.py`
- **Model**: Kimi K2 Thinking (`moonshotai/kimi-k2-thinking`) via OpenRouter
- **Mode**: Whole-paper synthesis (NOT paragraph-by-paragraph)
- **Config**: `src/config.yaml` - controls model, chunk size (28K tokens), temperature
- **QA**: Chinese leakage check (<0.5%), math preservation
- **Cost**: ~$0.08/paper (Kimi K2 pricing: $0.45/M input, $2.35/M output)

### Figure Translation
- **Location**: `src/figure_pipeline/`
- **Extract**: PyMuPDF extracts images from PDF
- **Translate**: Gemini 3 Pro generates new image with English text
- **QA**: Moondream validates Chinese removed, English present
- **Storage**: B2 for original + translated images

## Environment Variables Required

```bash
# Text Translation
OPENROUTER_API_KEY=xxx

# Figure Translation
GEMINI_API_KEY=xxx          # Google API for figure translation
MOONDREAM_API_KEY=xxx       # Figure QA validation

# Storage
BACKBLAZE_KEY_ID=xxx
BACKBLAZE_APPLICATION_KEY=xxx
BACKBLAZE_S3_ENDPOINT=xxx
BACKBLAZE_BUCKET=xxx

# Harvesting
BRIGHTDATA_API_KEY=xxx
BRIGHTDATA_ZONE=xxx
```

## Fail-Fast Error Handling

The pipeline uses circuit breakers to stop immediately on billing/auth errors:
- `402 Payment Required` → Stop immediately
- `401 Unauthorized` → Stop immediately
- `429 + quota message` → Stop immediately
- Transient errors (5xx, network) → Retry with backoff, then stop after 5 failures

## Running a Backfill

The universal orchestrator handles all backfill operations. Use GitHub Actions (recommended) or run locally.

### Via GitHub Actions (Recommended)

```bash
# Step 1: Discover papers for the month (adds to DB)
gh workflow run pipeline.yml -f scope=discover -f target=202501

# Step 2: Translate all papers (text + figures + PDF)
gh workflow run pipeline.yml -f scope=month -f target=202501

# Monitor progress
gh run list --workflow=pipeline.yml --limit 5
```

### Via Local Development

```bash
# Step 1: Discover papers
python -m src.orchestrator --scope discover --target 202501

# Step 2: Translate (full pipeline)
python -m src.orchestrator --scope month --target 202501 --workers 10
```

### Deploy to Railway

Railway auto-deploys on push to main. The Flask app queries PostgreSQL directly.

```bash
# Manual deploy (if needed)
railway up --service chinaxiv-web

# Monitor deployment
railway logs --service chinaxiv-web

# Verify deployment
curl https://chinaxiv-web-production.up.railway.app/health
```

## Common Mistakes to Avoid

1. **Running text-only translation** - NEVER do this unless user explicitly approves
2. **Assuming figures can be added later** - While technically possible, it's extra work
3. **Ignoring circuit breaker errors** - These indicate billing issues, stop and check
4. **Not checking for GEMINI_API_KEY** - Figure translation requires Google API access
5. **Using wrong Python version on Railway** - Must use 3.11 (`.python-version` file)
6. **Gunicorn syntax with parentheses** - Use `'app:create_app'` NOT `'app:create_app()'`

## Monitoring

- Discord alerts for circuit breaker trips
- Check workflow status: `gh run list --workflow=pipeline.yml --limit 5`
- B2 manifests in `indexes/validated/manifest-*.csv`

## GitHub Token Management

### Architecture: Separate API Token from Git Operations

Git operations and API calls use **different tokens** to avoid workflow scope conflicts:

- **Git operations** (push, pull): Use `gh` CLI keyring (has workflow scope)
- **API calls** (`src/gh_actions.py`): Use token from `.env.github` file

This separation prevents the recurring "refusing to allow OAuth App to create or update workflow" error.

### File Structure

| File | Purpose | Loaded By |
|------|---------|-----------|
| `.env.github` | GH_TOKEN for API calls | `src/gh_actions.py` |
| `.env` | Other secrets (no GH_TOKEN) | Shell, Python dotenv |
| `.envrc` | Auto-unsets GH_TOKEN on repo entry | direnv |
| Keyring | Git authentication | `gh` CLI, git push |

### Layered Defense

This repo uses three layers to prevent GH_TOKEN from breaking git push:

1. **`.envrc`** - direnv auto-unsets GH_TOKEN when entering the repo
2. **Pre-push hook** - blocks push if GH_TOKEN is in environment
3. **Separate files** - `.env.github` for API calls, `.env` for everything else

### Setup (one-time)

1. **Ensure keyring has workflow scope:**
   ```bash
   gh auth refresh -s workflow
   gh auth status  # Should show: Token scopes include 'workflow'
   ```

2. **Create `.env.github` if missing:**
   ```bash
   echo "GH_TOKEN=$(gh auth token)" > .env.github
   echo "GH_REPO=domus-magna/chinaxiv-english" >> .env.github
   ```

3. **Verify `.env` does NOT have GH_TOKEN:**
   ```bash
   grep "^GH_TOKEN=" .env  # Should return nothing
   ```

4. **(Optional) Enable direnv:**
   ```bash
   brew install direnv
   echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc  # or ~/.bashrc
   direnv allow .
   ```

### If Push Fails with Workflow Scope Error

```bash
# Step 1: Unset any GH_TOKEN in your shell
unset GH_TOKEN

# Step 2: Refresh your keyring (if needed)
gh auth refresh -s workflow

# Step 3: Retry push
git push
```

The pre-push hook validates keyring has workflow scope before pushing workflow files.

### Verify Setup

```bash
# Check keyring has workflow scope
gh auth status
# Should show: Token scopes: 'gist', 'read:org', 'repo', 'workflow'

# Check GH_TOKEN is NOT in shell environment
echo $GH_TOKEN  # Should be empty

# Check .env.github exists with GH_TOKEN
cat .env.github | grep GH_TOKEN  # Should show the token
```

## CRITICAL: Backblaze B2 Persistence is MANDATORY

```
+==============================================================================+
|  ALL PIPELINE DATA MUST BE PERSISTED TO B2 - NO EPHEMERAL OPTIONS            |
|                                                                              |
|  GitHub Actions runners are ephemeral. If data is not uploaded to B2,        |
|  it is LOST when the job ends. This is a HARD requirement.                   |
+==============================================================================+
```

### What MUST be persisted to B2

| Data Type | B2 Path | Workflow Step |
|-----------|---------|---------------|
| Downloaded PDFs | `pdfs/{paper_id}.pdf` | After download, before translation |
| Validated translations | `validated/translations/{paper_id}.json` | After QA pass |
| Flagged translations | `flagged/translations/{paper_id}.json` | After QA flag |
| Translated figures | `figures/{paper_id}/` | After figure translation |
| Selection files | `selections/daily/{date}.json` | After selection |
| Records | `records/chinaxiv_{month}.json` | After harvest |

### Workflow Requirements

**EVERY workflow that produces output MUST:**
1. Have B2 credentials as required secrets (not optional)
2. Upload outputs to B2 BEFORE the job ends
3. **FAIL** (not skip) if B2 upload fails
4. **FAIL** (not continue) if B2 credentials are missing

**There are NO ephemeral options:**
- No `--skip-persist` flags
- No "local only" modes for production workflows
- No silent skips when B2 credentials are missing
- PR builds are the ONLY exception (secrets are withheld by GitHub)

### Why This Matters

1. **GitHub runners are ephemeral** - all local data is destroyed after job ends
2. **PDFs cost money to download** - re-downloading wastes BrightData credits
3. **Translations cost money** - re-translating wastes OpenRouter credits
4. **Retries must resume** - without B2 persistence, retries start from scratch

### Verifying B2 Persistence

**Quick status check:**
```bash
python scripts/b2_status.py
```

**Manual checks:**
```bash
# Check if PDFs are in B2
aws s3 ls s3://${BACKBLAZE_BUCKET}/${BACKBLAZE_PREFIX}pdfs/ --endpoint-url ${BACKBLAZE_S3_ENDPOINT}

# Check if translations are in B2
aws s3 ls s3://${BACKBLAZE_BUCKET}/${BACKBLAZE_PREFIX}validated/translations/ --endpoint-url ${BACKBLAZE_S3_ENDPOINT}

# Count files for a specific month
aws s3 ls s3://${BACKBLAZE_BUCKET}/${BACKBLAZE_PREFIX}pdfs/chinaxiv-202401 --endpoint-url ${BACKBLAZE_S3_ENDPOINT} | wc -l
```

## B2 Storage Map

**Bucket:** `chinaxiv` (Backblaze B2)
**Endpoint:** `https://s3.us-west-004.backblazeb2.com`

### Directory Structure

```
s3://chinaxiv/
├── pdfs/                              # Source PDFs (934+ files)
│   └── chinaxiv-YYYYMM.NNNNN.pdf
│
├── validated/translations/            # QA-passed text translations (3,872+ files)
│   └── chinaxiv-YYYYMM.NNNNN.json
│
├── flagged/translations/              # QA-failed translations (need review)
│   └── chinaxiv-YYYYMM.NNNNN.json
│
├── figures/                           # Translated figures (currently EMPTY)
│   └── chinaxiv-YYYYMM.NNNNN/
│       ├── original/
│       │   └── fig_N.png
│       └── translated/
│           └── fig_N.png
│
├── records/                           # Harvested metadata
│   └── chinaxiv_YYYYMM.json
│
├── selections/                        # Daily selection files
│   └── daily/
│       └── YYYY-MM-DD.json
│
└── indexes/                           # Manifests and indices
    └── validated/
        └── manifest-YYYY-MM-DD.csv
```

### What Each Directory Contains

| Path | Contents | Created By |
|------|----------|------------|
| `pdfs/` | Original Chinese PDFs | `harvest_chinaxiv_optimized.py` |
| `validated/translations/` | QA-passed JSON (text only for now) | `batch_translate.yml` workflow |
| `flagged/translations/` | Failed QA, needs manual review | `batch_translate.yml` workflow |
| `figures/` | **EMPTY** - figure pipeline hasn't run | `figure-backfill.yml` (pending) |
| `records/` | Harvested paper metadata | `daily-pipeline.yml` harvest |
| `selections/` | Papers selected for translation | Pipeline selection step |

### Status Summary (as of Dec 2025 refresh)

| Data | Count | Status |
|------|-------|--------|
| Text translations | 3,872+ | ✅ Working |
| PDFs | In progress | ✅ PDF backfill running |
| Figures | In progress | ✅ Figure pipeline stable; CS/AI filter on |
| Records | ~50 months | ✅ Working |

**To check current status:** `python scripts/b2_status.py`

## Figure Translation Requests

Users can request figure translation for papers via the "Request Figure Translation" button on the paper detail page.

### Request Storage

**Production Storage:** Cloudflare KV `FIGURE_REQUESTS` namespace

**Storage Pattern:** Per-request keys (eliminates race conditions)
- Key format: `requests:YYYY-MM-DD:uuid` (e.g., `requests:2025-12-08:a1b2c3d4-...`)
- TTL: 90 days (auto-expiration)
- Value: JSON object with request details

**Format:** Each KV key contains a single JSON object:
```json
{"paper_id":"chinaxiv-202510.00001","timestamp":"2025-12-08T12:34:56.123Z","ip_hash":"abc123def456789"}
```

**Fields:**
- `paper_id`: Paper identifier (format: `chinaxiv-YYYYMM.NNNNN`)
- `timestamp`: ISO 8601 UTC timestamp of the request
- `ip_hash`: First 16 characters of SHA-256 hash of requester IP (privacy-preserving)

### Viewing Requests

#### Production Workflow (Cloudflare KV)

The API logs requests to Cloudflare KV. To aggregate production requests:

```bash
# Set environment variables (one-time setup)
export CF_ACCOUNT_ID=your_account_id
export CF_API_TOKEN=your_api_token  # Needs KV read scope
export CF_KV_NAMESPACE_ID=88b32d74f91649bca3321de23732d3c3

# Aggregate from KV (last 30 days)
python scripts/aggregate_figure_requests.py --kv --days 30

# Export to local file for faster re-runs
python scripts/aggregate_figure_requests.py --kv --days 30 --export-jsonl data/figure_requests.jsonl

# Get top 50 requested papers
python scripts/aggregate_figure_requests.py --kv --days 30 --top 50

# Export paper IDs for batch processing
python scripts/aggregate_figure_requests.py --kv --output data/high_priority_papers.txt
```

**Getting Cloudflare Credentials:**

1. **Account ID**: Dashboard → Account → Account ID
2. **API Token**: Dashboard → My Profile → API Tokens → Create Token
   - Template: "Edit Cloudflare Workers"
   - Permissions: Account > Workers KV Storage > Read
3. **Namespace ID**: From `wrangler.toml` (line 10) or `88b32d74f91649bca3321de23732d3c3`

#### Local Development Workflow

For testing without KV access (uses local JSONL file):

```bash
# Aggregate from local JSONL file
python scripts/aggregate_figure_requests.py --input data/figure_requests.jsonl

# Or use default path
python scripts/aggregate_figure_requests.py
```

### Spam Protection

Two-layer protection:

1. **Client-side:** localStorage tracks requested papers - button shows "Request Submitted" persistently
2. **Server-side:** Duplicate detection - same IP can't request same paper within 60 seconds (Cloudflare KV)
3. **Future:** Can add more sophisticated rate limiting later if spam becomes an issue

**Race Condition Status:** ✅ FIXED - Per-request keys (v2) eliminated the race condition from v1's append-to-daily-log approach.

### Integration with Figure Pipeline

To process most-requested papers:

```bash
# Get top 20 requested papers from production
python scripts/aggregate_figure_requests.py --kv --output data/high_priority_papers.txt --top 20

# Review the list
cat data/high_priority_papers.txt

# Run figure translation for high-priority papers
# (Manual selection recommended - review the list first)
python -m src.figure_pipeline --paper-ids <paper_id1> <paper_id2> ...
```

### Cloudflare Setup

**Required KV Namespace:** `FIGURE_REQUESTS`

**Binding in wrangler.toml:**
```toml
[[kv_namespaces]]
binding = "FIGURE_REQUESTS"
id = "88b32d74f91649bca3321de23732d3c3"
```

**KV Keys:**
- `dup:{ip_hash}:{paper_id}` → Duplicate detection (TTL: 60 seconds)
- `requests:YYYY-MM-DD:uuid` → Per-request log (TTL: 90 days)

## Text Translation Requests

Users can request full text translation for papers that only have abstracts or partial translations via the "Request Full Text Translation" button on the paper detail page.

### Button Priority Logic

The sidebar shows translation request buttons with this priority:
1. If paper lacks full text (`_has_full_text` = False) → Show "Request Full Text Translation"
2. Else if paper lacks figures (`_has_translated_figures` = False) → Show "Request Figure Translation"
3. Else → No request button (paper is fully translated)

### Request Storage

**Storage:** Same Cloudflare KV namespace as figure requests (`FIGURE_REQUESTS`), but with different key prefixes.

**KV Key Patterns:**
- `text_dup:{ip_hash}:{paper_id}` → Duplicate detection (TTL: 60 seconds)
- `text_requests:YYYY-MM-DD:uuid` → Per-request log (TTL: 90 days)

**Format:** Same JSON structure as figure requests:
```json
{"paper_id":"chinaxiv-202201.00007","timestamp":"2025-12-10T12:34:56.123Z","ip_hash":"abc123def456789"}
```

### Viewing Requests

```bash
# Aggregate text translation requests from KV (last 30 days)
python scripts/aggregate_figure_requests.py --kv --type text --days 30

# Get top 50 requested papers for text translation
python scripts/aggregate_figure_requests.py --kv --type text --days 30 --top 50

# Export text translation request paper IDs
python scripts/aggregate_figure_requests.py --kv --type text --output data/text_priority_papers.txt
```

### Detection Logic (database)

Papers are marked as having/not having full text via the `has_full_text` column in the papers table:
- Set to TRUE if `body_md` contains >100 chars non-heading content OR >200 chars total
- Fallback: checks `body_en` array for substantial paragraphs (>100 chars or 2+ paragraphs)
- Set during import by `scripts/import_to_postgres.py`

## Frontend Development

When working on frontend design and UI/UX tasks:

**Use the `frontend-design` skill** for creating production-grade interfaces with high design quality. This skill generates distinctive, polished code that avoids generic AI aesthetics.

**Always dispatch gemini subagents** to research and make design and front-end recommendations, as they are experts. Dispatch with `gemini -p "your prompt"`. Use these in addition to your explore agents.

This ensures you get expert-level design input and recommendations for user interface work.

## Feature Planning

**Planned features and enhancements are tracked in `TODO.md`**. Before implementing new features:
1. Check `TODO.md` for existing plans and context
2. Update `TODO.md` with your implementation approach
3. Mark items as completed when done
4. Move completed items to the "Completed Features" section

The TODO file provides implementation notes, file locations, and integration points for future work.

## Umami Analytics

**STATUS**: Integrated (Dec 2025)

Privacy-first, self-hosted analytics using Umami on Railway.

### Quick Reference

- **Skill**: Use the `umami` skill for detailed documentation on adding/modifying tracking
- **Dashboard**: Access via Railway (umami service)
- **Script**: Loaded conditionally in `src/templates/base.html` when `UMAMI_WEBSITE_ID` is set

### Environment Variables (Railway chinaxiv-web)

```
UMAMI_WEBSITE_ID=<uuid-from-umami-dashboard>
UMAMI_SCRIPT_URL=<your-umami-domain>/script.js
```

### Tracked Events

| Event | Data | Location |
|-------|------|----------|
| `search` | `{ query }` | Header search |
| `filter-category` | `{ category }` | Category tabs |
| `filter-advanced` | `{ has_search, has_date, has_figures, subject_count }` | Advanced search modal |
| `pdf-download-en` | `{ paper_id }` | Paper detail |
| `pdf-download-cn` | `{ paper_id }` | Paper detail |
| `view-chinaxiv` | `{ paper_id }` | Paper detail |
| `copy-citation` | `{ paper_id, format }` | Paper detail |
| `copy-link` | `{ paper_id }` | Paper detail |
| `bookmark` | `{ paper_id, action }` | Paper detail |
| `request-figures` | `{ paper_id }` | Paper detail |
| `request-text` | `{ paper_id }` | Paper detail |
| `report-submit` | `{ paper_id, issue_type }` | Paper detail |
| `sponsor-cta` | `{ target, crypto? }` | Sponsors page |

### Adding New Events

Use `trackEvent()` helper in `assets/site.js` (graceful degradation if Umami not loaded):

```javascript
trackEvent('event-name', { key: 'value' });
```

Or use data attributes on HTML elements:

```html
<button data-umami-event="event-name" data-umami-event-key="value">Click</button>
```

### Files

| File | Purpose |
|------|---------|
| `src/templates/base.html` | Script tag (conditional) |
| `app/__init__.py` | Config vars |
| `assets/site.js` | `trackEvent()` helper |
| `~/.claude/skills/umami/SKILL.md` | Full documentation |
- never write local translation scripts. ALWAYS use our async cloud functions for translation and uploads