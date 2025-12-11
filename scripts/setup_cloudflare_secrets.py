#!/usr/bin/env python3
"""
Setup script to help configure Cloudflare Pages deployment secrets.
"""

import sys
import subprocess

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
    result = run_command("gh --version", check=False)
    if result is None:
        print("❌ GitHub CLI (gh) is not installed.")
        print("Please install it from: https://cli.github.com/")
        return False
    print("✅ GitHub CLI is installed")
    return True

def check_gh_auth():
    """Check if GitHub CLI is authenticated."""
    result = run_command("gh auth status", check=False)
    if result is None:
        print("❌ GitHub CLI is not authenticated.")
        print("Please run: gh auth login")
        return False
    print("✅ GitHub CLI is authenticated")
    return True

def get_current_secrets():
    """Get current GitHub secrets."""
    print("\n📋 Current GitHub Secrets:")
    result = run_command("gh secret list")
    if result:
        print(result)
    else:
        print("No secrets found or error retrieving secrets")

def add_secret(name, value):
    """Add a GitHub secret."""
    print(f"\n🔐 Adding secret: {name}")
    result = run_command(f'gh secret set {name} --body "{value}"', check=False)
    if result is not None:
        print(f"✅ Successfully added {name}")
        return True
    else:
        print(f"❌ Failed to add {name}")
        return False

def main():
    print("🚀 Cloudflare Pages Setup Helper")
    print("=" * 50)

    # Check prerequisites
    if not check_gh_cli():
        return 1

    if not check_gh_auth():
        return 1

    # Show current secrets
    get_current_secrets()

    print("\n" + "=" * 50)
    print("📝 Required Secrets for Cloudflare Pages:")
    print("=" * 50)

    secrets = {
        "CF_API_TOKEN": "Cloudflare API token with Pages:Edit permission",
        "CLOUDFLARE_ACCOUNT_ID": "Your Cloudflare Account ID",
        "OPENROUTER_API_KEY": "OpenRouter API key for translations"
    }

    print("\nPlease provide the following secrets:")
    print("(Press Enter to skip if already set)")

    for secret_name, description in secrets.items():
        print(f"\n{secret_name}: {description}")
        value = input(f"Enter value for {secret_name}: ").strip()

        if value:
            if add_secret(secret_name, value):
                print(f"✅ {secret_name} added successfully")
            else:
                print(f"❌ Failed to add {secret_name}")
        else:
            print(f"⏭️  Skipped {secret_name}")

    print("\n" + "=" * 50)
    print("🎉 Setup Complete!")
    print("=" * 50)

    print("\nNext steps:")
    print("1. Go to Cloudflare Pages: https://pages.cloudflare.com")
    print("2. Connect your GitHub repository")
    print("3. Configure build settings:")
    print("   - Project name: chinaxiv-english")
    print("   - Build output directory: site")
    print("4. Test deployment by running GitHub Actions workflow")

    print("\n📚 For detailed instructions, see: docs/CLOUDFLARE_SETUP.md")

    return 0

if __name__ == "__main__":
    sys.exit(main())
