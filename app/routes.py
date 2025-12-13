"""
Flask routes for ChinaRxiv English web server.

This module contains all route handlers for the application:
- Homepage with filtering and pagination
- Paper detail pages
- JSON API for AJAX (optional)
"""

from flask import Blueprint, render_template, request, jsonify, abort, current_app, send_from_directory
from datetime import datetime, timedelta, timezone
from calendar import monthrange
import hashlib
import json
import logging
import re
import uuid
import psycopg2
from .database import query_papers, get_db
from .db_adapter import get_adapter
from .filters import build_categories, get_available_filters

logger = logging.getLogger(__name__)

# Create blueprint for main routes
bp = Blueprint('main', __name__)

# ---------------------------------------------------------------------------
# User report API constraints
# ---------------------------------------------------------------------------
VALID_REPORT_TYPES = {"translation", "figure", "site-bug", "feature", "other"}
MAX_REPORT_DESCRIPTION_LENGTH = 5000
MAX_REPORT_CONTEXT_SIZE = 10_000  # bytes


@bp.route('/health')
def health():
    """
    Health check endpoint for Railway and load balancers.

    Returns 200 OK without requiring database access.
    """
    return jsonify({'status': 'ok'})


@bp.route('/favicon.ico')
def favicon():
    """
    Serve favicon without 404 noise.

    Browsers default to requesting /favicon.ico. Our static assets live under
    /assets, so we proxy the request here.
    """
    return send_from_directory(current_app.static_folder, 'favicon.ico')


def _inject_figures_into_body_md(body_md: str, figures: list[dict]) -> str:
    """
    Replace [FIGURE:N] markers in body markdown with inline images.

    This is a lightweight, server-side equivalent of the PDF injector.
    It keeps the DB unchanged and only affects rendering.
    """
    if not body_md or not figures:
        return body_md

    # Strip table markers (tables aren't translated yet)
    body_md = re.sub(r"\[TABLE:\d+[A-Za-z]?\]", "", body_md)

    figure_urls: dict[str, list[str]] = {}
    for fig in figures:
        num = str(fig.get("number", "")).strip()
        url = fig.get("translated_url") or fig.get("url")
        if not num or not isinstance(url, str):
            continue
        url = url.strip()
        # Safety: only allow http(s) figure URLs to avoid XSS via crafted schemes.
        if not url.startswith(("http://", "https://")):
            continue
        figure_urls.setdefault(num, []).append(url)

    def replace_marker(match: re.Match) -> str:
        num = match.group(1)
        urls = figure_urls.get(num)
        if not urls:
            return match.group(0)
        imgs = "\n\n".join(f"![Figure {num}]({u})" for u in urls)
        return f"\n\n{imgs}\n\n"

    return re.sub(r"\[FIGURE:(\d+[A-Za-z]?)\]", replace_marker, body_md)


