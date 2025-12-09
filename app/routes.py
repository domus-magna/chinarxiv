"""
Flask routes for ChinaRxiv English web server.

This module contains all route handlers for the application:
- Homepage with filtering and pagination
- Paper detail pages
- JSON API for AJAX (optional)
"""

from flask import Blueprint, render_template, request, jsonify, abort, current_app
from datetime import datetime
import logging
from .database import query_papers, get_db
from .filters import build_categories
from .db_adapter import get_adapter

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

    Example:
        >>> parse_date('2022-01-15')
        '2022-01-15T00:00:00'
        >>> parse_date('2022-01')
        '2022-01-01T00:00:00'
        >>> parse_date('2022')
        '2022-01-01T00:00:00'
        >>> parse_date('invalid')
        None
    """
    if not date_str:
        return default

    # Strip whitespace
    date_str = date_str.strip()
    if not date_str:
        return default

    # Try full date format (YYYY-MM-DD)
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.isoformat()
    except ValueError:
        pass

    # Try year-month format (YYYY-MM)
    try:
        dt = datetime.strptime(date_str, '%Y-%m')
        return dt.isoformat()
    except ValueError:
        pass

    # Try year only format (YYYY)
    try:
        dt = datetime.strptime(date_str, '%Y')
        return dt.isoformat()
    except ValueError:
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

    This fixes the critical bug where to=2022 would exclude all papers in 2022.

    Args:
        date_str: Date string to parse (or empty string/None)
        default: Default value to return if parsing fails

    Returns:
        str: ISO timestamp at end of period or default value

    Example:
        >>> parse_date_end('2022-01-15')
        '2022-01-15T23:59:59'
        >>> parse_date_end('2022-01')
        '2022-01-31T23:59:59'
        >>> parse_date_end('2022-02')  # Non-leap year
        '2022-02-28T23:59:59'
        >>> parse_date_end('2024-02')  # Leap year
        '2024-02-29T23:59:59'
        >>> parse_date_end('2022')
        '2022-12-31T23:59:59'
    """
    if not date_str:
        return default

    # Strip whitespace
    date_str = date_str.strip()
    if not date_str:
        return default

    # Try full date format (YYYY-MM-DD) - return end of day
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.replace(hour=23, minute=59, second=59).isoformat()
    except ValueError:
        pass

    # Try year-month format (YYYY-MM) - return end of month
    try:
        dt = datetime.strptime(date_str, '%Y-%m')
        # Find last day of month
        if dt.month == 12:
            last_day = 31
        else:
            # Get first day of next month, subtract one day
            from calendar import monthrange
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
    # Parse filter params from query string
    category = request.args.get('category', '')
    date_from = parse_date(request.args.get('from', ''))
    date_to = parse_date_end(request.args.get('to', ''))  # Use parse_date_end for inclusive ranges
    search = request.args.get('q', '')
    has_figures = request.args.get('figures') == '1'

    try:
        page = int(request.args.get('page', 1))
    except (ValueError, TypeError):
        page = 1

    # Query database with filters
    per_page = current_app.config['PER_PAGE']
    papers, total = query_papers(
        category=category,
        date_from=date_from,
        date_to=date_to,
        search=search,
        has_figures=has_figures,
        page=page,
        per_page=per_page
    )

    # Build category data with counts
    db = get_db()
    categories = build_categories(db)

    # Calculate pagination
    total_pages = (total + per_page - 1) // per_page

    return render_template('index.html',
                          items=papers,
                          total=total,
                          page=page,
                          total_pages=total_pages,
                          categories=categories,
                          filters={
                              'category': category,
                              'date_from': date_from,
                              'date_to': date_to,
                              'search': search,
                              'has_figures': has_figures
                          })


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
    adapter = get_adapter()

    # Use adapter for database-agnostic query
    query = adapter.adapt_placeholder("SELECT * FROM papers WHERE id = ?")
    cursor = adapter.get_cursor(db)
    cursor.execute(query, (paper_id,))
    paper = cursor.fetchone()

    if not paper:
        abort(404)

    return render_template('item.html', item=dict(paper))


@bp.route('/api/papers')
def api_papers():
    """
    JSON API for filtered papers (for AJAX if needed).

    Query Parameters:
        Same as index() route

    Returns:
        JSON response with structure:
        {
            "papers": [...],
            "total": 42,
            "page": 1,
            "per_page": 50
        }
    """
    # Parse filter params (same as index())
    category = request.args.get('category', '')
    date_from = parse_date(request.args.get('from', ''))
    date_to = parse_date_end(request.args.get('to', ''))  # Use parse_date_end for inclusive ranges
    search = request.args.get('q', '')
    has_figures = request.args.get('figures') == '1'

    try:
        page = int(request.args.get('page', 1))
    except (ValueError, TypeError):
        page = 1

    # Query database
    per_page = current_app.config['PER_PAGE']
    papers, total = query_papers(
        category=category,
        date_from=date_from,
        date_to=date_to,
        search=search,
        has_figures=has_figures,
        page=page,
        per_page=per_page
    )

    return jsonify({
        'papers': papers,
        'total': total,
        'page': page,
        'per_page': per_page
    })
