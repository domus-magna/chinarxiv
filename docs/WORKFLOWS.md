# GitHub Actions Workflows

## Overview
This document describes the GitHub Actions workflows used for building, testing, and deploying the ChinaXiv Translations project.

## Workflows

### Build Workflow
- **File**: `.github/workflows/build.yml`
- **Schedule**: Daily at 3 AM UTC
- **Purpose**: Harvest (optimized), translate all new items, and deploy site
- **Inputs**: None (translates all newly selected items by default)

#### Features
- Automated daily builds
- Manual dispatch with configurable paper limit
- Full pipeline: harvest (BrightData, optimized: current + previous month) → select (dedupe) → translate (parallel) → render → deploy
- Cloudflare Pages deployment
- Discord notifications

#### Steps
1. Checkout code
2. Setup Python 3.11
3. Install dependencies
4. Run tests (blocking on nightly schedule; non-blocking on push/manual)
5. Build site (select, translate all, render)
6. Deploy to Cloudflare Pages

### Continuous Deployment
- Pushes to `main` automatically run the build-and-deploy workflow and publish the `site/` directory to Cloudflare Pages.
- Internal `[skip ci]` pushes (from dedupe state persists) are isolated by concurrency so they do not cancel an in-flight deployment.

### Backfill Workflow (Targeted by Month)
- **File**: `.github/workflows/backfill.yml`
- **Trigger**: Manual dispatch
- **Purpose**: Backfill a single month end-to-end (harvest → select → translate → render), with optional deploy
- **Inputs**:
  - `month` (YYYYMM): Month to backfill (required)
  - `workers` (default: 20): Parallel translation workers
  - `deploy` (default: true): Deploy site after backfill

#### Features
- Optimized harvester per month
- Single selection pass with dedupe
- Translate all selected items in parallel
- Optional deploy to Cloudflare Pages

#### Steps
1. Checkout code
2. Setup Python 3.11
3. Install dependencies
4. Harvest month via `src.harvest_chinaxiv_optimized`
5. Select new items from `data/records/chinaxiv_YYYYMM.json`
6. Run `src.pipeline --skip-selection --workers N` (translates all, renders site)
7. Optionally deploy via Wrangler Pages
8. Persist dedupe state: workflow uploads `data/seen.json` to Backblaze B2 (`state/seen.json`) so every job hydrates the same dedupe list

## Configuration

### Required Secrets
- `OPENROUTER_API_KEY`: API key for translation service
- `BRIGHTDATA_API_KEY`: BrightData API key (harvest)
- `BRIGHTDATA_ZONE`: BrightData zone name (harvest)
- `CF_API_TOKEN`: Cloudflare API token for deployment
- `DISCORD_WEBHOOK_URL`: Discord webhook for notifications (optional)

### Environment Variables
- `OPENROUTER_API_KEY`: Required for translation
- `DISCORD_WEBHOOK_URL`: Optional for notifications
- `CLOUDFLARE_API_TOKEN`: Required for deployment

## Usage

### Manual Build
1. Go to GitHub Actions tab
2. Select "build-and-deploy" workflow
3. Click "Run workflow"
4. Optionally set paper limit
5. Click "Run workflow"

### Manual Backfill
1. Go to GitHub Actions tab
2. Select "backfill-translations" workflow
3. Click "Run workflow"
4. Configure parameters:
   - Total papers to process
   - Workers per job
   - Number of parallel jobs
5. Click "Run workflow"

## Monitoring

### Build Status
- Check GitHub Actions tab for build status
- Monitor Discord notifications for success/failure
- Review Cloudflare Pages dashboard for deployment status

### Performance Metrics
- Build time: ~10-15 minutes
- Translation time: ~30 seconds per paper
- Deployment time: ~2-3 minutes

## Troubleshooting

### Common Issues
1. **Build Failures**: Check logs in GitHub Actions
2. **Translation Errors**: Verify OpenRouter API key
3. **Deployment Issues**: Check Cloudflare API token
4. **Test Failures**: Review test output and fix issues

### Debug Steps
1. Check GitHub Actions logs
2. Verify secrets are set correctly
3. Test locally with same parameters
4. Check service status (OpenRouter, Cloudflare)

## Best Practices

### Workflow Design
- Use matrix strategy for parallel processing
- Implement proper error handling
- Add timeout limits for long-running jobs
- Use secrets for sensitive data

### Performance Optimization
- Cache dependencies when possible
- Use appropriate worker counts
- Monitor resource usage
- Optimize build steps

### Security
- Never commit secrets to repository
- Use GitHub Secrets for sensitive data
- Implement proper permissions
- Regular security audits

## Future Improvements

### Planned Features
- Automated testing on pull requests
- Performance monitoring and alerting
- Advanced deployment strategies
- Integration with monitoring dashboard

### Optimization Opportunities
- Parallel test execution
- Caching improvements
- Resource optimization
- Build time reduction
