#!/bin/bash
# Start monitoring dashboard for ChinaXiv Translations

set -e

echo "🚀 Starting ChinaXiv Translations Monitor"
echo "=========================================="

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Please run: python3.11 -m venv .venv"
    exit 1
fi

# Activate virtual environment
source .venv/bin/activate

# Check if Flask is installed
if ! python -c "import flask" 2>/dev/null; then
    echo "❌ Flask not installed. Installing dependencies..."
    pip install -r requirements.txt
fi

# Set environment variables
export MONITORING_USERNAME="${MONITORING_USERNAME:-admin}"
export MONITORING_PASSWORD="${MONITORING_PASSWORD:-chinaxiv2024}"
export MONITORING_PORT="${MONITORING_PORT:-5001}"
export SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 32)}"

echo "📊 Dashboard will be available at: http://localhost:$MONITORING_PORT"
echo "🔐 Username: $MONITORING_USERNAME"
echo "🔑 Password: $MONITORING_PASSWORD"
echo "🌐 Site: https://chinarxiv.org"
echo ""

# Start the monitoring dashboard
python -m src.monitor
