# ChinaXiv Translations - Gemini Context

This project translates Chinese academic papers from ChinaXiv to English using an automated pipeline powered by OpenRouter (DeepSeek V3.2-Exp). It includes a harvesting system, translation engine, and a static site generator deployed to Cloudflare Pages.

## Project Overview

*   **Goal:** Automate translation of academic papers and publish them to a searchable static website.
*   **Tech Stack:**
    *   **Language:** Python 3.11+
    *   **AI/Translation:** OpenRouter API (DeepSeek model)
    *   **Harvesting:** BrightData (Web Unlocker)
    *   **Frontend:** Static HTML/CSS/JS (built via Python scripts)
    *   **Infrastructure:** Cloudflare Pages, GitHub Actions
    *   **Storage:** Local JSON files (committed to repo or artifact storage)

## Architecture

1.  **Harvesting:** Scrapes metadata from ChinaXiv using BrightData or local optimized scripts (`src/harvest_chinaxiv_optimized.py`).
2.  **Selection:** Filters papers based on relevance and availability (`src/select_and_fetch.py`).
3.  **Translation:** Uses LLMs via OpenRouter to translate titles and abstracts (`src/translate.py`).
4.  **Rendering:** Generates a static website from translated JSON data (`src/render.py`).
5.  **Search:** Client-side search index generation (`src/search_index.py`).
6.  **PDF:** Optional PDF generation of translated papers (`src/make_pdf.py`).

## Key Files & Directories

*   `src/`: Source code for the pipeline components.
*   `data/`: Data storage (input records, translated JSONs).
*   `site/`: Output directory for the generated static website.
*   `tests/`: Pytest suite (`pytest.ini` config).
*   `Makefile`: Command automation for development and production tasks.
*   `.env`: Configuration file for API keys (see `.env.example`).
*   `requirements.txt`: Python dependencies.

## Development Workflow

### Prerequisites
*   Python 3.11+
*   Valid `.env` file with `OPENROUTER_API_KEY` (minimum for translation).
*   `BRIGHTDATA_*` keys for harvesting (optional for dev, can use mock data).

### Common Commands

**1. Full Development Loop (Recommended)**
Creates a virtual environment, installs deps, runs tests, and spins up a local pipeline with a limit of 5 items.
```bash
make dev
```
*   **Force Clean:** `make dev-clean` (WARNING: Deletes `data/` and `site/`)
*   **Custom Limit:** `make dev DEV_LIMIT=10`

**2. Serving the Site**
Starts a local HTTP server for the `site/` directory on port 8001.
```bash
make serve
```

**3. Environment Management**
*   **Check Keys:** `make check-keys` (Validates `.env` vs shell environment)
*   **Fix Keys:** `make fix-keys` (Auto-fixes environment mismatches)

**4. Testing & Quality**
*   **Run Tests:** `make test`
*   **Linting:** `make lint` (Ruff)
*   **Formatting:** `make fmt` (Black)
*   **Health Check:** `make health`

**5. Manual Pipeline Steps (via Python)**
*   **Harvest:** `python -m src.harvest_chinaxiv_optimized --month YYYYMM`
*   **Translate:** `python -m src.translate --selected data/selected.json`
*   **Render:** `python -m src.render`

## Admin & Maintenance
*   **Admin UI:** `make admin` (Starts a Flask app on port 8081 for CI management - requires `ADMIN_PASSWORD`).
*   **Self Review:** `make self-review` runs a review script.
*   **Gate Fixtures:** `make gate-fixtures` seeds test data for validation gates.

## Deployment
*   **Production:** Deployed to Cloudflare Pages via GitHub Actions (`.github/workflows/`).
*   **Schedule:** Nightly builds at 03:00 UTC.
*   **Secrets:** Managed in GitHub Repository Secrets (`OPENROUTER_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`, etc.).
