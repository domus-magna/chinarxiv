"""
Flask routes for ChinaRxiv English web server.

This module contains all route handlers for the application:
- Homepage with filtering and pagination
- Paper detail pages
- JSON API for AJAX (optional)
"""

from flask import Blueprint, render_template, request, jsonify, abort, current_app
from .database import query_papers, get_db
from .filters import build_categories

# Create blueprint for main routes
bp = Blueprint('main', __name__)


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
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
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

    paper = db.execute(
        "SELECT * FROM papers WHERE id = ?",
        (paper_id,)
    ).fetchone()

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
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
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