def _prepare_paper_for_template(paper):
    """
    Add underscore-prefixed fields expected by templates.

    The templates (index.html, item.html) use underscore-prefixed field names
    that were originally computed during static site generation. This function
    maps the DB fields to the expected template field names.

    Args:
        paper: Dict of paper data from database

    Returns:
        paper: Same dict with additional _has_* fields added
    """
    # Map DB fields to template fields
    paper['_has_full_text'] = bool(paper.get('has_full_text', False))
    paper['_has_english_pdf'] = bool(paper.get('english_pdf_url'))
    paper['_english_pdf_url'] = paper.get('english_pdf_url', '')

    # Parse figure_urls JSON column into _translated_figures list.
    # Expected format: [{"number": N, "url": "..."}, ...]
    figure_urls = paper.get('figure_urls')
    if figure_urls:
        try:
            paper['_translated_figures'] = json.loads(figure_urls)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Invalid JSON in figure_urls for paper {paper.get('id')}")
            paper['_translated_figures'] = []
    else:
        paper['_translated_figures'] = []

    # Derive _has_translated_figures from actual figure_urls content (not has_figures flag)
    # This ensures gallery displays when figure_urls is populated, regardless of has_figures
    paper['_has_translated_figures'] = bool(paper['_translated_figures'])

    # Map body_md to formatted_body_md for template, injecting inline figures
    raw_body_md = paper.get('body_md', '') or ''
    paper['formatted_body_md'] = _inject_figures_into_body_md(
        raw_body_md, paper['_translated_figures']
    )

    # Ensure license is dict-like if present
    lic = paper.get("license")
    if isinstance(lic, str):
        try:
            paper["license"] = json.loads(lic)
        except json.JSONDecodeError:
            paper["license"] = None

    # Ensure creators fields are lists (JSONB should already parse, but be safe)
    # This prevents join() from joining characters if field is a string

    for field in ('creators_en', 'creators'):
        val = paper.get(field)
        if isinstance(val, str):
            try:
                paper[field] = json.loads(val)
            except json.JSONDecodeError:
                paper[field] = [val]  # Treat string as single author
        elif val is None:
            paper[field] = []

    return paper


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

    # Allow API callers to override per_page with limit parameter (capped at 1000)
    default_per_page = current_app.config['PER_PAGE']
    try:
        limit = request.args.get('limit')
        per_page = min(int(limit), 1000) if limit else default_per_page
    except (ValueError, TypeError):
        per_page = default_per_page

    return {
        'category': category,
        'date_from': date_from,
        'date_to': date_to,
        'search': search,
        'has_figures': has_figures,
        'subjects': subjects,  # Always a list (empty or populated)
        'page': page,
        'per_page': per_page
    }


