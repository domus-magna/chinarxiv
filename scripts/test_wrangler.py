#!/usr/bin/env python3
"""
Test script to verify Wrangler CLI setup and deployment.
"""

import subprocess
import sys
import os
from pathlib import Path

def run_command(cmd, check=True):
    """Run a command and return the result."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {cmd}")
        print(f"Error: {e.stderr}")
        return None

def check_wrangler_installed():
    """Check if Wrangler CLI is installed."""
    print("🔍 Checking Wrangler CLI installation...")
    result = run_command("wrangler --version", check=False)
    if result is None:
        print("❌ Wrangler CLI is not installed.")
        print("Please install it with: npm install -g wrangler")
        return False
    print(f"✅ Wrangler CLI is installed: {result}")
    return True

def check_wrangler_auth():
    """Check if Wrangler is authenticated."""
    print("\n🔍 Checking Wrangler authentication...")
    result = run_command("wrangler whoami", check=False)
    if result is None:
        print("❌ Wrangler is not authenticated.")
        print("Please run: wrangler login")
        return False
    print(f"✅ Wrangler is authenticated: {result}")
    return True

def check_pages_project():
    """Check if Pages project exists."""
    print("\n🔍 Checking Pages project...")
    result = run_command("wrangler pages project list", check=False)
    if result is None:
        print("❌ Failed to list Pages projects.")
        return False
    
    if "chinaxiv-english" in result:
        print("✅ Pages project 'chinaxiv-english' exists")
        return True
    else:
        print("❌ Pages project 'chinaxiv-english' not found")
        print("Available projects:")
        print(result)
        return False

def check_site_directory():
    """Check if site directory exists."""
    print("\n🔍 Checking site directory...")
    site_dir = Path("site")
    if not site_dir.exists():
        print("❌ Site directory does not exist.")
        print("Please run: python -m src.render")
        return False
    
    files = list(site_dir.glob("*"))
    if not files:
        print("❌ Site directory is empty.")
        print("Please run: python -m src.render")
        return False
    
    print(f"✅ Site directory exists with {len(files)} files")
    return True

def test_deployment():
    """Test deployment with dry run."""
    print("\n🔍 Testing deployment (dry run)...")
    result = run_command("wrangler pages deploy site --project-name chinaxiv-english --dry-run", check=False)
    if result is None:
        print("❌ Deployment test failed")
        return False
    
    print("✅ Deployment test passed")
    return True

def main():
    print("🚀 Wrangler CLI Test Suite")
    print("=" * 50)
    
    # Check prerequisites
    checks = [
        check_wrangler_installed,
        check_wrangler_auth,
        check_pages_project,
        check_site_directory,
        test_deployment
    ]
    
    all_passed = True
    for check in checks:
        if not check():
            all_passed = False
    
    print("\n" + "=" * 50)
    if all_passed:
        print("🎉 All tests passed! Wrangler CLI is ready to use.")
        print("\nNext steps:")
        print("1. Deploy to Pages: wrangler pages deploy site --project-name chinarxiv")
        print("2. Check deployment: wrangler pages deployment list chinarxiv")
        print("3. Visit your site: https://chinarxiv.org")
    else:
        print("❌ Some tests failed. Please fix the issues above.")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
