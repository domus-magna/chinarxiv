# ChinaXiv Translations

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
- [Agent Guide](AGENTS.md) - Required workflow and repo conventions
- [Claude Dev Guide](docs/CLAUDE.md) - Tests, local runs, background tasks
- [Setup Guide](docs/SETUP.md) - Complete setup instructions
- [Deployment Guide](docs/DEPLOYMENT.md) - Production deployment
- [API Documentation](docs/API.md) - API reference
- [Contributing Guide](docs/CONTRIBUTING.md) - Development guide
- [Workflows](docs/WORKFLOWS.md) - GitHub Actions workflows
- [PRD](docs/PRD.md) - Product requirements document

Notes
- Legacy root docs (e.g., `TESTING_GUIDE.md`, `BATCH_TRANSLATION_GUIDE.md`, status summaries) are archived under `docs/archive/old/`. Use the guides above for current instructions.

### Backfill by Month
Use the "backfill-month" GitHub Actions workflow to backfill a single month (YYYYMM). It harvests optimized, selects unseen items, translates all in parallel, and can optionally deploy to Cloudflare Pages. The workflow also persists cross-job dedupe by committing `data/seen.json` back to the repo after a successful run.

## Architecture
- **Harvesting**: ChinaXiv via BrightData Web Unlocker (default)
- **Translation**: OpenRouter API with DeepSeek V3.2-Exp model
- **Deployment**: Cloudflare Pages with GitHub Actions
- **Monitoring**: Consolidated monitoring service with alerts, analytics, and performance metrics

## Support ChinaXiv Translations

Help us continue translating Chinese academic papers to English! Your donations support:

- 💰 OpenRouter API costs for translations
- 🖥️ Server hosting and infrastructure
- 🔧 Ongoing development and improvements
- 📚 Keeping the service free and accessible

### Cryptocurrency Donations

We accept donations in multiple cryptocurrencies:

- **Bitcoin (BTC)**: `bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh`
- **Ethereum (ETH)**: `0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6`
- **Solana (SOL)**: `9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM`
- **USD Coin (USDC)**: `0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6`
- **Tether (USDT)**: `0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6`
- **Stacks (STX)**: `SP2J6ZY48GV1EZ5V2V5RB9MP66SW86PYKKNRV9EJ7`

Visit our [donation page](https://chinaxiv-english.pages.dev/donation.html) for QR codes and detailed instructions.

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

### Installing Python 3.11 (macOS options)
- Homebrew: `brew install python@3.11` (then ensure `python3.11` is on PATH)
- pyenv: `brew install pyenv && pyenv install 3.11.9 && pyenv global 3.11.9`

## Production Deploy (Cloudflare Pages)

- Add GitHub repository secrets:
  - `OPENROUTER_API_KEY`: your OpenRouter key (rotate if exposed).
  - `CLOUDFLARE_ACCOUNT_ID`: from Cloudflare dashboard.
  - `CF_API_TOKEN`: API token with Pages:Edit permission.
- CI builds nightly at 03:00 UTC and deploys `site/` to Cloudflare Pages using `cloudflare/pages-action`.
- First-time project creation happens on deploy (project name: `chinaxiv-english`).
- Map a custom domain in Cloudflare Pages → Custom domains.

Batch translation (future option)
- Some providers offer asynchronous batch endpoints with longer SLAs (e.g., 12–24h) at significantly lower cost (~50%).
- DeepSeek and Z.AI GLM on OpenRouter do not currently expose batch endpoints; keep an eye on provider updates.
- If adopted later, the pipeline can submit segment batches on day N and collect results on day N+1 without major structural changes.

## Legal

- "Source: ChinaXiv" attribution and original link must be shown on every item page
- Respect article-level license; if derivatives are disallowed, publish title+abstract only

## Status
- **Papers Translated**: 3,096 / 3,461 (89.5%)
- **Site**: https://chinaxiv-english.pages.dev
- **Monitoring**: https://chinaxiv-english.pages.dev/monitor

## Contributing
See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for contribution guidelines.

## License
MIT License - see [LICENSE](LICENSE) for details.
