#!/bin/bash
# Sync GitHub token from keyring to .env
# Run this after: gh auth refresh -s workflow

set -e

# Get the active account's token from keyring (without GH_TOKEN override)
NEW_TOKEN=$(unset GH_TOKEN && gh auth token)

if [ -z "$NEW_TOKEN" ]; then
    echo "Error: Could not get token from gh auth"
    exit 1
fi

# Verify scopes include workflow
SCOPES=$(unset GH_TOKEN && gh auth status 2>&1 | grep "Token scopes" || true)
if ! echo "$SCOPES" | grep -q "workflow"; then
    echo "Warning: Token doesn't have 'workflow' scope"
    echo "Run: gh auth refresh -s workflow"
    exit 1
fi

# Update .env
ENV_FILE="${1:-.env}"
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found"
    exit 1
fi

# Replace GH_TOKEN line
if grep -q "^GH_TOKEN=" "$ENV_FILE"; then
    sed -i.bak "s|^GH_TOKEN=.*|GH_TOKEN=$NEW_TOKEN|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
    echo "Updated GH_TOKEN in $ENV_FILE"
else
    echo "GH_TOKEN=$NEW_TOKEN" >> "$ENV_FILE"
    echo "Added GH_TOKEN to $ENV_FILE"
fi

# Verify
echo "Token synced for account: $(unset GH_TOKEN && gh api user --jq '.login')"
