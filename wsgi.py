"""
WSGI entry point for production deployment.

This module creates the Flask application instance for WSGI servers
like gunicorn, uWSGI, or mod_wsgi.

Usage with gunicorn:
    gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app
"""

from app import create_app

# Create Flask application instance
app = create_app()

if __name__ == '__main__':
    # For local development only (use flask run instead)
    app.run(debug=True)
