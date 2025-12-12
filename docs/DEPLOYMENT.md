# Production Deployment Guide

## Architecture Overview

ChinaXiv Translations runs on Railway with a Flask backend serving dynamic pages.

| Component | Service | Notes |
|-----------|---------|-------|
| Web App | Railway (Flask + Gunicorn) | `chinaxiv-web` service |
| Database | Railway PostgreSQL | `Postgres-TS9y` service |
| Storage | Backblaze B2 | PDFs, figures, translations |
| CDN/DNS | Cloudflare | DNS proxy, caching |

## Production URLs

| Endpoint | URL |
|----------|-----|
| Web App | `https://chinaxiv-web-production.up.railway.app` |
| Health Check | `https://chinaxiv-web-production.up.railway.app/health` |
| API | `https://chinaxiv-web-production.up.railway.app/api/papers` |
| Domain (after DNS) | `https://chinarxiv.org` |

## Railway Project Structure

The Railway project contains two services:

1. **chinaxiv-web** - Flask application
   - Built from `nixpacks.toml`
   - Uses `requirements-web.txt`
   - Auto-deploys on push to `main`

2. **Postgres-TS9y** - PostgreSQL database
   - Managed PostgreSQL 15
   - Auto-linked via `DATABASE_URL`
   - Internal: `postgres-ts9y.railway.internal:5432`
   - Public: `metro.proxy.rlwy.net:52123`

## Critical Configuration Files

### `.python-version`
```
3.11
```
**REQUIRED** - Python 3.11 must be pinned because `psycopg2-binary` has no wheel for Python 3.13. Without this file, Railway uses Python 3.13 and causes:
```
ImportError: undefined symbol: _PyInterpreterState_Get
```

### `nixpacks.toml`
```toml
[phases.setup]
nixPkgs = ["python311", "postgresql"]

[phases.install]
cmds = ["pip install --upgrade pip", "pip install -r requirements-web.txt"]

[start]
cmd = "gunicorn -w 4 -b 0.0.0.0:$PORT 'app:create_app'"
```

**CRITICAL**: The gunicorn command must use `'app:create_app'` (no parentheses). Using `'app:create_app()'` causes a parse error and immediate crash.

### `requirements-web.txt`
Minimal dependencies for the web app:
```
flask==3.0.3
gunicorn==21.2.0
psycopg2-binary==2.9.9
Jinja2==3.1.4
markdown==3.7
markupsafe>=2.0
bleach==6.3.0
python-dotenv==1.0.1
```

## Deployment Commands

### Deploy to Railway
```bash
# Upload and build (interactive)
railway up --service chinaxiv-web

# Or just push to main (auto-deploys via Railway’s native GitHub integration)
git push origin main
```

### Monitor Deployment
```bash
# View logs
railway logs --service chinaxiv-web

# Check health
curl https://chinaxiv-web-production.up.railway.app/health
# Expected: {"status":"ok"}
```

### Railway CLI Setup (One-time)
```bash
# Install Railway CLI
brew install railway

# Login
railway login

# Link to project
railway link
```

## Database Operations

### Schema Creation
The schema is created via `scripts/create_schema.py` which creates:
- `papers` table with tsvector search column
- `paper_subjects` table (VARCHAR(500) for B-tree index compatibility)
- `category_counts` materialized view
- 7 indexes for optimized queries

### Refresh Materialized View
Run after data imports:
```bash
# Via psql
psql $DATABASE_URL -c "REFRESH MATERIALIZED VIEW category_counts;"

# Or via Railway
railway run python -c "
import psycopg2
import os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
conn.cursor().execute('REFRESH MATERIALIZED VIEW category_counts;')
conn.commit()
"
```

### Data Import
To import papers from B2:
1. Connect to PostgreSQL using public URL
2. Run import script (see `scripts/import_to_postgres.py`)
3. Refresh materialized view

```bash
# Example import (from local machine)
DATABASE_URL="postgresql://postgres:PASSWORD@metro.proxy.rlwy.net:52123/railway" \
  python scripts/import_to_postgres.py
```

## Environment Variables

Railway auto-sets these variables:

| Variable | Source | Notes |
|----------|--------|-------|
| `DATABASE_URL` | Auto-linked | `${{Postgres-TS9y.DATABASE_URL}}` |
| `PORT` | Auto-set | Railway assigns port |

## Lazy Database Initialization

The app uses lazy DB initialization to prevent crash loops:

```python
# app/db_adapter.py
def init_adapter():
    """No-op at startup."""
    pass

def get_adapter():
    """Creates connection on first database query."""
    global _adapter
    if _adapter is None:
        _adapter = DatabaseAdapter()
    return _adapter
```

This allows:
- `/health` to always return 200 even if DB is down
- App to start and serve error pages instead of crash-looping
- Graceful degradation during DB maintenance

## Cloudflare DNS Configuration

After Railway is verified working:

1. Go to Cloudflare DNS dashboard
2. Update `chinarxiv.org` CNAME:
   - Target: `chinaxiv-web-production.up.railway.app`
   - Initially: DNS-only (gray cloud) for testing
   - After verification: Proxy enabled (orange cloud)

## GitHub Actions (Translation Pipeline)

The translation pipeline still runs in GitHub Actions:
- `daily-pipeline.yml` - Daily harvest and translation
- `backfill.yml` - Batch translation
- `figure-backfill.yml` - Figure translation

These upload results to B2, which the Railway app imports.

## Troubleshooting

### App Returns 502
1. Check Railway logs: `railway logs --service chinaxiv-web`
2. Common causes:
   - Wrong Python version (check `.python-version` exists)
   - Gunicorn syntax error (check for parentheses)
   - Database connection failure (check DATABASE_URL)

### Database Connection Refused
1. Check PostgreSQL service is running in Railway dashboard
2. Verify DATABASE_URL is linked to web service
3. Check service logs for PostgreSQL errors

### Empty Homepage (No Papers)
1. Check paper count: `psql $DATABASE_URL -c "SELECT COUNT(*) FROM papers;"`
2. Run data import if needed
3. Refresh materialized view

### Health Returns 200 but Pages Fail
1. Database is probably down - health endpoint doesn't use DB
2. Check PostgreSQL service status
3. Check database logs

## Rollback Plan

If Railway fails:
1. Cloudflare Pages still exists at `chinarxiv.pages.dev`
2. Update DNS to point back to Pages
3. Re-run static site generation via `deploy.yml`

## Cost Breakdown

| Service | Monthly Cost |
|---------|--------------|
| Railway Web App | ~$5 |
| Railway PostgreSQL | ~$7 |
| Backblaze B2 | ~$0.50 |
| **Total** | ~$12.50 |
