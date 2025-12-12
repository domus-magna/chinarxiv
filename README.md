# ChinaRxiv

[![CI](https://github.com/domus-magna/chinaxiv-english/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/domus-magna/chinaxiv-english/actions/workflows/ci.yml)
[![Daily Pipeline](https://github.com/domus-magna/chinaxiv-english/actions/workflows/daily-pipeline.yml/badge.svg?branch=main)](https://github.com/domus-magna/chinaxiv-english/actions/workflows/daily-pipeline.yml)
[![Preflight Checks](https://github.com/domus-magna/chinarxiv/actions/workflows/preflight.yml/badge.svg?branch=main)](https://github.com/domus-magna/chinarxiv/actions/workflows/preflight.yml)
[![Render Gate](https://github.com/domus-magna/chinarxiv/actions/workflows/render-gate.yml/badge.svg?branch=main)](https://github.com/domus-magna/chinarxiv/actions/workflows/render-gate.yml)
[![Translation Gate](https://github.com/domus-magna/chinarxiv/actions/workflows/translation-gate.yml/badge.svg?branch=main)](https://github.com/domus-magna/chinarxiv/actions/workflows/translation-gate.yml)

English translations of Chinese academic papers from ChinaXiv.

## Features
- Automated translation pipeline
- Search functionality
- PDF generation
- Responsive web interface
- API access
- Monitoring dashboard
- Batch processing

## Quick Start
1. Clone repository
2. Install dependencies
3. Configure environment
4. Run pipeline

See [SETUP.md](docs/SETUP.md) for detailed instructions.

## Documentation
- [Setup Guide](docs/SETUP.md) - Complete setup instructions
- [Deployment Guide](docs/DEPLOYMENT.md) - Production deployment
- [API Documentation](docs/API.md) - API reference
- [Contributing Guide](docs/CONTRIBUTING.md) - Development guide
- [Workflows](docs/WORKFLOWS.md) - GitHub Actions workflows
- [PRD](docs/PRD.md) - Product requirements document

### Backfill by Month
Use the "backfill-month" GitHub Actions workflow to backfill a single month (YYYYMM). It harvests via BrightData, translates all papers in parallel, writes validated outputs to Backblaze B2, and updates PostgreSQL status so the Railway web app can serve the results.

## Architecture
- **Harvesting**: ChinaXiv via BrightData Web Unlocker (default)
- **Translation**: OpenRouter API (default `moonshotai/kimi-k2-thinking` with GLM fallback)
- **Web App**: Railway Flask app (`chinaxiv-web`) backed by Railway PostgreSQL
- **Storage**: PostgreSQL is source of truth; Backblaze B2 stores durable artifacts (PDFs, figures, translations)
- **CDN/DNS**: Cloudflare for caching and the `chinarxiv.org` domain

## Support ChinaRxiv

Help us continue translating Chinese academic papers to English! Your donations support:

- 💰 OpenRouter API costs for translations
- 🖥️ Server hosting and infrastructure
- 🔧 Ongoing development and improvements
- 📚 Keeping the service free and accessible

### Cryptocurrency Donations

We accept donations in multiple cryptocurrencies:

- **Bitcoin (BTC)**: `bc1qcxzuuykxx46g6u70fa9sytty53vv74eakch5hk`
- **Ethereum (ETH)**: `0x107F501699EFb65562bf97FBE06144Cd431ECc9D`
- **Solana (SOL)**: `9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM`
- **USD Coin (USDC)**: `0x107F501699EFb65562bf97FBE06144Cd431ECc9D` (ERC-20 on Ethereum)
- **Tether (USDT)**: `0x107F501699EFb65562bf97FBE06144Cd431ECc9D` (ERC-20 on Ethereum)
- **Stacks (STX)**: `SP2J6ZY48GV1EZ5V2V5RB9MP66SW86PYKKNRV9EJ7`

Visit our [donation page](https://chinarxiv.org/donation.html) for QR codes and detailed instructions.

### Donation Alerts

- **Ko‑fi**: Enable Ko‑fi’s built‑in Discord notifications in the Ko‑fi dashboard to get real‑time alerts for one‑click donations.
- **Crypto wallets (BTC/ETH/ERC‑20)**: The scheduled GitHub Action `Donation Watch` polls our donation addresses and posts a Discord alert on any new confirmed deposit, including an approximate USD value at the donation date for BTC/ETH and stablecoins. It also emits an optional Umami custom event `donation-received` with `{chain, symbol, amount, txid, usd?}`.

**Config (GitHub Secrets, optional unless noted):**
- `DISCORD_WEBHOOK_URL` (required for Discord alerts)
- `ETHERSCAN_API_KEY` (recommended for reliable ETH/ERC20 metadata)
- `BTC_DONATION_ADDRESS`, `ETH_DONATION_ADDRESS` (override defaults in `scripts/check_donations.py`)
- `ETH_DONATION_TOKENS` (comma‑separated ERC‑20 symbols; leave empty to ignore tokens, or set `ALL` to accept any)
- `UMAMI_WEBSITE_ID`, `UMAMI_SCRIPT_URL` (enable server‑side Umami events)
- `UMAMI_HOST_URL`, `UMAMI_COLLECT_ENDPOINT` (only if your Umami endpoint differs from the default `/api/send`)

To change poll frequency, edit the cron in `.github/workflows/donation-watch.yml`.

## Configuration

- `OPENROUTER_API_KEY` must be set in CI secrets for translation
- `BRIGHTDATA_API_KEY` and `BRIGHTDATA_ZONE` enable ChinaXiv harvesting (default path)
- `config.yaml` controls model slugs, glossary, and optional proxy settings

Cost tracking is estimated via crude token counts and `config.yaml` pricing.

### Environment Management

**The `.env` file is the single source of truth for API keys.** The system:

- ✅ Validates consistency between shell environment and `.env` file
- ✅ Provides clear instructions for fixing mismatches
- ✅ Auto-fixes current session environment when possible
- ✅ Prevents operations when environment is unhealthy

**Commands:**
- `make check-keys` - Check for API key mismatches
- `make fix-keys` - Auto-fix environment issues (current session)
- `make ensure-env` - Validate environment before operations

**To fix mismatches:** `source .env` or restart your shell.

This eliminates API key mismatch issues that previously caused translation failures.

## Health Checks

- Quick checks: `python -m src.health --skip-openrouter` or `scripts/health.sh`
- OpenRouter check requires `OPENROUTER_API_KEY`.
- **Environment health**: `make check-keys` - Validates API key consistency
- **Auto-fix issues**: `make fix-keys` - Automatically resolves environment mismatches

## Validation Gates & Fixtures

Use the validation gates to guard pipeline quality:

- `python -m src.tools.env_diagnose --preflight`
- `python -m src.validators.harvest_gate`
- `python -m src.validators.translation_gate`

When a run starts from a clean working tree, the helper `scripts/prepare_gate_fixtures.py` seeds representative harvest and translation artifacts from `tests/fixtures/` so the gates always exercise non-empty inputs. Run it manually with:

```bash
source .venv/bin/activate
python scripts/prepare_gate_fixtures.py
```

The script copies small sample JSON/PDF files into `data/` only when real artifacts are missing, keeping gate runs deterministic without polluting the repo.

## Preview

- `python -m http.server -d site 8001`
- Or: `make serve`

## Dev (live translation)

- Requires Python 3.11+ available as `python3.11` and `OPENROUTER_API_KEY`.
- One-liner: `make dev DEV_LIMIT=5`
- This creates `.venv` with Python 3.11, installs deps, runs tests + health, processes up to 5 new items live, builds site, and serves at `http://localhost:8001`.
- To avoid accidental data loss, `make dev` refuses to delete existing `site/` or `data/` unless you confirm with `DEV_ALLOW_CLEAN=1 make dev` (or run the helper `make dev-clean`).

### Installing Python 3.11 (macOS options)
- Homebrew: `brew install python@3.11` (then ensure `python3.11` is on PATH)
- pyenv: `brew install pyenv && pyenv install 3.11.9 && pyenv global 3.11.9`

## Production Deploy (Railway)

- The Flask app is deployed to Railway via Railway’s native GitHub integration (push to `main` triggers deploy).  
- The translation pipeline still runs in GitHub Actions (`pipeline.yml` / `backfill-*.yml`) and publishes to PostgreSQL + B2; the Railway app reads from PostgreSQL at request time.
- The previous Cloudflare Pages static‑site deploy is legacy and no longer used in production.

Batch translation (future option)
- Some providers offer asynchronous batch endpoints with longer SLAs (e.g., 12–24h) at significantly lower cost (~50%).
- DeepSeek and Z.AI GLM on OpenRouter do not currently expose batch endpoints; keep an eye on provider updates.
- If adopted later, the pipeline can submit segment batches on day N and collect results on day N+1 without major structural changes.

## Legal

- "Source: ChinaXiv" attribution and original link must be shown on every item page
- Respect article-level license; if derivatives are disallowed, publish title+abstract only

## Status
- **Papers Translated**: 3,096 / 3,461 (89.5%)
- **Site**: https://chinarxiv.org
- **Monitoring**: https://chinarxiv.org/monitor

## Contributing
See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for contribution guidelines.

## License
MIT License - see [LICENSE](LICENSE) for details.
