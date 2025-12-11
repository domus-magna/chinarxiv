#!/usr/bin/env python3
"""
Script to help create Cloudflare API token for GitHub Actions.
"""

import webbrowser
import subprocess
import sys
import argparse

def open_browser():
    """Open Cloudflare API tokens page in browser."""
    url = "https://dash.cloudflare.com/profile/api-tokens"
    print(f"🌐 Opening {url} in your browser...")
    webbrowser.open(url)
    return True

def get_token_from_user():
    """Get API token from user input."""
    print("\n📝 Please follow these steps:")
    print("1. Click 'Create Token'")
    print("2. Use 'Custom token' template")
    print("3. Set permissions:")
    print("   - Account: Cloudflare Pages:Edit")
    print("   - Account Resources: Include your account")
    print("4. Click 'Continue to summary'")
    print("5. Click 'Create Token'")
    print("6. Copy the token")

    token = input("\n🔑 Paste your Cloudflare API token here: ").strip()

    if not token:
        print("❌ No token provided")
        return None

    if not token.startswith(('sk-', 'cf-')):
        print("⚠️  Warning: Token doesn't look like a typical Cloudflare token")
        confirm = input("Continue anyway? (y/N): ").strip().lower()
        if confirm != 'y':
            return None

    return token

def detect_repository():
    """Detect the current repository from git remote."""
    try:
        result = subprocess.run([
            "git", "remote", "get-url", "origin"
        ], capture_output=True, text=True, check=True)

        # Extract owner/repo from git URL
        url = result.stdout.strip()
        if url.startswith("https://github.com/"):
            repo_path = url.replace("https://github.com/", "").replace(".git", "")
            return repo_path
        elif url.startswith("git@github.com:"):
            repo_path = url.replace("git@github.com:", "").replace(".git", "")
            return repo_path
        else:
            return None

    except subprocess.CalledProcessError:
        return None

def add_token_to_github(token, repo=None):
    """Add token to GitHub secrets."""
    print("\n🔧 Adding token to GitHub secrets...")

    # Detect repository if not provided
    if not repo:
        repo = detect_repository()
        if not repo:
            print("❌ Could not detect repository. Please specify with --repo")
            return False
        print(f"📁 Detected repository: {repo}")

    try:
        subprocess.run([
            "gh", "secret", "set", "CF_API_TOKEN",
            "--repo", repo,
            "--body", token
        ], capture_output=True, text=True, check=True)

        print("✅ CF_API_TOKEN added to GitHub secrets")
        return True

    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to add token to GitHub: {e.stderr}")
        return False

def verify_secrets(repo=None):
    """Verify all secrets are set."""
    print("\n🔍 Verifying GitHub secrets...")

    # Detect repository if not provided
    if not repo:
        repo = detect_repository()
        if not repo:
            print("❌ Could not detect repository. Please specify with --repo")
            return False

    secrets = ["CF_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "OPENROUTER_API_KEY"]

    for secret in secrets:
        try:
            result = subprocess.run([
                "gh", "secret", "list", "--repo", repo
            ], capture_output=True, text=True, check=True)

            if secret in result.stdout:
                print(f"✅ {secret} is set")
            else:
                print(f"❌ {secret} is not set")

        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to check secrets: {e.stderr}")

def main():
    parser = argparse.ArgumentParser(description="Cloudflare API Token Setup")
    parser.add_argument("--repo", help="GitHub repository (owner/repo) - auto-detected if not provided")
    parser.add_argument("--verify-only", action="store_true", help="Only verify existing secrets")

    args = parser.parse_args()

    print("🚀 Cloudflare API Token Setup")
    print("=" * 50)

    # Verify only mode
    if args.verify_only:
        verify_secrets(args.repo)
        return 0

    # Open browser
    if not open_browser():
        print("❌ Failed to open browser")
        return 1

    # Get token from user
    token = get_token_from_user()
    if not token:
        print("❌ No token provided")
        return 1

    # Add token to GitHub
    if not add_token_to_github(token, args.repo):
        return 1

    # Verify secrets
    verify_secrets(args.repo)

    print("\n" + "=" * 50)
    print("🎉 Cloudflare API token setup complete!")
    print("\nNext steps:")
    print("1. Go to GitHub Actions tab")
    print("2. Run 'build-and-deploy' workflow")
    print("3. Monitor the deployment")
    print("4. Verify site loads at your Cloudflare Pages URL")

    return 0

if __name__ == "__main__":
    sys.exit(main())
