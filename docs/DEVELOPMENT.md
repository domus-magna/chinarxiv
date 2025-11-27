# Development Guide

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- Git

### Setup
```bash
# Clone repository
git clone https://github.com/your-username/chinarxiv.git
cd chinarxiv

# Install Python dependencies
pip install -r requirements.txt

# Install Wrangler CLI
npm install -g wrangler

# Login to Cloudflare
wrangler login
```

## Development Commands

### Using the Development Script
```bash
# Build the site
./scripts/dev.sh build

# Deploy to Cloudflare Pages
./scripts/dev.sh deploy

# Run tests
./scripts/dev.sh test

# Run translation pipeline (5 papers)
./scripts/dev.sh pipeline

# Run translation pipeline (10 papers)
./scripts/dev.sh pipeline 10

# Start local server (port 8001)
./scripts/dev.sh serve

# Start local server (port 3000)
./scripts/dev.sh serve 3000

# Build and start local server
./scripts/dev.sh dev

# Full workflow: test, build, pipeline, deploy
./scripts/dev.sh full
```

### Manual Commands
```bash
# Build site
python -m src.render
python -m src.search_index

# Deploy to Cloudflare Pages
wrangler pages deploy site --project-name chinarxiv

# Start local server
python -m http.server -d site 8001

# Run tests
python -m pytest tests/ -v

# Run translation pipeline
python -m src.pipeline --limit 5
```

## Wrangler CLI Commands

### Authentication
```bash
# Login to Cloudflare
wrangler login

# Check authentication status
wrangler whoami

# Logout
wrangler logout
```

### Pages Management
```bash
# List Pages projects
wrangler pages project list

# Create new project
wrangler pages project create project-name --production-branch main

# Deploy directory to Pages
wrangler pages deploy site --project-name chinarxiv

# List deployments
wrangler pages deployment list --project-name chinarxiv

# View deployment details
wrangler pages deployment tail --project-name chinarxiv
```

### Custom Domains
```bash
# Add custom domain
wrangler pages domain add chinarxiv yourdomain.com

# List domains
wrangler pages domain list chinarxiv

# Remove domain
wrangler pages domain remove chinarxiv yourdomain.com
```

## GitHub Actions

### Workflows
- **Daily Build** (`.github/workflows/build.yml`): Automated daily deployment
- **Configurable Backfill** (`.github/workflows/backfill.yml`): Configurable parallel processing via inputs (1-10 jobs, 1-100 workers per job)

### Required Secrets
- `CF_API_TOKEN`: Cloudflare API token
- `CLOUDFLARE_ACCOUNT_ID`: Cloudflare Account ID
- `OPENROUTER_API_KEY`: OpenRouter API key
- `DISCORD_WEBHOOK_URL`: Discord webhook (optional)

## Local Development Workflow

### 1. Daily Development
```bash
# Start development server
./scripts/dev.sh dev

# Make changes to code
# Test locally at http://localhost:8001

# Deploy changes
./scripts/dev.sh deploy
```

### 2. Translation Pipeline Testing
```bash
# Test with small batch
./scripts/dev.sh pipeline 5

# Check results
./scripts/dev.sh serve

# Deploy to production
./scripts/dev.sh deploy
```

### 3. Full Testing
```bash
# Run complete test suite
./scripts/dev.sh full
```

## Configuration Files

### wrangler.toml
```toml
name = "chinarxiv"
compatibility_date = "2024-01-01"

# Pages configuration
pages_build_output_dir = "site"

# Environment variables
[vars]
OPENROUTER_API_KEY = ""
DISCORD_WEBHOOK_URL = ""
```

### .env
```bash
# OpenRouter API key for translations
OPENROUTER_API_KEY=your-api-key-here

# Discord webhook for notifications (optional)
DISCORD_WEBHOOK_URL=your-webhook-url-here
```

## Troubleshooting

### Common Issues

#### 1. Wrangler Authentication
```bash
# Re-authenticate
wrangler logout
wrangler login
```

#### 2. Build Failures
```bash
# Check Python dependencies
pip install -r requirements.txt

# Check Node.js version
node --version

# Reinstall Wrangler
npm install -g wrangler
```

#### 3. Deployment Issues
```bash
# Check project exists
wrangler pages project list

# Check deployment status
wrangler pages deployment list --project-name chinarxiv

# View deployment logs
wrangler pages deployment tail --project-name chinarxiv
```

#### 4. Translation Failures
```bash
# Check API key
echo $OPENROUTER_API_KEY

# Test API connection
curl -X POST "https://openrouter.ai/api/v1/chat/completions" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek/deepseek-v3.2-exp", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 10}'
```

### Debug Commands
```bash
# Check Wrangler version
wrangler --version

# Check Node.js version
node --version

# Check Python version
python --version

# Check site directory
ls -la site/

# Check deployment status
wrangler pages deployment list --project-name chinarxiv
```

## Performance Optimization

### Local Development
- Use `./scripts/dev.sh serve` for fast local testing
- Use `./scripts/dev.sh dev` for build + serve workflow
- Use `./scripts/dev.sh pipeline N` for translation testing

### Production Deployment
- Use GitHub Actions for automated deployment
- Use Wrangler CLI for manual deployment
- Monitor deployment status and logs

### Translation Pipeline
- Start with small batches (5-10 papers)
- Scale up gradually (50-100 papers)
- Use parallel processing for large batches

## Best Practices

### Code Quality
- Run tests before deployment: `./scripts/dev.sh test`
- Use type hints and docstrings
- Follow PEP 8 style guidelines
- Use conventional commit messages

### Deployment
- Test locally before deploying
- Use descriptive commit messages
- Monitor deployment status
- Check site functionality after deployment

### Translation Pipeline
- Test with small batches first
- Monitor API costs and usage
- Check translation quality
- Use appropriate batch sizes

## Support and Resources

### Documentation
- **Wrangler CLI**: https://developers.cloudflare.com/workers/wrangler/
- **Cloudflare Pages**: https://developers.cloudflare.com/pages/
- **OpenRouter API**: https://openrouter.ai/docs

### Community
- **Cloudflare Discord**: https://discord.gg/cloudflaredevs
- **GitHub Issues**: https://github.com/your-username/chinarxiv/issues

### Tools
- **Development Script**: `./scripts/dev.sh`
- **Test Script**: `python3 scripts/test_wrangler.py`
- **Wrangler CLI**: `wrangler`

---

**Happy coding!** 🚀
