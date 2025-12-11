#!/usr/bin/env python3
"""
Add GitHub repository secrets via API.
Requires GitHub Personal Access Token with repo permissions.
"""

import argparse
import base64
import os
import sys

try:
    import requests
except ImportError:
    print("❌ requests library not found. Install with: pip install requests")
    sys.exit(1)

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
except ImportError:
    print("❌ cryptography library not found. Install with: pip install cryptography")
    sys.exit(1)


def get_repo_public_key(owner: str, repo: str, token: str) -> tuple[str, str]:
    """Get repository public key for encryption."""
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/public-key"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()

    data = response.json()
    return data["key"], data["key_id"]


def encrypt_secret(secret_value: str, public_key: str) -> str:
    """Encrypt secret value using repository public key."""
    # Decode the public key
    public_key_bytes = base64.b64decode(public_key)
    public_key_obj = load_pem_public_key(public_key_bytes)

    # Encrypt the secret
    encrypted_bytes = public_key_obj.encrypt(
        secret_value.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )

    # Return base64 encoded encrypted value
    return base64.b64encode(encrypted_bytes).decode("utf-8")


def add_repo_secret(owner: str, repo: str, secret_name: str, secret_value: str, token: str) -> bool:
    """Add secret to GitHub repository."""
    try:
        # Get repository public key
        public_key, key_id = get_repo_public_key(owner, repo, token)

        # Encrypt the secret
        encrypted_value = encrypt_secret(secret_value, public_key)

        # Add the secret
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/{secret_name}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        data = {
            "encrypted_value": encrypted_value,
            "key_id": key_id
        }

        response = requests.put(url, headers=headers, json=data)
        response.raise_for_status()

        return True

    except requests.exceptions.RequestException as e:
        print(f"❌ API request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        return False
    except Exception as e:
        print(f"❌ Encryption failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Add GitHub repository secret via API")
    parser.add_argument("--owner", required=True, help="Repository owner (e.g., 'seconds-0')")
    parser.add_argument("--repo", required=True, help="Repository name (e.g., 'chinaxiv-english')")
    parser.add_argument("--secret-name", required=True, help="Secret name (e.g., 'DISCORD_WEBHOOK_URL')")
    parser.add_argument("--secret-value", help="Secret value (will prompt if not provided)")
    parser.add_argument("--token", help="GitHub Personal Access Token (will use GITHUB_TOKEN env var if not provided)")

    args = parser.parse_args()

    # Get GitHub token
    token = args.token or os.getenv("GITHUB_TOKEN")
    if not token:
        print("❌ GitHub token required. Set GITHUB_TOKEN environment variable or use --token")
        print("Create a token at: https://github.com/settings/tokens")
        print("Required permissions: repo")
        sys.exit(1)

    # Get secret value
    secret_value = args.secret_value
    if not secret_value:
        secret_value = input(f"Enter value for secret '{args.secret_name}': ")

    if not secret_value:
        print("❌ Secret value cannot be empty")
        sys.exit(1)

    print(f"Adding secret '{args.secret_name}' to {args.owner}/{args.repo}...")

    success = add_repo_secret(args.owner, args.repo, args.secret_name, secret_value, token)

    if success:
        print(f"✅ Secret '{args.secret_name}' added successfully!")
        print(f"🔗 View secrets: https://github.com/{args.owner}/{args.repo}/settings/secrets/actions")
    else:
        print(f"❌ Failed to add secret '{args.secret_name}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
