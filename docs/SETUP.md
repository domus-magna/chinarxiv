# ChinaRxiv Setup Guide

## Quick Start
1. Clone repository
2. Install dependencies
3. Configure environment
4. Run pipeline

## Detailed Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- Cloudflare account
- OpenRouter API key
- BrightData account (Web Unlocker) with zone configured

### PDF Generation (Optional)
To generate English PDF versions of papers locally, install pandoc and LaTeX:

```bash
# macOS
brew install pandoc
brew install --cask mactex-no-gui  # or: brew install texlive

# Ubuntu/Debian
sudo apt-get install pandoc texlive-latex-base texlive-fonts-recommended texlive-latex-recommended

# Verify installation
pandoc --version
pdflatex --version
```

Note: CI workflows already include these dependencies. This is only needed for local development.

### Installation
```bash
# Clone repository
git clone https://github.com/domus-magna/chinarxiv
cd chinarxiv

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration
```bash
# Copy environment template
cp .env.example .env

# Edit configuration
nano .env
```

#### Required Environment Variables
- `OPENROUTER_API_KEY`: Your OpenRouter API key for translations
- `BRIGHTDATA_API_KEY`: BrightData API key (for harvesting)
- `BRIGHTDATA_ZONE`: BrightData zone name (for harvesting)
- `DISCORD_WEBHOOK_URL`: Discord webhook for notifications (optional)
- `MONITORING_USERNAME`: Username for monitoring dashboard (default: admin)
- `MONITORING_PASSWORD`: Password for monitoring dashboard (default: chinaxiv2024)

### First Run
```bash
# Harvest current month via BrightData (optimized; optional if records provided)
python -m src.harvest_chinaxiv_optimized --month $(date -u +"%Y%m")

# Run pipeline (translates selected items; omit --limit to process all)
python -m src.pipeline --workers 10 --limit 5

# Build site
python -m src.render
python -m src.search_index

# Serve locally
python -m http.server 8001 --directory site
```

## Deployment

### Cloudflare Pages
1. Create Cloudflare account
2. Add GitHub repository
3. Configure build settings
4. Deploy

#### Build Settings
- **Build Command**: (empty - GitHub Actions handles building)
- **Build Output Directory**: `site`
- **Root Directory**: (empty)

#### Required Secrets
- `CF_API_TOKEN`: Cloudflare API token with Pages:Edit permission
- `CLOUDFLARE_ACCOUNT_ID`: Cloudflare Account ID
- `OPENROUTER_API_KEY`: OpenRouter API key for translations
- `DISCORD_WEBHOOK_URL`: Discord webhook for notifications (optional)

### Custom Domain
1. Purchase domain
2. Add to Cloudflare
3. Configure DNS
4. Enable SSL

## Development

### Running Tests
```bash
# Run all tests
python -m pytest tests/ -v --tb=short

# Run specific test file
python -m pytest tests/test_translate.py -v

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=term-missing

# Run E2E tests
python -m pytest tests/test_e2e_simple.py -v
```

### Code Quality
```bash
# Format code
black src tests

# Check linting
ruff check src tests

# Sort imports
isort src tests
```

### Monitoring Dashboard
```bash
# Start monitoring dashboard
python -m src.monitor

# Access dashboard
open http://localhost:5000
```

## Troubleshooting

### Common Issues

#### API Key Not Working
- Verify OpenRouter API key is correct
- Check API key has sufficient credits
- Ensure environment variable is set correctly

#### Build Failures
- Check GitHub Actions logs
- Verify all secrets are set
- Test locally with same parameters

#### Translation Errors
- Check OpenRouter service status
- Verify API key permissions
- Review translation logs

#### Performance Issues
- Monitor resource usage
- Check for memory leaks
- Optimize batch sizes

### Getting Help
- Check logs in `data/` directory
- Review GitHub Actions workflow logs
- Check monitoring dashboard
- Open issue on GitHub

## Advanced Configuration

### Batch Translation
```bash
# Initialize batch queue
python -m src.batch_translate init --years 2024,2025 --limit 1000

# Start workers
python -m src.batch_translate start --workers 10

# Monitor progress
python -m src.batch_translate status

# Stop workers
python -m src.batch_translate stop
```

### Preparing Records
If you don’t use BrightData, provide normalized records JSON under `data/records/` via your own harvester or manual curation. Then:
```bash
# Select and fetch papers (from your records)
python -m src.select_and_fetch --records data/records/<your_records>.json --limit 50

# Translate selected papers
python -m src.pipeline --limit 50
```

### Performance Optimization
```bash
# Optimize search index
python -c "from src.monitoring import monitoring_service; print(monitoring_service.optimize_site())"

# Clean up old data
python -c "from src.monitoring import monitoring_service; monitoring_service.cleanup_old_data(30)"
```

## Security Considerations

### API Keys
- Never commit API keys to repository
- Use environment variables or secrets
- Rotate keys regularly
- Monitor usage and costs

### Access Control
- Use strong passwords for monitoring dashboard
- Enable HTTPS in production
- Regular security audits
- Monitor access logs

### Data Protection
- Encrypt sensitive data
- Regular backups
- Secure data transmission
- Compliance with data protection regulations

## Maintenance

### Regular Tasks
- Monitor translation costs
- Clean up old data
- Update dependencies
- Review logs for errors

### Backup Strategy
- Regular database backups
- Version control for code
- Document configuration changes
- Test restore procedures

### Monitoring
- Set up alerts for failures
- Monitor performance metrics
- Track usage patterns
- Regular health checks