@bp.route('/')
def index():
    """
    Homepage with filtering and pagination.

    Query Parameters:
        category: Category ID (e.g., 'ai_cs')
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

    # Convert datetime objects to ISO strings and add template fields
    for paper in papers:
        if paper.get('date') and hasattr(paper['date'], 'isoformat'):
            paper['date'] = paper['date'].isoformat()
        _prepare_paper_for_template(paper)

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
    adapter = get_adapter()

    # PostgreSQL query with RealDictCursor (via adapter pattern)
    # Only show papers that have passed QA (i.e., have translations)
    cursor = adapter.get_cursor(db)
    cursor.execute(
        "SELECT * FROM papers WHERE id = %s AND qa_status = 'pass'",
        (paper_id,)
    )
    paper = cursor.fetchone()

    if not paper:
        abort(404)

    # Convert datetime to ISO string and add template fields
    paper = dict(paper)
    if paper.get('date') and hasattr(paper['date'], 'isoformat'):
        paper['date'] = paper['date'].isoformat()
    _prepare_paper_for_template(paper)

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
    adapter = get_adapter()
    cursor = adapter.get_cursor(db)

    # Count papers (total and validated)
    cursor.execute("SELECT COUNT(*) FROM papers WHERE qa_status = 'pass'")
    result = cursor.fetchone()
    text_translated = result['count'] if 'count' in result else result[list(result.keys())[0]]

    # For now, use placeholder values for figures and costs
    # TODO: Calculate these from actual data when figure translation is implemented
    figures_translated = 0
    total_papers = 50000  # Approximate total papers in ChinaXiv
    remaining = total_papers - text_translated

    # Per-paper costs (based on historical data)
    COST_TEXT_PER_PAPER = 0.08  # Kimi K2
    COST_FIGURES_PER_PAPER = 0.43  # Nano Banana Pro
    COST_PER_PAPER = COST_TEXT_PER_PAPER + COST_FIGURES_PER_PAPER  # ~$0.51

    # Calculate totals spent so far
    cost_text = text_translated * COST_TEXT_PER_PAPER
    cost_figures = figures_translated * COST_FIGURES_PER_PAPER
    cost_per_paper = COST_PER_PAPER
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


# Paper ID validation pattern: chinaxiv-YYYYMM.NNNNN
PAPER_ID_PATTERN = re.compile(r'^chinaxiv-\d{6}\.\d{5}$')

# Duplicate detection window (same IP + paper within this window = duplicate)
DUPLICATE_WINDOW_SECONDS = 60


def _get_client_ip():
    """
    Get client IP address from request headers.

    Railway/proxies set X-Forwarded-For header with the real client IP.
    Falls back to remote_addr if header not present.

    If no IP is available, generates a unique per-request token to prevent
    all unknown-IP requests from sharing the same dedup bucket (which could
    cause unrelated requests to DoS each other).

    Returns:
        str: Client IP address or unique per-request token
    """
    # X-Forwarded-For may contain multiple IPs: "client, proxy1, proxy2"
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        # Take the first (client) IP
        return forwarded.split(',')[0].strip()
    if request.remote_addr:
        return request.remote_addr
    # Generate unique token so unknown-IP requests don't share dedup bucket
    return f"unknown-{uuid.uuid4().hex[:12]}"


def _hash_ip(ip):
    """
    Hash IP address for privacy-preserving storage.

    Args:
        ip: IP address string

    Returns:
        str: First 16 characters of SHA-256 hash
    """
    full_hash = hashlib.sha256(ip.encode()).hexdigest()
    return full_hash[:16]


def _handle_translation_request(request_type):
    """
    Common handler for figure and text translation requests.

    Args:
        request_type: 'figure' or 'text'

    Returns:
        Flask JSON response with appropriate status code
    """
    # Parse JSON body
    try:
        data = request.get_json()
        # P1 fix: Check for dict type - arrays/strings/bools pass truthiness check
        # but don't have .get() method, causing AttributeError → 500
        if not data or not isinstance(data, dict):
            return jsonify({
                'success': False,
                'message': 'Invalid JSON in request body'
            }), 400
    except Exception:
        return jsonify({
            'success': False,
            'message': 'Invalid JSON in request body'
        }), 400

    paper_id = data.get('paper_id')

    # Validate paper_id exists
    if not paper_id or not isinstance(paper_id, str):
        return jsonify({
            'success': False,
            'message': 'Invalid paper_id'
        }), 400

    # Validate paper_id format
    if not PAPER_ID_PATTERN.match(paper_id):
        return jsonify({
            'success': False,
            'message': 'Invalid paper_id format'
        }), 400

    # Get and hash client IP
    client_ip = _get_client_ip()
    ip_hash = _hash_ip(client_ip)

    # Database operations with proper cleanup
    db = get_db()
    adapter = get_adapter()
    cursor = adapter.get_cursor(db)

    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(seconds=DUPLICATE_WINDOW_SECONDS)

        # P1 fix: Use INSERT ... ON CONFLICT to handle race condition atomically
        # Two concurrent requests could both pass a SELECT check and insert duplicates.
        # Instead, attempt insert and check if it was a duplicate via RETURNING.
        cursor.execute("""
            INSERT INTO translation_requests (paper_id, request_type, ip_hash)
            SELECT %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM translation_requests
                WHERE paper_id = %s
                  AND request_type = %s
                  AND ip_hash = %s
                  AND created_at > %s
            )
            RETURNING id
        """, (paper_id, request_type, ip_hash, paper_id, request_type, ip_hash, cutoff_time))

        result = cursor.fetchone()
        db.commit()

        if result is None:
            # Insert was blocked by the WHERE NOT EXISTS - duplicate detected
            return jsonify({
                'success': False,
                'message': 'Duplicate request detected. Please wait before requesting again.'
            }), 409

        return jsonify({
            'success': True,
            'message': 'Request logged successfully'
        }), 200

    except psycopg2.errors.UndefinedTable:
        # Schema not migrated yet (translation_requests missing)
        db.rollback()
        return jsonify({
            'success': False,
            'message': 'Requests are temporarily unavailable. Please try again later.'
        }), 503
    except psycopg2.Error as e:
        logger.error(f"Database error processing translation request: {e}")
        db.rollback()
        return jsonify({
            'success': False,
            'message': 'Database unavailable. Please try again later.'
        }), 503
    except Exception as e:
        logger.error(f"Failed to process translation request: {e}")
        db.rollback()
        return jsonify({
            'success': False,
            'message': 'Internal server error'
        }), 500
    finally:
        # P1 fix: Always close cursor to prevent connection leaks
        cursor.close()


@bp.route('/api/request-figure-translation', methods=['POST'])
def request_figure_translation():
    """
    API endpoint for figure translation requests.

    Body: { "paper_id": "chinaxiv-202510.00001" }

    Returns:
        200: Request logged successfully
        400: Invalid paper_id or JSON
        409: Duplicate request within 60 seconds
        500: Internal server error
    """
    return _handle_translation_request('figure')


@bp.route('/api/request-text-translation', methods=['POST'])
def request_text_translation():
    """
    API endpoint for text translation requests.

    Body: { "paper_id": "chinaxiv-202510.00001" }

    Returns:
        200: Request logged successfully
        400: Invalid paper_id or JSON
        409: Duplicate request within 60 seconds
        500: Internal server error
    """
    return _handle_translation_request('text')


@bp.route('/api/report', methods=['POST'])
def report_problem():
    """
    API endpoint for users to report translation/site issues.

    Body:
        {
            "type": "translation" | "figure" | "site-bug" | "feature" | "other",
            "description": "...",
            "context": { "paperId": "...", "url": "...", ... }
        }
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"success": False, "message": "Invalid JSON"}), 400

    issue_type = data.get("type")
    description = data.get("description")
    context = data.get("context") or {}

    if not issue_type or not isinstance(issue_type, str):
        return jsonify({"success": False, "message": "Invalid type"}), 400
    issue_type_norm = issue_type.strip().lower()
    if issue_type_norm not in VALID_REPORT_TYPES:
        return jsonify({"success": False, "message": "Invalid type"}), 400

    if not description or not isinstance(description, str):
        return jsonify({"success": False, "message": "Invalid description"}), 400
    description_stripped = description.strip()
    if len(description_stripped) < 10:
        return jsonify({"success": False, "message": "Invalid description"}), 400
    if len(description_stripped) > MAX_REPORT_DESCRIPTION_LENGTH:
        return jsonify({"success": False, "message": "Description too long"}), 400

    paper_id = None
    context_json = None
    if isinstance(context, dict):
        paper_id = context.get("paperId") or context.get("paper_id")
        try:
            context_json = json.dumps(context, ensure_ascii=False)
        except (TypeError, ValueError):
            context_json = None
        if context_json is not None and len(context_json.encode("utf-8")) > MAX_REPORT_CONTEXT_SIZE:
            return jsonify({"success": False, "message": "Context too large"}), 400

    # Privacy-preserving IP hash (optional)
    client_ip = _get_client_ip()
    ip_hash = _hash_ip(client_ip)

    db = get_db()
    adapter = get_adapter()
    cursor = adapter.get_cursor(db)
    try:
        cursor.execute(
            """
            INSERT INTO user_reports (paper_id, issue_type, description, context, ip_hash)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                paper_id,
                issue_type_norm[:32],
                description_stripped,
                context_json,
                ip_hash,
            ),
        )
        db.commit()
        return jsonify({"success": True, "message": "Report received"}), 200
    except psycopg2.errors.UndefinedTable:
        db.rollback()
        return jsonify({
            "success": False,
            "message": "Reporting is temporarily unavailable. Please try again later."
        }), 503
    except psycopg2.Error as e:
        logger.error(f"Database error logging user report: {e}")
        db.rollback()
        return jsonify({
            "success": False,
            "message": "Database unavailable. Please try again later."
        }), 503
    except Exception as e:
        logger.error(f"Failed to log user report: {e}")
        db.rollback()
        return jsonify({"success": False, "message": "Internal server error"}), 500
    finally:
        cursor.close()
