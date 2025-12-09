"""
Flask application factory for ChinaRxiv English web server.

This module creates and configures the Flask application with:
- Database connection management (with automatic cleanup)
- Template and static file paths
- Blueprint registration for routes
"""

from flask import Flask, g
import os


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

    # Default configuration
    app.config['DATABASE'] = os.path.join('data', 'papers.db')
    app.config['PER_PAGE'] = 50  # Papers per page for pagination

    # Override with custom config if provided
    if config:
        app.config.update(config)

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
