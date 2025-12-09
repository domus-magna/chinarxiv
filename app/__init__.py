"""
Flask application factory for ChinaRxiv English web server.

This module creates and configures the Flask application with:
- Database connection management (with automatic cleanup)
- Template and static file paths
- Blueprint registration for routes
"""

from flask import Flask, g
import os
from pathlib import Path
import markdown as md
from markupsafe import Markup
import bleach
import logging


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
                static_folder='../static')

    # Default configuration (use absolute path for database)
    app_root = Path(__file__).parent.parent
    app.config['DATABASE'] = str(app_root / 'data' / 'papers.db')
    app.config['PER_PAGE'] = 50  # Papers per page for pagination

    # Override with custom config if provided
    if config:
        app.config.update(config)

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
            tags=['p', 'br', 'em', 'strong', 'ul', 'ol', 'li', 'code', 'pre',
                  'a', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'],
            attributes={'a': ['href', 'title']},
            strip=True  # Strip disallowed tags instead of escaping
        )

        return Markup(clean_html)

    # Connection teardown hook (Codex P2 fix: prevent connection leaks)
    @app.teardown_appcontext
    def close_db(error):
        """
        Close database connection at end of request.

        This ensures connections are properly cleaned up after each request,
        preventing resource leaks in production.
        """
        db = g.pop('db', None)
        if db is not None:
            db.close()

    # Register blueprints (routes will be added in Phase 2.4)
    from . import routes
    app.register_blueprint(routes.bp)

    return app
