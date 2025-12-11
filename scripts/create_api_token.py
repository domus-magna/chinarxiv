#!/usr/bin/env python3
"""
Script to create Cloudflare API token using Cloudflare API.
"""

import sys
import subprocess

def get_auth_token():
    """Get auth token from Wrangler config."""
    try:
        # Try to get token from wrangler config
        subprocess.run([
            "wrangler", "whoami"
        ], capture_output=True, text=True, check=True)

        # Extract token from output (this is a simplified approach)
        # In practice, we'd need to parse the wrangler config file
        print("✅ Wrangler is authenticated")
        return "oauth_token"  # Placeholder

    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to get auth token: {e.stderr}")
        return None

def create_api_token():
    """Create API token using Cloudflare API."""
    print("🔧 Creating Cloudflare API token...")

    # For now, we'll guide the user to create it manually
    print("\n📝 Manual API Token Creation:")
    print("1. Go to: https://dash.cloudflare.com/profile/api-tokens")
    print("2. Click 'Create Token'")
    print("3. Use 'Custom token' template")
    print("4. Set permissions:")
    print("   - Account: Cloudflare Pages:Edit")
    print("   - Account Resources: Include your account")
    print("5. Click 'Continue to summary'")
    print("6. Click 'Create Token'")
    print("7. Copy the token")

    # Get token from user
    token = input("\n🔑 Paste your Cloudflare API token here: ").strip()

    if not token:
        print("❌ No token provided")
        return None

    return token

def add_to_github(token):
    """Add token to GitHub secrets."""
    print("\n🔧 Adding token to GitHub secrets...")

    try:
        subprocess.run([
            "gh", "secret", "set", "CF_API_TOKEN",
            "--repo", "seconds-0/chinaxiv-english",
            "--body", token
        ], capture_output=True, text=True, check=True)

        print("✅ CF_API_TOKEN added to GitHub secrets")
        return True

    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to add token to GitHub: {e.stderr}")
        return False

def main():
    print("🚀 Cloudflare API Token Creation")
    print("=" * 50)

    # Create API token
    token = create_api_token()
    if not token:
        return 1

    # Add to GitHub
    if not add_to_github(token):
        return 1

    print("\n" + "=" * 50)
    print("🎉 API token setup complete!")
    print("\nNext steps:")
    print("1. Test GitHub Actions workflow")
    print("2. Monitor deployment")
    print("3. Verify site functionality")

    return 0

if __name__ == "__main__":
    sys.exit(main())
