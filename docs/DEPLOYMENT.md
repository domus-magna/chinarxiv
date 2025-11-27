# Production Deployment Guide

## Overview
This guide covers deploying ChinaXiv Translations to production using Cloudflare Pages and GitHub Actions.

## Prerequisites
- Cloudflare account
- GitHub repository
- OpenRouter API key
- Domain name (optional)

## Cloudflare Pages Setup

### 1. Create Cloudflare Account
1. Go to [Cloudflare](https://cloudflare.com)
2. Sign up for a free account
3. Verify your email address

### 2. Add GitHub Repository
1. Go to Cloudflare Dashboard
2. Navigate to Pages
3. Click "Create a project"
4. Select "Connect to Git"
5. Choose your GitHub repository
6. Authorize Cloudflare to access your repository

### 3. Configure Build Settings
- **Project Name**: `chinarxiv`
- **Production Branch**: `main`
- **Build Command**: (leave empty)
- **Build Output Directory**: `site`
- **Root Directory**: (leave empty)

### 4. Environment Variables
Add the following environment variables in Cloudflare Pages:
- `OPENROUTER_API_KEY`: Your OpenRouter API key
- `DISCORD_WEBHOOK_URL`: Discord webhook URL (optional)

## GitHub Actions Setup

### 1. Repository Secrets
Add the following secrets to your GitHub repository:

#### Required Secrets
- `CF_API_TOKEN`: Cloudflare API token with Pages:Edit permission
- `CLOUDFLARE_ACCOUNT_ID`: Your Cloudflare Account ID
- `OPENROUTER_API_KEY`: OpenRouter API key for translations
- `BRIGHTDATA_API_KEY`: BrightData API key (for harvesting)
- `BRIGHTDATA_ZONE`: BrightData zone name (for harvesting)
- `DISCORD_WEBHOOK_URL`: Discord webhook for notifications (optional)

### 2. Create Cloudflare API Token
1. Go to Cloudflare Dashboard
2. Navigate to "My Profile" → "API Tokens"
3. Click "Create Token"
4. Use "Custom token" template
5. Set permissions:
   - `Account:Cloudflare Pages:Edit`
   - `Zone:Zone:Read`
6. Set account resources to your account
7. Set zone resources to your domain (if applicable)
8. Copy the token and add it to GitHub secrets

### 3. Get Cloudflare Account ID
1. Go to Cloudflare Dashboard
2. Select your account
3. Copy the Account ID from the right sidebar
4. Add it to GitHub secrets

## Deployment Process

### Automated Deployment
The system uses GitHub Actions for automated deployment:

1. **Daily Build**: Runs at 3 AM UTC every day
2. **Manual Build**: Can be triggered manually with custom parameters
3. **Backfill**: Processes large batches of papers

### Build Workflow
```yaml
# .github/workflows/build.yml
name: build-and-deploy
on:
  schedule:
    - cron: '0 3 * * *'
  workflow_dispatch:
    inputs:
      limit:
        description: 'Number of papers to process'
        required: false
        default: '5'
```

### Manual Deployment
1. Go to GitHub Actions tab
2. Select "build-and-deploy" workflow
3. Click "Run workflow"
4. Optionally set paper limit
5. Click "Run workflow"

## Custom Domain Setup

### 1. Purchase Domain
- Choose a domain registrar (GoDaddy, Namecheap, etc.)
- Purchase your desired domain
- Note: Cloudflare also offers domain registration

### 2. Add Domain to Cloudflare
1. Go to Cloudflare Dashboard
2. Click "Add a Site"
3. Enter your domain name
4. Choose a plan (Free plan is sufficient)
5. Update nameservers at your registrar

### 3. Configure DNS
1. Go to DNS tab in Cloudflare
2. Add CNAME record:
   - **Name**: `@` (or `www`)
   - **Target**: `chinarxiv.pages.dev`
   - **Proxy**: Enabled (orange cloud)

### 4. SSL Certificate
- Cloudflare automatically issues SSL certificates
- Enable "Always Use HTTPS" in SSL/TLS settings
- Set SSL/TLS encryption mode to "Full (strict)"

## Monitoring and Maintenance

### Health Checks
```bash
# Check site health
curl -I https://your-domain.com

# Check monitoring dashboard
curl -I https://your-domain.com/monitor
```

### Performance Monitoring
- Use Cloudflare Analytics for traffic insights
- Monitor GitHub Actions for build success/failure
- Set up Discord notifications for alerts
- Use monitoring dashboard for system metrics

### Backup Strategy
- GitHub repository serves as code backup
- Cloudflare Pages provides automatic backups
- Regular database exports (if applicable)
- Document configuration changes

## Troubleshooting

### Common Issues

#### Build Failures
1. Check GitHub Actions logs
2. Verify all secrets are set correctly
3. Test locally with same parameters
4. Check OpenRouter API key validity

#### Deployment Issues
1. Verify Cloudflare API token permissions
2. Check Cloudflare Account ID
3. Ensure domain DNS is configured correctly
4. Check SSL certificate status

#### Performance Issues
1. Monitor Cloudflare Analytics
2. Check GitHub Actions build times
3. Review translation costs
4. Optimize batch sizes

### Debug Steps
1. **Check Logs**: Review GitHub Actions workflow logs
2. **Test Locally**: Run the same commands locally
3. **Verify Secrets**: Ensure all secrets are set correctly
4. **Check Status**: Verify service status (OpenRouter, Cloudflare)

## Security Best Practices

### API Key Management
- Never commit API keys to repository
- Use GitHub Secrets for sensitive data
- Rotate keys regularly
- Monitor usage and costs

### Access Control
- Use strong passwords for monitoring dashboard
- Enable two-factor authentication where possible
- Regular security audits
- Monitor access logs

### Data Protection
- Use HTTPS for all communications
- Encrypt sensitive data
- Regular backups
- Compliance with data protection regulations

## Scaling and Optimization

### Performance Optimization
- Use Cloudflare CDN for global distribution
- Optimize images and assets
- Implement caching strategies
- Monitor and optimize build times

### Cost Optimization
- Monitor OpenRouter API usage
- Optimize translation batch sizes
- Use free Cloudflare features
- Regular cost reviews

### Scaling Considerations
- Monitor resource usage
- Plan for increased traffic
- Consider load balancing
- Implement monitoring and alerting

## Advanced Configuration

### Custom Build Process
```yaml
# Custom build configuration
build:
  command: |
    # Harvest removed (Internet Archive). Proceed with existing records.
    python -m src.pipeline --limit 5
    python -m src.render
    python -m src.search_index
  output_directory: site
```

### Environment-Specific Settings
```bash
# Production environment
OPENROUTER_API_KEY=your_production_key
DISCORD_WEBHOOK_URL=your_production_webhook
MONITORING_USERNAME=admin
MONITORING_PASSWORD=secure_password

# Staging environment
OPENROUTER_API_KEY=your_staging_key
DISCORD_WEBHOOK_URL=your_staging_webhook
MONITORING_USERNAME=staging_admin
MONITORING_PASSWORD=staging_password
```

### Custom Domains and Subdomains
```bash
# Main domain
chinaxiv-english.com

# Subdomains
api.chinaxiv-english.com
monitor.chinaxiv-english.com
docs.chinaxiv-english.com
```

## Maintenance Schedule

### Daily Tasks
- Monitor build status
- Check for failed translations
- Review error logs
- Monitor costs

### Weekly Tasks
- Review performance metrics
- Clean up old data
- Update dependencies
- Security audit

### Monthly Tasks
- Full system backup
- Performance optimization
- Cost analysis
- Documentation updates

## Support and Resources

### Documentation
- [Cloudflare Pages Documentation](https://developers.cloudflare.com/pages/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [OpenRouter API Documentation](https://openrouter.ai/docs)

### Community
- GitHub Issues for bug reports
- Discord for community support
- Stack Overflow for technical questions

### Professional Support
- Cloudflare Support (paid plans)
- GitHub Support (paid plans)
- OpenRouter Support (paid plans)
