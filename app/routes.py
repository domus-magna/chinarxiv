"""
Flask routes for ChinaRxiv English web server.

This module contains all route handlers for the application:
- Homepage with filtering and pagination
- Paper detail pages
- JSON API for AJAX (optional)
"""

from flask import Blueprint, render_template, request, jsonify, abort, current_app
from datetime import datetime
from calendar import monthrange
import logging
from psycopg2.extras import RealDictCursor
from .database import query_papers, get_db
from .filters import build_categories, get_available_filters

logger = logging.getLogger(__name__)

# Create blueprint for main routes
bp = Blueprint('main', __name__)


def parse_date(date_str, default=None):
    """
    Parse date string to ISO format with validation (start of period).

    Accepts multiple date formats and normalizes to ISO timestamp:
    - YYYY-MM-DD: Full date (e.g., '2022-01-15')
    - YYYY-MM: Year and month (e.g., '2022-01')
    - YYYY: Year only (e.g., '2022')

    Args:
        date_str: Date string to parse (or empty string/None)
        default: Default value to return if parsing fails

    Returns:
        str: ISO timestamp (e.g., '2022-01-15T00:00:00') or default value
    """
    if not date_str or not date_str.strip():
        return default
    date_str = date_str.strip()

    # Try formats from most to least specific
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.isoformat()
        except ValueError:
            pass

    logger.warning(f"Invalid date format received: {date_str}")
    return default


def parse_date_end(date_str, default=None):
    """
    Parse date string to ISO format with validation (end of period).

    This function ensures date ranges are INCLUSIVE by returning the last
    moment of the specified period:
    - YYYY: Returns end of year (YYYY-12-31T23:59:59)
    - YYYY-MM: Returns end of month (YYYY-MM-lastday T23:59:59)
    - YYYY-MM-DD: Returns end of day (YYYY-MM-DDT23:59:59)

    Args:
        date_str: Date string to parse (or empty string/None)
        default: Default value to return if parsing fails

    Returns:
        str: ISO timestamp at end of period or default value
    """
    if not date_str or not date_str.strip():
        return default
    date_str = date_str.strip()

    # Try full date format (YYYY-MM-DD) - return end of day
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.replace(hour=23, minute=59, second=59).isoformat()
    except ValueError:
        pass

    # Try year-month format (YYYY-MM) - return end of month
    try:
        dt = datetime.strptime(date_str, '%Y-%m')
        _, last_day = monthrange(dt.year, dt.month)
        return dt.replace(day=last_day, hour=23, minute=59, second=59).isoformat()
    except ValueError:
        pass

    # Try year only format (YYYY) - return end of year
    try:
        dt = datetime.strptime(date_str, '%Y')
        return dt.replace(month=12, day=31, hour=23, minute=59, second=59).isoformat()
    except ValueError:
        logger.warning(f"Invalid date format received: {date_str}")
        return default


def _get_paper_query_args():
    """
    Helper to parse and return common paper query filters from the request.

    Returns:
        dict: Query arguments for query_papers() including:
            - category, date_from, date_to, search, has_figures, subjects
            - page, per_page
    """
    category = request.args.get('category', '')
    date_from = parse_date(request.args.get('from', ''))
    date_to = parse_date_end(request.args.get('to', ''))
    search = request.args.get('q', '')
    # Only set has_figures if the parameter is explicitly provided
    has_figures = request.args.get('figures') == '1' if request.args.get('figures') else None

    # Parse subjects parameter (can have multiple values)
    # request.args.getlist() returns a list of all values for a given key
    subjects = request.args.getlist('subjects')

    try:
        page = int(request.args.get('page', 1))
    except (ValueError, TypeError):
        page = 1

    return {
        'category': category,
        'date_from': date_from,
        'date_to': date_to,
        'search': search,
        'has_figures': has_figures,
        'subjects': subjects,  # Always a list (empty or populated)
        'page': page,
        'per_page': current_app.config['PER_PAGE']
    }


