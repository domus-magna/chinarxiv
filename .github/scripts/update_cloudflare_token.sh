#!/bin/bash
set -e

echo "🔧 Cloudflare API Token Setup Helper"
echo "======================================"
echo ""
echo "Your Cloudflare Account ID: f8f951bd34fc7d5e0c17c7d00cfc37e8"
echo ""
echo "📋 Steps to create a new API token:"
echo ""
echo "1. Browser opened at: https://dash.cloudflare.com/profile/api-tokens"
echo "2. Click 'Create Token' → 'Create Custom Token'"
echo "3. Configure permissions:"
echo "   ✓ User -> User Details -> Read"
echo "   ✓ Account -> Cloudflare Pages -> Edit"
echo "4. Set Account Resources:"
echo "   Account: f8f951bd34fc7d5e0c17c7d00cfc37e8"
echo "5. Click 'Continue to summary' → 'Create Token'"
echo "6. Copy the generated token"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Prompt for token
read -p "Paste your new Cloudflare API token here: " CF_TOKEN

if [ -z "$CF_TOKEN" ]; then
  echo "❌ No token provided. Exiting."
  exit 1
fi

echo ""
echo "🔍 Validating token..."

# Test the token
VALIDATION=$(curl -s -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer $CF_TOKEN" \
  -H "Content-Type: application/json")

if echo "$VALIDATION" | grep -q '"success":true'; then
  echo "✅ Token is valid!"

  # Update GitHub secret
  echo ""
  echo "🔐 Updating GitHub secret CF_API_TOKEN..."
  echo "$CF_TOKEN" | gh secret set CF_API_TOKEN -R domus-magna/chinarxiv

  echo "✅ GitHub secret updated successfully!"
  echo ""
  echo "🎉 Done! You can now re-run the backfill workflow."
  echo ""
  echo "To test:"
  echo "  gh workflow run backfill.yml --ref main -f month=202510 -f workers=20 -f deploy=true"
else
  echo "❌ Token validation failed:"
  echo "$VALIDATION" | python3 -m json.tool 2>/dev/null || echo "$VALIDATION"
  exit 1
fi
