#!/usr/bin/env python3
"""
Monitoring dashboard for ChinaXiv Translations.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

import requests
from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import check_password_hash

from .monitoring import monitoring_service
from .logging_utils import log

# Monitoring defaults
DEFAULT_ANALYTICS_DAYS = 7
DEFAULT_ALERT_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 100
AUTH_SESSION_TIMEOUT_SECONDS = 3600
HEALTHCHECK_TIMEOUT_SECONDS = 5
# WARNING: Paper translation times vary widely (30s to 40min).
# This is a rough estimate used only for UI progress display.
ESTIMATED_JOB_DURATION_SECONDS = 30

# Configuration
MONITORING_USERNAME = os.getenv("MONITORING_USERNAME")
MONITORING_PASSWORD = os.getenv("MONITORING_PASSWORD")
MONITORING_PASSWORD_HASH = os.getenv("MONITORING_PASSWORD_HASH")
MONITORING_PORT = int(os.getenv("MONITORING_PORT", "5000"))
# Generate random secret key if not provided (sessions won't persist across restarts)
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)


@dataclass
class JobStats:
    """Job statistics."""

    total: int
    completed: int
    pending: int
    failed: int
    progress_percent: float
    estimated_completion: Optional[str] = None


@dataclass
class SystemStats:
    """System statistics."""

    uptime: str
    last_update: str
    site_url: str
    github_actions_status: str
    cloudflare_status: str


class MonitoringDashboard:
    """Monitoring dashboard for ChinaXiv Translations."""

    def __init__(self):
        # Configure Flask with templates directory
        template_dir = Path(__file__).parent / "templates"
        self.app = Flask(__name__, template_folder=str(template_dir))
        self.app.secret_key = SECRET_KEY
        self.setup_routes()

    def setup_routes(self):
        """Setup Flask routes."""

        @self.app.route("/")
        def index():
            """Main dashboard page."""
            if not self.check_auth():
                return self.auth_required()
            return self.render_dashboard()

        @self.app.route("/api/stats")
        def api_stats():
            """API endpoint for statistics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            stats = self.get_job_stats()
            return jsonify(stats.__dict__)

        @self.app.route("/api/system")
        def api_system():
            """API endpoint for system statistics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            stats = self.get_system_stats()
            return jsonify(stats.__dict__)

        @self.app.route("/api/logs")
        def api_logs():
            """API endpoint for recent logs."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            logs = self.get_recent_logs()
            return jsonify(logs)

        @self.app.route("/login", methods=["GET", "POST"])
        def login():
            """Login page."""
            # Require credentials to be configured via environment
            if not MONITORING_USERNAME or not (
                MONITORING_PASSWORD or MONITORING_PASSWORD_HASH
            ):
                return self.render_login(
                    error="Monitoring credentials not configured. Set MONITORING_USERNAME and MONITORING_PASSWORD or MONITORING_PASSWORD_HASH."
                )

            if request.method == "POST":
                username = request.form.get("username")
                password = request.form.get("password")
                ok = False
                if MONITORING_PASSWORD_HASH:
                    try:
                        # Use constant-time comparison to prevent timing attacks
                        ok = secrets.compare_digest(
                            username, MONITORING_USERNAME
                        ) and check_password_hash(MONITORING_PASSWORD_HASH, password)
                    except Exception:
                        ok = False
                else:
                    # Use constant-time comparison to prevent timing attacks
                    ok = secrets.compare_digest(
                        username, MONITORING_USERNAME
                    ) and secrets.compare_digest(password, MONITORING_PASSWORD)

                if ok:
                    # Use Flask's signed session (cryptographically secure)
                    session.permanent = True
                    self.app.permanent_session_lifetime = timedelta(
                        seconds=AUTH_SESSION_TIMEOUT_SECONDS
                    )
                    session["authenticated"] = True
                    return self.render_login_success()
                else:
                    return self.render_login(error="Invalid credentials")

            return self.render_login()

        @self.app.route("/health")
        def health():
            """Health check endpoint."""
            return jsonify(
                {
                    "status": "healthy",
                    "timestamp": datetime.now().isoformat(),
                    "version": "1.0.0",
                }
            )

        @self.app.route("/alerts")
        def alerts():
            """Get recent alerts."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            limit = request.args.get("limit", DEFAULT_ALERT_LIMIT, type=int)
            level = request.args.get("level")

            alerts = monitoring_service.get_alerts(limit)
            if level:
                alerts = [a for a in alerts if a.get("level") == level]

            return jsonify(alerts)

        @self.app.route("/alerts/create", methods=["POST"])
        def create_alert():
            """Create a new alert."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            data = request.get_json()
            if not data:
                return jsonify({"error": "No JSON data provided"}), 400

            try:
                level = data.get("level", "info")
                title = data.get("title", "")
                message = data.get("message", "")
                source = data.get("source", "api")
                metadata = data.get("metadata", {})

                alert = monitoring_service.create_alert(
                    level, title, message, source=source, metadata=metadata
                )

                return jsonify({"success": True, "alert": alert})

            except Exception as e:
                return jsonify({"error": str(e)}), 400

        @self.app.route("/analytics")
        def analytics():
            """Get analytics data."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            days = request.args.get("days", DEFAULT_ANALYTICS_DAYS, type=int)
            stats = monitoring_service.get_analytics(days)
            return jsonify(stats)

        @self.app.route("/analytics/page_views")
        def analytics_page_views():
            """Get page view analytics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            days = request.args.get("days", DEFAULT_ANALYTICS_DAYS, type=int)
            page = request.args.get("page")
            analytics = monitoring_service.get_analytics(days)
            page_views = analytics.get("page_views", [])
            if page:
                page_views = [pv for pv in page_views if pv.get("page") == page]
            return jsonify(page_views)

        @self.app.route("/analytics/search_queries")
        def analytics_search_queries():
            """Get search query analytics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            days = request.args.get("days", DEFAULT_ANALYTICS_DAYS, type=int)
            limit = request.args.get("limit", DEFAULT_SEARCH_LIMIT, type=int)
            analytics = monitoring_service.get_analytics(days)
            queries = analytics.get("search_queries", [])
            queries = queries[-limit:] if queries else []
            return jsonify(queries)

        @self.app.route("/analytics/downloads")
        def analytics_downloads():
            """Get download analytics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            # Downloads are not tracked in the consolidated service yet
            return jsonify([])

        @self.app.route("/performance")
        def performance():
            """Get performance metrics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            days = request.args.get("days", DEFAULT_ANALYTICS_DAYS, type=int)
            stats = monitoring_service.get_performance(days)
            return jsonify(stats)

        @self.app.route("/performance/report")
        def performance_report():
            """Get comprehensive performance report."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            days = request.args.get("days", DEFAULT_ANALYTICS_DAYS, type=int)
            # Simplified performance report
            performance = monitoring_service.get_performance(days)
            report = {
                "period_days": days,
                "metrics": performance.get("metrics", []),
                "generated_at": datetime.now().isoformat(),
            }
            return jsonify(report)

        @self.app.route("/performance/optimize", methods=["POST"])
        def optimize_performance():
            """Run performance optimizations."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            results = monitoring_service.optimize_site()

            return jsonify(results)

    def check_auth(self) -> bool:
        """Check if user is authenticated via signed session."""
        return session.get("authenticated") is True

    def auth_required(self):
        """Return authentication required page."""
        return self.render_login()

    def get_job_stats(self) -> JobStats:
        """Get job statistics from database."""
        try:
            db_path = Path("data/job_queue.db")
            if not db_path.exists():
                print(f"Warning: Database not found at {db_path}")
                return JobStats(0, 0, 0, 0, 0.0)

            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()

                # Get total jobs
                cursor.execute("SELECT COUNT(*) FROM jobs")
                total = cursor.fetchone()[0]

                # Get completed jobs
                cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'completed'")
                completed = cursor.fetchone()[0]

                # Get pending jobs
                cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'pending'")
                pending = cursor.fetchone()[0]

                # Get failed jobs
                cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'")
                failed = cursor.fetchone()[0]

                # Calculate progress
                progress_percent = (completed / total * 100) if total > 0 else 0.0

                # Estimate completion time
                estimated_completion = None
                if pending > 0 and completed > 0:
                    # Simple estimation based on rough average
                    remaining_seconds = pending * ESTIMATED_JOB_DURATION_SECONDS
                    estimated_completion = datetime.now() + timedelta(
                        seconds=remaining_seconds
                    )
                    estimated_completion = estimated_completion.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

                return JobStats(
                    total=total,
                    completed=completed,
                    pending=pending,
                    failed=failed,
                    progress_percent=progress_percent,
                    estimated_completion=estimated_completion,
                )

        except (sqlite3.Error, OSError) as e:
            log(f"Error getting job stats: {e}")
            return JobStats(0, 0, 0, 0, 0.0)

    def get_system_stats(self) -> SystemStats:
        """Get system statistics."""
        try:
            # Get uptime (simplified)
            uptime = "Running"

            # Get last update
            last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Site URL - use production domain or configurable via env
            site_url = os.environ.get("SITE_URL", "https://chinarxiv.org")

            # Check GitHub Actions status
            github_status = "Active"

            # Check Cloudflare status
            try:
                response = requests.get(site_url, timeout=HEALTHCHECK_TIMEOUT_SECONDS)
                cloudflare_status = (
                    "Online" if response.status_code == 200 else "Offline"
                )
            except requests.RequestException:
                cloudflare_status = "Unknown"

            return SystemStats(
                uptime=uptime,
                last_update=last_update,
                site_url=site_url,
                github_actions_status=github_status,
                cloudflare_status=cloudflare_status,
            )

        except (requests.RequestException, OSError) as e:
            log(f"Error getting system stats: {e}")
            return SystemStats("Unknown", "Unknown", "Unknown", "Unknown", "Unknown")

    def get_recent_logs(self) -> List[Dict[str, Any]]:
        """Get recent logs."""
        try:
            logs = []

            # Check for log files
            log_files = [
                "data/batch_translate.log",
                "data/translation.log",
                "data/harvest.log",
            ]

            for log_file in log_files:
                if Path(log_file).exists():
                    with open(log_file, "r") as f:
                        lines = f.readlines()
                        # Get last 10 lines
                        for line in lines[-10:]:
                            if line.strip():
                                logs.append(
                                    {
                                        "timestamp": datetime.now().strftime(
                                            "%Y-%m-%d %H:%M:%S"
                                        ),
                                        "level": "INFO",
                                        "message": line.strip(),
                                        "source": Path(log_file).name,
                                    }
                                )

            # Sort by timestamp (newest first)
            logs.sort(key=lambda x: x["timestamp"], reverse=True)
            return logs[:20]  # Return last 20 logs

        except Exception as e:
            print(f"Error getting logs: {e}")
            return []

    def render_dashboard(self):
        """Render the main dashboard."""
        return render_template("dashboard.html")

    def render_login(self, error: str = None):
        """Render the login page."""
        return render_template("login.html", error=error)

    def render_login_success(self):
        """Render the login success page."""
        return render_template("login_success.html")

    def run(self, host="0.0.0.0", port=MONITORING_PORT, debug=False):
        """Run the monitoring dashboard."""
        print("🚀 Starting ChinaXiv Translations Monitor")
        print(f"📊 Dashboard: http://{host}:{port}")
        print(f"🔐 Username: {MONITORING_USERNAME}")
        print(f"🔑 Password: {MONITORING_PASSWORD}")
        print(f"🌐 Site: {os.environ.get('SITE_URL', 'https://chinarxiv.org')}")

        self.app.run(host=host, port=port, debug=debug)


def main():
    """Main function."""
    dashboard = MonitoringDashboard()
    dashboard.run()


if __name__ == "__main__":
    main()