@bp.route('/')
def index():
    """
    Homepage with filtering and pagination.

    Query Parameters:
        category: Category ID (e.g., 'ai_computing')
        from: ISO date string for date range start
        to: ISO date string for date range end
        q: Search query string
        figures: '1' to filter for papers with figures
        page: Page number (1-indexed)

    Returns:
        Rendered index.html template with papers and pagination
    """
    query_args = _get_paper_query_args()
    papers, total = query_papers(**query_args)

    # Convert datetime objects to ISO strings for template rendering
    for paper in papers:
        if paper.get('date') and hasattr(paper['date'], 'isoformat'):
            paper['date'] = paper['date'].isoformat()

    # Build category data with counts for the filter sidebar
    db = get_db()
    categories = build_categories(db)

    # Get available filters for the Advanced Search modal
    available_filters = get_available_filters(db)

    # Calculate pagination details
    per_page = query_args['per_page']
    total_pages = (total + per_page - 1) // per_page

    # For repopulating the search form, use raw query params to preserve user input
    form_filters = {
        'category': query_args['category'],
        'date_from': request.args.get('from', ''),
        'date_to': request.args.get('to', ''),
        'search': query_args['search'],
        'has_figures': query_args['has_figures'],
        'subjects': query_args.get('subjects', [])
    }

    return render_template('index.html',
                          items=papers,
                          total=total,
                          page=query_args['page'],
                          total_pages=total_pages,
                          categories=categories,
                          available_filters=available_filters,
                          filters=form_filters)


@bp.route('/items/<paper_id>', strict_slashes=False)
def paper_detail(paper_id):
    """
    Paper detail page.

    Args:
        paper_id: Paper identifier (e.g., 'chinaxiv-202201.00001')

    Returns:
        Rendered item.html template with paper details

    Raises:
        404: If paper not found
    """
    db = get_db()

    # PostgreSQL query with RealDictCursor
    cursor = db.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM papers WHERE id = %s", (paper_id,))
    paper = cursor.fetchone()

    if not paper:
        abort(404)

    # Convert datetime to ISO string for template rendering
    paper = dict(paper)
    if paper.get('date') and hasattr(paper['date'], 'isoformat'):
        paper['date'] = paper['date'].isoformat()

    return render_template('item.html', item=paper)


@bp.route('/sponsors')
@bp.route('/sponsors.html')
def sponsors():
    """
    Support Us / Sponsors page.

    Returns:
        Rendered sponsors.html template with translation statistics
    """
    # Get translation statistics from database
    db = get_db()
    cursor = db.cursor()

    # Count papers (total and validated)
    cursor.execute("SELECT COUNT(*) FROM papers WHERE qa_status = 'pass'")
    text_translated = cursor.fetchone()[0]

    # For now, use placeholder values for figures and costs
    # TODO: Calculate these from actual data when figure translation is implemented
    figures_translated = 0
    total_papers = 50000  # Approximate total papers in ChinaXiv
    remaining = total_papers - text_translated

    # Cost estimates (placeholder values)
    cost_text = 250.00  # Example cost for text translation
    cost_figures = 0.00  # No figures yet
    cost_per_paper = 0.15 if text_translated > 0 else 0
    total_cost = remaining * cost_per_paper if remaining > 0 else 0

    return render_template('sponsors.html',
                          text_translated=text_translated,
                          figures_translated=figures_translated,
                          remaining=remaining,
                          total_papers=total_papers,
                          cost_text=cost_text,
                          cost_figures=cost_figures,
                          cost_per_paper=cost_per_paper,
                          total_cost=total_cost)


@bp.route('/api/papers')
def api_papers():
    """
    JSON API for filtered papers.

    Query Parameters: Same as index() route.

    Returns:
        JSON response with structure:
        {
            "papers": [...],
            "total": 42,
            "page": 1,
            "per_page": 50
        }
    """
    query_args = _get_paper_query_args()
    papers, total = query_papers(**query_args)

    # Convert datetime objects to ISO strings for JSON serialization
    for paper in papers:
        if paper.get('date') and hasattr(paper['date'], 'isoformat'):
            paper['date'] = paper['date'].isoformat()

    return jsonify({
        'papers': papers,
        'total': total,
        'page': query_args['page'],
        'per_page': query_args['per_page']
    })
