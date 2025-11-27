# Report Problem Feature - Manual Setup Steps

This document lists the manual steps needed to complete the Report Problem feature setup.

## 1. Add Claude Triage Workflow to Private Repo

The workflow file cannot be pushed via CLI due to OAuth scope limitations.

**Steps:**
1. Go to: https://github.com/domus-magna/chinarxiv-reports/new/main/.github/workflows
2. Name the file: `issue-triage-claude.yml`
3. Copy the contents from: `docs/reports-repo/issue-triage-claude.yml` (in this repo)
4. Commit directly to main

## 2. Set Up GitHub Secrets in Private Repo

Go to: https://github.com/domus-magna/chinarxiv-reports/settings/secrets/actions

Add this secret:
- `CLAUDE_CODE_OAUTH_TOKEN` - Copy from main repo's secrets (same token works)

## 3. Create GitHub Labels in Private Repo

Go to: https://github.com/domus-magna/chinarxiv-reports/labels

Create these labels:
- `user-report` - Default for all user submissions
- `triage-ai` - Triggers AI analysis
- `translation` - Translation quality issues
- `figure` - Figure rendering issues
- `site-bug` - Website functionality bugs
- `feature` - Feature requests
- `security-review` - Flagged as potentially malicious
- `needs-info` - Needs clarification
- `bug-confirmed` - Bug verified
- `cannot-reproduce` - Bug not reproducible
- `feature-assessed` - Feature analyzed

## 4. Set Up Cloudflare Turnstile

1. Go to: https://dash.cloudflare.com/ → Turnstile
2. Click "Add widget"
3. Name: `chinarxiv-report`
4. Domain: `chinarxiv.org`
5. Widget Mode: **Invisible** (recommended) or Managed
6. Copy:
   - **Site Key** (public) - Add to `assets/site.js` (replace `YOUR_SITE_KEY`)
   - **Secret Key** - Add to Worker secrets

## 5. Create GitHub Token for Worker

1. Go to: https://github.com/settings/tokens
2. Generate new token (classic) with scopes:
   - `repo` (for private repo access)
   - OR create a fine-grained token with:
     - Repository: `domus-magna/chinarxiv-reports`
     - Permissions: Issues (Read & Write)
3. Copy the token

## 6. Deploy Report API Worker

```bash
cd workers/report-api

# Install dependencies
npm install

# Set secrets
wrangler secret put GITHUB_TOKEN
# Paste the GitHub token from step 5

wrangler secret put TURNSTILE_SECRET
# Paste the Turnstile secret key from step 4

# Deploy
npm run deploy:production
```

## 7. Update Frontend with Turnstile Site Key

Edit `assets/site.js` and replace `YOUR_SITE_KEY` with the actual Turnstile site key.

Then re-render and deploy the site.

## Testing

1. Visit a paper page on the site
2. Click "Report Problem" in the sidebar
3. Fill out the form and submit
4. Check https://github.com/domus-magna/chinarxiv-reports/issues for the new issue
5. Verify the `triage-ai` label triggers the Claude workflow

## Troubleshooting

**Issue not created:**
- Check Worker logs: `wrangler tail --env production`
- Verify GitHub token has correct permissions
- Check rate limiting isn't blocking

**Claude workflow not running:**
- Ensure `triage-ai` label exists
- Check workflow file was saved correctly
- Verify `CLAUDE_CODE_OAUTH_TOKEN` secret is set

**Turnstile failing:**
- Ensure site key matches domain
- Check secret key is correct in Worker
- For local testing, Turnstile validation is optional (will pass if no secret configured)
