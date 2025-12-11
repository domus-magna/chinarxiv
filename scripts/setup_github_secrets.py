#!/usr/bin/env python3
"""
Script to help set up GitHub secrets for Cloudflare Pages deployment.
"""

import subprocess
import sys

def run_command(cmd, check=True):
    """Run a command and return the result."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {cmd}")
        print(f"Error: {e.stderr}")
        return None

def check_gh_cli():
    """Check if GitHub CLI is installed."""
    print("🔍 Checking GitHub CLI installation...")
    result = run_command("gh --version", check=False)
    if result is None:
        print("❌ GitHub CLI is not installed.")
        print("Please install it from: https://cli.github.com/")
        return False
    print(f"✅ GitHub CLI is installed: {result}")
    return True

def check_gh_auth():
    """Check if GitHub CLI is authenticated."""
    print("\n🔍 Checking GitHub CLI authentication...")
    result = run_command("gh auth status", check=False)
    if result is None:
        print("❌ GitHub CLI is not authenticated.")
        print("Please run: gh auth login")
        return False
    print("✅ GitHub CLI is authenticated")
    return True

def get_current_repo():
    """Get current repository name."""
    result = run_command("gh repo view --json nameWithOwner -q .nameWithOwner", check=False)
    if result:
        return result.strip('"')
    return None

def setup_secrets():
    """Guide user through setting up secrets."""
    print("\n🔧 GitHub Secrets Setup Guide")
    print("=" * 50)

    # Get repository info
    repo = get_current_repo()
    if not repo:
        print("❌ Could not determine repository name")
        return False

    print(f"Repository: {repo}")

    # Cloudflare Account ID
    account_id = "f8f951bd34fc7d5e0c17c7d00cfc37e8"
    print(f"\n📋 Cloudflare Account ID: {account_id}")

    print("\n🔑 Required GitHub Secrets:")
    print("1. CF_API_TOKEN - Cloudflare API token")
    print("2. CLOUDFLARE_ACCOUNT_ID - Cloudflare Account ID")
    print("3. OPENROUTER_API_KEY - OpenRouter API key")
    print("4. DISCORD_WEBHOOK_URL - Discord webhook (optional)")

    print("\n📝 Setup Instructions:")
    print("1. Go to: https://dash.cloudflare.com/profile/api-tokens")
    print("2. Click 'Create Token'")
    print("3. Use 'Custom token' template")
    print("4. Set permissions:")
    print("   - Account: Cloudflare Pages:Edit")
    print("   - Account Resources: Include your account")
    print("5. Copy the token")

    print("\n🚀 Add secrets to GitHub:")
    print(f"gh secret set CF_API_TOKEN --repo {repo}")
    print(f"gh secret set CLOUDFLARE_ACCOUNT_ID --repo {repo} --body '{account_id}'")
    print("gh secret set OPENROUTER_API_KEY --repo <your-repo>")
    print("gh secret set DISCORD_WEBHOOK_URL --repo <your-repo>  # Optional")

    print("\n✅ After adding secrets, test the workflow:")
    print("1. Go to GitHub Actions tab")
    print("2. Run 'build-and-deploy' workflow")
    print("3. Monitor the deployment")

    return True

def verify_secrets():
    """Verify GitHub secrets are set."""
    print("\n🔍 Verifying GitHub secrets...")

    repo = get_current_repo()
    if not repo:
        print("❌ Could not determine repository name")
        return False

    secrets = ["CF_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "OPENROUTER_API_KEY"]

    for secret in secrets:
        result = run_command(f"gh secret list --repo {repo} | grep {secret}", check=False)
        if result:
            print(f"✅ {secret} is set")
        else:
            print(f"❌ {secret} is not set")

    return True

def main():
    print("🚀 GitHub Secrets Setup for Cloudflare Pages")
    print("=" * 50)

    # Check prerequisites
    if not check_gh_cli():
        return 1

    if not check_gh_auth():
        return 1

    # Setup secrets
    if not setup_secrets():
        return 1

    # Verify secrets
    verify_secrets()

    print("\n" + "=" * 50)
    print("🎉 Setup guide complete!")
    print("\nNext steps:")
    print("1. Create Cloudflare API token")
    print("2. Add secrets to GitHub repository")
    print("3. Test GitHub Actions workflow")
    print("4. Verify site deployment")

    return 0

if __name__ == "__main__":
    sys.exit(main())
