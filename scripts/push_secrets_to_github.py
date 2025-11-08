#!/usr/bin/env python3
"""
Push API keys from .env file to GitHub Secrets.

This script reads the .env file and pushes all required secrets to GitHub.
It uses the GitHub CLI (gh) to set secrets securely.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Required secrets for the pipeline
REQUIRED_SECRETS = [
    "OPENROUTER_API_KEY",
    "BRIGHTDATA_API_KEY",
    "BRIGHTDATA_ZONE",
    "BACKBLAZE_KEY_ID",
    "BACKBLAZE_APPLICATION_KEY",
    "BACKBLAZE_S3_ENDPOINT",
    "BACKBLAZE_BUCKET",
    "BACKBLAZE_PREFIX",  # Optional but included
    "CF_API_TOKEN",
    "DISCORD_WEBHOOK_URL",  # Optional but included
]

OPTIONAL_SECRETS = [
    "BACKBLAZE_PREFIX",
    "DISCORD_WEBHOOK_URL",
]


def load_env_file(env_path: Path = Path(".env")) -> Dict[str, str]:
    """Load environment variables from .env file."""
    env_vars = {}
    if not env_path.exists():
        print(f"❌ .env file not found at {env_path}")
        return env_vars
    
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue
            # Parse KEY=VALUE
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                env_vars[key] = value
    
    return env_vars


def check_gh_cli() -> bool:
    """Check if GitHub CLI is installed and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            check=True
        )
        print("✅ GitHub CLI is installed")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ GitHub CLI is not installed or not in PATH")
        print("   Install from: https://cli.github.com/")
        return False
    
    # Check authentication
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=True
        )
        print("✅ GitHub CLI is authenticated")
    except subprocess.CalledProcessError:
        print("❌ GitHub CLI is not authenticated")
        print("   Run: gh auth login")
        return False
    
    return True


def get_repo_name() -> Optional[str]:
    """Get the current repository name in owner/repo format."""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=True
        )
        repo = result.stdout.strip().strip('"')
        return repo
    except subprocess.CalledProcessError:
        # Try git remote as fallback
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=True
            )
            url = result.stdout.strip()
            # Extract owner/repo from URL
            if "github.com" in url:
                parts = url.replace(".git", "").split("github.com/")[-1]
                return parts
        except subprocess.CalledProcessError:
            pass
        return None


def set_github_secret(repo: str, secret_name: str, secret_value: str) -> bool:
    """Set a GitHub secret using gh CLI."""
    try:
        # Use gh secret set which reads from stdin for security
        process = subprocess.Popen(
            ["gh", "secret", "set", secret_name, "--repo", repo],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=secret_value)
        
        if process.returncode == 0:
            print(f"  ✅ {secret_name}")
            return True
        else:
            print(f"  ❌ {secret_name}: {stderr.strip()}")
            return False
    except Exception as e:
        print(f"  ❌ {secret_name}: {str(e)}")
        return False


def main() -> int:
    """Main function to push secrets from .env to GitHub."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Push secrets from .env to GitHub")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()
    
    print("🚀 Pushing secrets from .env to GitHub")
    print("=" * 60)
    
    # Check prerequisites
    if not check_gh_cli():
        return 1
    
    # Get repository name
    repo = get_repo_name()
    if not repo:
        print("❌ Could not determine repository name")
        print("   Make sure you're in a git repository with GitHub remote")
        return 1
    
    print(f"📦 Repository: {repo}\n")
    
    # Load .env file
    print("📖 Loading .env file...")
    env_vars = load_env_file()
    if not env_vars:
        print("❌ No environment variables found in .env file")
        return 1
    
    print(f"✅ Loaded {len(env_vars)} variables from .env\n")
    
    # Check which secrets are present
    missing = []
    present = []
    for secret in REQUIRED_SECRETS:
        if secret in env_vars and env_vars[secret]:
            present.append(secret)
        else:
            if secret not in OPTIONAL_SECRETS:
                missing.append(secret)
    
    if missing:
        print("⚠️  Missing required secrets in .env:")
        for secret in missing:
            print(f"   - {secret}")
        print()
    
    if not present:
        print("❌ No secrets found in .env file")
        return 1
    
    # Confirm before pushing
    print(f"📤 Ready to push {len(present)} secrets to GitHub:")
    for secret in present:
        value_preview = env_vars[secret][:20] + "..." if len(env_vars[secret]) > 20 else env_vars[secret]
        print(f"   - {secret}: {value_preview}")
    
    if not args.yes:
        print("\n⚠️  This will overwrite existing secrets in GitHub!")
        response = input("Continue? (yes/no): ").strip().lower()
        if response not in ["yes", "y"]:
            print("❌ Cancelled")
            return 1
    else:
        print("\n⚠️  Pushing secrets (--yes flag provided)")
    
    # Push secrets
    print("\n🔐 Pushing secrets to GitHub...")
    success_count = 0
    failed = []
    
    for secret in present:
        if set_github_secret(repo, secret, env_vars[secret]):
            success_count += 1
        else:
            failed.append(secret)
    
    # Summary
    print("\n" + "=" * 60)
    print(f"✅ Successfully pushed {success_count} secrets")
    if failed:
        print(f"❌ Failed to push {len(failed)} secrets:")
        for secret in failed:
            print(f"   - {secret}")
        return 1
    
    print("\n🎉 All secrets pushed successfully!")
    print("\nNext steps:")
    print("1. Verify secrets in GitHub: Settings → Secrets and variables → Actions")
    print("2. Test a workflow run to ensure secrets are working")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

