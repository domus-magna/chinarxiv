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
from .filters import build_categories

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
            - category, date_from, date_to, search, has_figures
            - page, per_page
    """
    category = request.args.get('category', '')
    date_from = parse_date(request.args.get('from', ''))
    date_to = parse_date_end(request.args.get('to', ''))
    search = request.args.get('q', '')
    has_figures = request.args.get('figures') == '1'

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

    # Build category data with counts for the filter sidebar
    db = get_db()
    categories = build_categories(db)

    # Calculate pagination details
    per_page = query_args['per_page']
    total_pages = (total + per_page - 1) // per_page

    # For repopulating the search form, use raw query params to preserve user input
    form_filters = {
        'category': query_args['category'],
        'date_from': request.args.get('from', ''),
        'date_to': request.args.get('to', ''),
        'search': query_args['search'],
        'has_figures': query_args['has_figures']
    }

    return render_template('index.html',
                          items=papers,
                          total=total,
                          page=query_args['page'],
                          total_pages=total_pages,
                          categories=categories,
                          filters=form_filters)


@bp.route('/items/<paper_id>')
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

    return render_template('item.html', item=dict(paper))


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

    return jsonify({
        'papers': papers,
        'total': total,
        'page': query_args['page'],
        'per_page': query_args['per_page']
    })
