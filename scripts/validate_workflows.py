#!/usr/bin/env python3
"""
Validate GitHub Actions workflow files.
Checks YAML syntax and validates secret references.
"""

import yaml
import sys
from pathlib import Path

def validate_yaml(file_path):
    """Validate YAML syntax."""
    try:
        with open(file_path, 'r') as f:
            yaml.safe_load(f)
        return True, None
    except yaml.YAMLError as e:
        return False, str(e)

def validate_secrets(file_path):
    """Validate that referenced secrets exist."""
    required_secrets = {
        'OPENROUTER_API_KEY',
        'CF_API_TOKEN',
        'CLOUDFLARE_ACCOUNT_ID',
        'DISCORD_WEBHOOK_URL'
    }

    try:
        with open(file_path, 'r') as f:
            content = f.read()

        missing_secrets = []
        for secret in required_secrets:
            if f'secrets.{secret}' in content and secret not in content:
                missing_secrets.append(secret)

        return len(missing_secrets) == 0, missing_secrets
    except Exception as e:
        return False, [str(e)]

def main():
    """Main validation function."""
    workflows_dir = Path('.github/workflows')

    if not workflows_dir.exists():
        print("❌ .github/workflows directory not found")
        return 1

    errors = []

    for workflow_file in workflows_dir.glob('*.yml'):
        print(f"🔍 Validating {workflow_file}...")

        # Validate YAML syntax
        is_valid, error = validate_yaml(workflow_file)
        if not is_valid:
            errors.append(f"{workflow_file}: YAML syntax error - {error}")
            continue

        # Validate secrets
        secrets_valid, missing = validate_secrets(workflow_file)
        if not secrets_valid:
            errors.append(f"{workflow_file}: Missing secret references - {missing}")

        print(f"✅ {workflow_file} - Valid")

    if errors:
        print("\n❌ Validation errors found:")
        for error in errors:
            print(f"  - {error}")
        return 1
    else:
        print("\n✅ All workflows validated successfully")
        return 0

if __name__ == "__main__":
    sys.exit(main())
