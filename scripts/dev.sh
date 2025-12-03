#!/bin/bash
# Development script for ChinaXiv Translations

set -e

echo "🚀 ChinaXiv Translations Development Script"
echo "============================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if we're in the right directory
if [ ! -f "requirements.txt" ]; then
    print_error "Please run this script from the project root directory"
    exit 1
fi

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check dependencies
print_status "Checking dependencies..."

if ! command_exists python3; then
    print_error "Python 3 is required but not installed"
    exit 1
fi

if ! command_exists node; then
    print_error "Node.js is required but not installed"
    exit 1
fi

if ! command_exists wrangler; then
    print_warning "Wrangler CLI not found, installing..."
    npm install -g wrangler
fi

print_success "All dependencies are available"

# Function to build the site
build_site() {
    print_status "Building site..."
    
    # Render the site
    python3 -m src.render
    if [ $? -eq 0 ]; then
        print_success "Site rendered successfully"
    else
        print_error "Site rendering failed"
        exit 1
    fi
    
    # Build search index
    python3 -m src.search_index
    if [ $? -eq 0 ]; then
        print_success "Search index built successfully"
    else
        print_error "Search index build failed"
        exit 1
    fi
}

# Function to deploy to Cloudflare Pages
deploy_site() {
    print_status "Deploying to Cloudflare Pages..."
    
    wrangler pages deploy site --project-name chinarxiv --commit-message "Local development deployment"
    if [ $? -eq 0 ]; then
        print_success "Site deployed successfully"
        print_status "Visit: https://chinarxiv.org"
    else
        print_error "Deployment failed"
        exit 1
    fi
}

# Function to run tests
run_tests() {
    print_status "Running tests..."
    
    python3 -m pytest tests/ -v
    if [ $? -eq 0 ]; then
        print_success "All tests passed"
    else
        print_error "Tests failed"
        exit 1
    fi
}

# Function to run translation pipeline
run_pipeline() {
    local limit=${1:-5}
    print_status "Running translation pipeline (limit: $limit)..."
    
    # Harvest via BrightData if configured, else try to find existing records
    if [[ -n "$BRIGHTDATA_API_KEY" && -n "$BRIGHTDATA_ZONE" ]]; then
        print_status "Harvesting from ChinaXiv (BrightData, optimized)..."
        python3 -m src.harvest_chinaxiv_optimized --month $(date -u +"%Y%m") || true
    else
        print_warning "Skipping harvest: set BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE to enable"
    fi
    latest=$(ls -1t data/records/*.json 2>/dev/null | head -n1 || echo '')
    if [ -n "$latest" ]; then
        python3 -m src.select_and_fetch --records "$latest" --limit $limit --output data/selected.json
    else
        echo '[]' > data/selected.json
    fi

    # Download PDFs (select_and_fetch no longer does this)
    print_status "Downloading PDFs..."
    python3 scripts/download_missing_pdfs.py --limit $limit || print_warning "Some PDF downloads failed"

    # Translate
    python3 -m src.pipeline --limit $limit
    
    print_success "Translation pipeline completed"
}

# Function to start local server
start_server() {
    local port=${1:-8001}
    print_status "Starting local server on port $port..."
    print_status "Visit: http://localhost:$port"
    print_status "Press Ctrl+C to stop"
    
    python3 -m http.server -d site $port
}

# Function to show help
show_help() {
    echo "Usage: $0 [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  build          Build the site"
    echo "  deploy         Deploy to Cloudflare Pages"
    echo "  test           Run tests"
    echo "  pipeline [N]   Run translation pipeline (default: 5 papers)"
    echo "  serve [PORT]   Start local server (default: 8001)"
    echo "  dev            Build and start local server"
    echo "  full           Build, test, pipeline, and deploy"
    echo "  help           Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 build"
    echo "  $0 deploy"
    echo "  $0 pipeline 10"
    echo "  $0 serve 3000"
    echo "  $0 dev"
    echo "  $0 full"
}

# Main script logic
case "${1:-help}" in
    "build")
        build_site
        ;;
    "deploy")
        build_site
        deploy_site
        ;;
    "test")
        run_tests
        ;;
    "pipeline")
        limit=${2:-5}
        run_pipeline $limit
        ;;
    "serve")
        port=${2:-8001}
        start_server $port
        ;;
    "dev")
        build_site
        start_server
        ;;
    "full")
        run_tests
        build_site
        run_pipeline 10
        deploy_site
        ;;
    "help"|*)
        show_help
        ;;
esac
