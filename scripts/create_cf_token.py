#!/usr/bin/env python3
"""
Create Cloudflare API token for GitHub Actions.
"""

import requests
import subprocess
import sys
import os

def get_auth_token():
    """Get auth token from Wrangler config."""
    try:
        # Get token from wrangler config file
        config_path = os.path.expanduser("~/.wrangler/config/default.toml")
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                content = f.read()
                # Extract token (simplified parsing)
                for line in content.split('\n'):
                    if 'oauth_token' in line:
                        return line.split('=')[1].strip().strip('"')
        return None
    except Exception as e:
        print(f"Error reading wrangler config: {e}")
        return None

def create_api_token():
    """Create API token using Cloudflare API."""
    print("🔧 Creating Cloudflare API token...")

    # Get auth token
    auth_token = get_auth_token()
    if not auth_token:
        print("❌ Could not get auth token from Wrangler config")
        return None

    # API token payload
    payload = {
        "name": "GitHub Actions - ChinaXiv Translations",
        "policies": [
            {
                "effect": "allow",
                "resources": {
                    "com.cloudflare.api.account": {
                        "f8f951bd34fc7d5e0c17c7d00cfc37e8": "*"
                    }
                },
                "permission_groups": [
                    {
                        "id": "c8fed203ed3043cba015a93c5f0bad6f",  # Cloudflare Pages:Edit
                        "name": "Cloudflare Pages:Edit"
                    }
                ]
            }
        ],
        "expires_on": None,
        "not_before": None,
        "condition": {}
    }

    # Create token
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            "https://api.cloudflare.com/client/v4/user/tokens",
            headers=headers,
            json=payload
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                token = result["result"]["value"]
                print("✅ API token created successfully")
                return token
            else:
                print(f"❌ API error: {result.get('errors', [])}")
                return None
        else:
            print(f"❌ HTTP error: {response.status_code}")
            print(f"Response: {response.text}")
            return None

    except Exception as e:
        print(f"❌ Error creating token: {e}")
        return None

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
        print("\n❌ Failed to create API token")
        print("Please create it manually:")
        print("1. Go to: https://dash.cloudflare.com/profile/api-tokens")
        print("2. Click 'Create Token'")
        print("3. Use 'Custom token' template")
        print("4. Set permissions: Cloudflare Pages:Edit")
        print("5. Copy the token")
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
