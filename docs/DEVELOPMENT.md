# Development Guide

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 15+ (local or Docker)
- Git

### Setup
```bash
# Clone repository
git clone https://github.com/domus-magna/chinaxiv-english.git
cd chinaxiv-english

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up local PostgreSQL (see Database Setup below)
```

## Database Setup

### Option 1: Docker (Recommended)
```bash
docker-compose up -d
export DATABASE_URL="postgresql://postgres:postgres@localhost/chinaxiv_dev"
```

### Option 2: Homebrew (macOS)
```bash
brew install postgresql@15
brew services start postgresql@15
createdb chinaxiv_dev
createdb chinaxiv_test
export DATABASE_URL="postgresql://localhost/chinaxiv_dev"
```

### Option 3: Existing PostgreSQL
```bash
# Add to .env
echo "DATABASE_URL=postgresql://postgres:password@localhost:5432/chinaxiv_dev" >> .env

# Create databases
PGPASSWORD="password" psql -h localhost -U postgres -c "CREATE DATABASE chinaxiv_dev;"
PGPASSWORD="password" psql -h localhost -U postgres -c "CREATE DATABASE chinaxiv_test;"
```

### Schema Setup
```bash
# Run migration script
python scripts/migrate_to_postgres.py

# Or create schema directly
python scripts/create_schema.py
```

## Development Server

### Run Flask Development Server
```bash
# Port 5001 (port 5000 is used by macOS AirPlay)
export DATABASE_URL="postgresql://postgres:password@localhost:5432/chinaxiv_dev"
python -m flask --app app run --debug --port 5001
```
Access at: http://localhost:5001

### Environment Variables
Create `.env` file:
```bash
DATABASE_URL=postgresql://postgres:password@localhost:5432/chinaxiv_dev
OPENROUTER_API_KEY=your_key_here
```

## Running Tests

### All Tests
```bash
# Run all tests
python -m pytest tests/ -v --tb=short

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=term-missing
```

### Specific Tests
```bash
# Run specific test file
python -m pytest tests/test_translate.py -v

# Run specific test
python -m pytest tests/test_translate.py::TestTranslation::test_translate_field_success -v

# Stop on first failure
python -m pytest tests/ -x
```

### Test Database
Tests use a separate database:
```bash
# Create test database
createdb chinaxiv_test

# Run tests (auto-uses TEST_DATABASE_URL or default)
pytest tests/

# Or specify custom test database
TEST_DATABASE_URL="postgresql://localhost/chinaxiv_test" pytest tests/
```

## Development Commands

### Translation Pipeline
```bash
# Harvest papers (current month)
python -m src.harvest_chinaxiv_optimized --month $(date -u +"%Y%m")

# Translate papers
python -m src.translate

# Translate with options
python -m src.pipeline --workers 20 --with-qa --with-figures
```

### Database Operations
```bash
# Refresh materialized view
psql $DATABASE_URL -c "REFRESH MATERIALIZED VIEW category_counts;"

# Check paper count
psql $DATABASE_URL -c "SELECT COUNT(*) FROM papers;"

# View recent papers
psql $DATABASE_URL -c "SELECT id, title_en FROM papers ORDER BY date DESC LIMIT 5;"
```

## Deployment

### Deploy to Railway
```bash
# Install Railway CLI (one-time)
brew install railway
railway login
railway link

# Deploy
railway up --service chinaxiv-web

# View logs
railway logs --service chinaxiv-web

# Check health
curl https://chinaxiv-web-production.up.railway.app/health
```

### Auto-Deploy
Push to `main` triggers auto-deploy:
```bash
git push origin main
```

Note: We use Railway’s native GitHub integration for deploys (not a GitHub Actions deploy job).

## Project Structure

```
chinaxiv-english/
├── app/                    # Flask application
│   ├── __init__.py         # App factory (create_app)
│   ├── routes.py           # Route handlers
│   ├── database.py         # Query layer
│   ├── db_adapter.py       # PostgreSQL connection
│   └── filters.py          # Category/subject filters
├── src/                    # Translation pipeline
│   ├── translate.py        # Text translation
│   ├── pipeline.py         # Main pipeline
│   └── figure_pipeline/    # Figure translation
├── scripts/                # Utility scripts
│   ├── create_schema.py    # Database schema
│   └── migrate_to_postgres.py
├── tests/                  # Test suite
├── .python-version         # Python version (3.11)
├── nixpacks.toml           # Railway build config
└── requirements-web.txt    # Web dependencies
```

## Troubleshooting

### Port 5000 in Use
macOS uses port 5000 for AirPlay. Use port 5001:
```bash
python -m flask --app app run --debug --port 5001
```

### Database Connection Errors
1. Check PostgreSQL is running: `pg_isready`
2. Check DATABASE_URL is set: `echo $DATABASE_URL`
3. Check database exists: `psql $DATABASE_URL -c "SELECT 1;"`

### Import Errors
1. Activate virtual environment: `source .venv/bin/activate`
2. Install dependencies: `pip install -r requirements.txt`

### Test Failures
1. Create test database: `createdb chinaxiv_test`
2. Check test output: `pytest -v -s`
3. Run single test: `pytest tests/test_file.py -x`

## Best Practices

### Code Quality
- Run tests before committing
- Use `ruff` for linting
- Follow existing code patterns

### Git Workflow
- Work on feature branches
- Use atomic commits
- Open PRs for review

### Database Changes
- Add migrations to `scripts/`
- Update schema in both dev and prod
- Refresh materialized views after data changes
