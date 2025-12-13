"""
Flask application factory for ChinaRxiv English web server.

This module creates and configures the Flask application with:
- PostgreSQL database connection management (with automatic cleanup)
- Template and static file paths
- Blueprint registration for routes

Requires: DATABASE_URL environment variable for PostgreSQL connection
"""

from flask import Flask, g, render_template, request
import os
import markdown as md
from markupsafe import Markup
import bleach
import logging
from app.db_adapter import init_adapter, get_adapter
from werkzeug.exceptions import HTTPException
from whitenoise import WhiteNoise

try:
    import psycopg2
    from psycopg2 import Error as PsycopgError
except ImportError:  # pragma: no cover - psycopg2 is required in prod
    psycopg2 = None
    PsycopgError = None


def create_app(config=None):
    """
    Create and configure the Flask application.

    Args:
        config: Optional configuration dictionary to override defaults

    Returns:
        Flask: Configured Flask application instance
    """
    app = Flask(__name__,
                template_folder='../src/templates',
                static_folder='../assets',
                static_url_path='/assets')

    # Default configuration
    app.config['PER_PAGE'] = 50  # Papers per page for pagination

    # Umami analytics configuration (optional, disabled if not set)
    app.config['UMAMI_WEBSITE_ID'] = os.environ.get('UMAMI_WEBSITE_ID')
    app.config['UMAMI_SCRIPT_URL'] = os.environ.get('UMAMI_SCRIPT_URL')

    # Override with custom config if provided
    if config:
        app.config.update(config)

    # Configure lazy database initialization
    # Connection pool is created on first database query, not at startup
    # This allows the app to start even if the database is temporarily unavailable
    init_adapter()
    app.logger.info("Database configured for lazy initialization")

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Register Jinja2 filters
    @app.template_filter('markdown')
    def markdown_filter(text):
        """
        Convert markdown text to HTML with XSS protection.

        Sanitizes HTML using bleach allowlist to prevent stored XSS attacks.
        Only allows safe HTML tags (p, br, em, strong, ul, ol, li, code, pre, a, blockquote).
        """
        if not text:
            return ""

        # Convert markdown to HTML
        html = md.markdown(text, extensions=['extra', 'nl2br'])

        # Sanitize HTML with allowlist (XSS protection)
        clean_html = bleach.clean(
            html,
            tags=[
                'p', 'br', 'em', 'strong', 'ul', 'ol', 'li', 'code', 'pre',
                'a', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                # Inline figures injected from trusted B2 URLs
                'img', 'figure', 'figcaption',
            ],
            attributes={
                'a': ['href', 'title'],
                'img': ['src', 'alt', 'title', 'loading', 'width', 'height'],
                'figure': ['class'],
            },
            strip=True  # Strip disallowed tags instead of escaping
        )

        return Markup(clean_html)

    # Connection teardown hook (Codex P2 fix: prevent connection leaks)
    @app.teardown_appcontext
    def close_db(error):
        """
        Release database connection at end of request.

        Returns connection to PostgreSQL connection pool, ensuring proper
        cleanup after each request and preventing resource leaks.
        """
        db = g.pop('db', None)
        if db is not None:
            get_adapter().release_connection(db)

    # ------------------------------------------------------------------
    # Gentle failure pages
    # ------------------------------------------------------------------

    def _render_gentle_failure(
        message: str,
        *,
        status_code: int = 503,
        title: str = "Temporarily unavailable",
    ):
        return (
            render_template(
                "gentle_failure.html",
                title=title,
                message=message,
            ),
            status_code,
        )

    if PsycopgError is not None:
        @app.errorhandler(PsycopgError)
        def handle_psycopg_error(err):
            app.logger.error("Database error in request", exc_info=err)
            return _render_gentle_failure(
                "We’re having trouble connecting to the database right now. "
                "Please try again in a few minutes."
            )

    @app.errorhandler(RuntimeError)
    def handle_runtime_error(err):
        # get_adapter() raises RuntimeError("Database unavailable: ...") on pool init failures.
        msg = str(err)
        if msg.lower().startswith("database unavailable"):
            app.logger.error("Database unavailable", exc_info=err)
            return _render_gentle_failure(
                "We’re having trouble connecting to the database right now. "
                "Please try again in a few minutes."
            )
        # Other RuntimeErrors: show a gentle 500.
        app.logger.error("Runtime error in request", exc_info=err)
        return _render_gentle_failure(
            "Something went wrong while loading this page. "
            "Please try again later.",
            status_code=500,
            title="Something went wrong",
        )

    @app.errorhandler(500)
    def handle_500(err):
        # Preserve Werkzeug HTTPExceptions (they already have good semantics).
        if isinstance(err, HTTPException):
            return err
        app.logger.error("Unhandled server error", exc_info=err)
        return _render_gentle_failure(
            "Something went wrong while loading this page. "
            "Please try again later.",
            status_code=500,
            title="Something went wrong",
        )

    # Register blueprints (routes will be added in Phase 2.4)
    from . import routes
    app.register_blueprint(routes.bp)

    # ---------------------------------------------------------------------
    # HTTP Cache Headers (Performance optimization)
    # ---------------------------------------------------------------------
    @app.after_request
    def add_cache_headers(response):
        """
        Add Cache-Control headers for performance.

        - Paper detail pages (/items/): 24 hours (rarely change)
        - API responses: 1 minute
        - HTML pages: 5 minutes (content may change)

        Note: Static assets (/assets/) are handled by WhiteNoise middleware
        with its own caching headers, so we skip them here.
        """
        # Skip if response already has Cache-Control (set by WhiteNoise or other)
        if 'Cache-Control' in response.headers:
            return response

        path = request.path

        # Skip /assets/ - handled by WhiteNoise middleware
        if path.startswith('/assets/'):
            return response

        # Paper detail pages - cache 24 hours
        if path.startswith('/items/'):
            response.headers['Cache-Control'] = 'public, max-age=86400'
        # API endpoints - cache 1 minute
        elif path.startswith('/api/'):
            response.headers['Cache-Control'] = 'public, max-age=60'
        # Homepage and other pages - cache 5 minutes
        elif response.content_type and 'text/html' in response.content_type:
            response.headers['Cache-Control'] = 'public, max-age=300'

        return response

    # Wrap app with WhiteNoise for compressed static file serving
    # This must happen AFTER all routes are registered
    app.wsgi_app = WhiteNoise(
        app.wsgi_app,
        root=os.path.join(os.path.dirname(__file__), '..', 'assets'),
        prefix='/assets/',
        # Enable compression (gzip/brotli)
        # Add immutable for versioned assets, max_age for non-versioned
        max_age=86400,  # 1 day for non-versioned static files
    )

    return app
