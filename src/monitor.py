#!/usr/bin/env python3
"""
Monitoring dashboard for ChinaXiv Translations.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

import requests
from flask import Flask, render_template_string, request, jsonify, Response
from werkzeug.security import check_password_hash

from .monitoring import monitoring_service

# Configuration
MONITORING_USERNAME = os.getenv("MONITORING_USERNAME")
MONITORING_PASSWORD = os.getenv("MONITORING_PASSWORD")
MONITORING_PASSWORD_HASH = os.getenv("MONITORING_PASSWORD_HASH")
MONITORING_PORT = int(os.getenv("MONITORING_PORT", "5000"))
SECRET_KEY = os.getenv(
    "SECRET_KEY", "chinaxiv-monitoring-secret-key-change-in-production"
)
GITHUB_REPO = os.getenv("MONITORING_GITHUB_REPO") or os.getenv("GH_REPO") or os.getenv(
    "GITHUB_REPOSITORY", "domus-magna/chinaxiv-english"
)
GITHUB_TOKEN = os.getenv("MONITORING_GITHUB_TOKEN") or os.getenv("GH_TOKEN")
MONITORED_WORKFLOWS = [
    w.strip()
    for w in (os.getenv("MONITORING_WORKFLOWS", "backfill.yml,build.yml").split(","))
    if w.strip()
]


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
        self.app = Flask(__name__)
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

        @self.app.route("/api/workflows")
        def api_workflows():
            """API endpoint for GitHub workflow runs."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            return jsonify(self.get_workflow_runs())

        @self.app.route("/login", methods=["GET", "POST"])
        def login():
            """Login page."""
            # Require credentials to be configured via environment
            if not MONITORING_USERNAME or not (
                MONITORING_PASSWORD or MONITORING_PASSWORD_HASH
            ):
                return render_template_string(
                    self.get_login_template(),
                    error="Monitoring credentials not configured. Set MONITORING_USERNAME and MONITORING_PASSWORD or MONITORING_PASSWORD_HASH.",
                )

            if request.method == "POST":
                username = request.form.get("username")
                password = request.form.get("password")
                ok = False
                if MONITORING_PASSWORD_HASH:
                    try:
                        ok = (username == MONITORING_USERNAME) and check_password_hash(
                            MONITORING_PASSWORD_HASH, password
                        )
                    except Exception:
                        ok = False
                else:
                    ok = (username == MONITORING_USERNAME) and (
                        password == MONITORING_PASSWORD
                    )

                if ok:
                    response = Response(
                        render_template_string(self.get_login_success_template())
                    )
                    response.set_cookie("auth_token", "authenticated", max_age=3600)
                    return response
                else:
                    return render_template_string(
                        self.get_login_template(), error="Invalid credentials"
                    )

            return render_template_string(self.get_login_template())

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

            limit = request.args.get("limit", 50, type=int)
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

            days = request.args.get("days", 7, type=int)
            stats = monitoring_service.get_analytics(days)
            return jsonify(stats)

        @self.app.route("/analytics/page_views")
        def analytics_page_views():
            """Get page view analytics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            days = request.args.get("days", 7, type=int)
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

            days = request.args.get("days", 7, type=int)
            limit = request.args.get("limit", 100, type=int)
            analytics = monitoring_service.get_analytics(days)
            queries = analytics.get("search_queries", [])
            queries = queries[-limit:] if queries else []
            return jsonify(queries)

        @self.app.route("/analytics/downloads")
        def analytics_downloads():
            """Get download analytics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            request.args.get("days", 7, type=int)
            request.args.get("paper_id")
            # Downloads are not tracked in the consolidated service yet
            return jsonify([])

        @self.app.route("/performance")
        def performance():
            """Get performance metrics."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            days = request.args.get("days", 7, type=int)
            stats = monitoring_service.get_performance(days)
            return jsonify(stats)

        @self.app.route("/performance/report")
        def performance_report():
            """Get comprehensive performance report."""
            if not self.check_auth():
                return jsonify({"error": "Unauthorized"}), 401

            days = request.args.get("days", 7, type=int)
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

            data = request.get_json() or {}
            data.get("type", "all")

            results = monitoring_service.optimize_site()

            return jsonify(results)

    def check_auth(self) -> bool:
        """Check if user is authenticated."""
        auth_token = request.cookies.get("auth_token")
        return auth_token == "authenticated"

    def auth_required(self):
        """Return authentication required page."""
        return render_template_string(self.get_login_template())

    def get_job_stats(self) -> JobStats:
        """Get job statistics from database."""
        try:
            db_path = Path("data/job_queue.db")
            if db_path.exists():
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.cursor()

                    cursor.execute("SELECT COUNT(*) FROM jobs")
                    total = cursor.fetchone()[0]

                    cursor.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status = 'completed'"
                    )
                    completed = cursor.fetchone()[0]

                    cursor.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status = 'pending'"
                    )
                    pending = cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'")
                    failed = cursor.fetchone()[0]

                    progress_percent = (completed / total * 100) if total > 0 else 0.0

                    estimated_completion = None
                    if pending > 0 and completed > 0:
                        avg_time_per_job = 30
                        remaining_seconds = pending * avg_time_per_job
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

            # Fallback to file-based queue stats
            jobs_dir = Path("data/jobs")
            if jobs_dir.exists():
                total = completed = pending = failed = in_progress = 0
                for job_file in jobs_dir.glob("*.json"):
                    try:
                        data = job_file.read_text(encoding="utf-8")
                        job = json.loads(data)
                    except Exception:
                        continue
                    total += 1
                    status = job.get("status", "pending")
                    if status == "completed":
                        completed += 1
                    elif status == "failed":
                        failed += 1
                    elif status == "in_progress":
                        in_progress += 1
                    else:
                        pending += 1
                progress_percent = (completed / total * 100) if total else 0.0
                estimated_completion = None
                if pending > 0 and completed > 0:
                    avg_time_per_job = 30
                    remaining_seconds = pending * avg_time_per_job
                    estimated_completion = datetime.now() + timedelta(
                        seconds=remaining_seconds
                    )
                    estimated_completion = estimated_completion.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                return JobStats(
                    total=total,
                    completed=completed,
                    pending=pending + in_progress,
                    failed=failed,
                    progress_percent=progress_percent,
                    estimated_completion=estimated_completion,
                )

        except Exception as e:
            print(f"Error getting job stats: {e}")
            import traceback

            traceback.print_exc()
            return JobStats(0, 0, 0, 0, 0.0)

    def get_system_stats(self) -> SystemStats:
        """Get system statistics."""
        try:
            # Get uptime (simplified)
            uptime = "Running"

            # Get last update
            last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Site URL
            site_url = "https://chinaxiv-english.pages.dev"

            # Check GitHub Actions status
            github_status = "Active"

            # Check Cloudflare status
            try:
                response = requests.get(site_url, timeout=5)
                cloudflare_status = (
                    "Online" if response.status_code == 200 else "Offline"
                )
            except Exception:
                cloudflare_status = "Unknown"

            return SystemStats(
                uptime=uptime,
                last_update=last_update,
                site_url=site_url,
                github_actions_status=github_status,
                cloudflare_status=cloudflare_status,
            )

        except Exception as e:
            print(f"Error getting system stats: {e}")
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

    def fetch_workflow_runs(self, workflow_file: str, limit: int = 5) -> List[Dict]:
        """Fetch recent GitHub workflow runs for a given workflow file."""
        if not GITHUB_REPO or not workflow_file or not GITHUB_TOKEN:
            return []

        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/runs"
        params = {"per_page": limit}
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code != 200:
                return []
            payload = resp.json() or {}
            runs = payload.get("workflow_runs") or []
            out: List[Dict[str, Any]] = []
            for run in runs:
                out.append(
                    {
                        "name": run.get("name"),
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "event": run.get("event"),
                        "run_number": run.get("run_number"),
                        "head_branch": run.get("head_branch"),
                        "created_at": run.get("created_at"),
                        "updated_at": run.get("updated_at"),
                        "html_url": run.get("html_url"),
                    }
                )
            return out
        except Exception:
            return []

    def get_workflow_runs(self) -> List[Dict[str, Any]]:
        """Return workflow run summaries for monitored workflows."""
        data: List[Dict[str, Any]] = []
        for wf in MONITORED_WORKFLOWS:
            runs = self.fetch_workflow_runs(wf)
            data.append({"workflow": wf, "runs": runs})
        return data

    def render_dashboard(self):
        """Render the main dashboard."""
        return render_template_string(self.get_dashboard_template())

    def get_dashboard_template(self) -> str:
        """Get dashboard HTML template."""
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ChinaXiv Translations Monitor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; }
        .header { background: #2c3e50; color: white; padding: 1rem; text-align: center; }
        .container { max-width: 1200px; margin: 0 auto; padding: 2rem; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 2rem; margin-bottom: 2rem; }
        .card { background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .card h3 { color: #2c3e50; margin-bottom: 1rem; }
        .stat { display: flex; justify-content: space-between; margin-bottom: 0.5rem; }
        .stat-value { font-weight: bold; }
        .progress-bar { width: 100%; height: 20px; background: #ecf0f1; border-radius: 10px; overflow: hidden; margin: 1rem 0; }
        .progress-fill { height: 100%; background: #27ae60; transition: width 0.3s ease; }
        .status { padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.8rem; }
        .status.online { background: #d5f4e6; color: #27ae60; }
        .status.offline { background: #fadbd8; color: #e74c3c; }
        .status.unknown { background: #f8f9fa; color: #6c757d; }
        .logs { max-height: 400px; overflow-y: auto; }
        .log-entry { padding: 0.5rem; border-bottom: 1px solid #ecf0f1; font-size: 0.9rem; }
        .log-timestamp { color: #7f8c8d; font-size: 0.8rem; }
        .refresh-btn { background: #3498db; color: white; border: none; padding: 0.5rem 1rem; border-radius: 4px; cursor: pointer; }
        .refresh-btn:hover { background: #2980b9; }
        .auto-refresh { margin-left: 1rem; }
        .workflow-run { border-bottom: 1px solid #ecf0f1; padding: 0.5rem 0; font-size: 0.9rem; }
        .workflow-run:last-child { border-bottom: none; }
        .workflow-name { font-weight: 600; margin-bottom: 0.5rem; }
        .workflow-meta { color: #7f8c8d; font-size: 0.8rem; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🚀 ChinaXiv Translations Monitor</h1>
        <p>Real-time monitoring dashboard</p>
    </div>
    
    <div class="container">
        <div style="text-align: center; margin-bottom: 2rem;">
            <button class="refresh-btn" onclick="refreshData()">Refresh</button>
            <label class="auto-refresh">
                <input type="checkbox" id="autoRefresh" onchange="toggleAutoRefresh()"> Auto-refresh (30s)
            </label>
        </div>
        
        <div class="grid">
            <div class="card">
                <h3>📊 Job Statistics</h3>
                <div id="jobStats">
                    <div class="stat">
                        <span>Total Jobs:</span>
                        <span class="stat-value" id="totalJobs">-</span>
                    </div>
                    <div class="stat">
                        <span>Completed:</span>
                        <span class="stat-value" id="completedJobs">-</span>
                    </div>
                    <div class="stat">
                        <span>Pending:</span>
                        <span class="stat-value" id="pendingJobs">-</span>
                    </div>
                    <div class="stat">
                        <span>Failed:</span>
                        <span class="stat-value" id="failedJobs">-</span>
                    </div>
                    <div class="stat">
                        <span>Progress:</span>
                        <span class="stat-value" id="progressPercent">-</span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill" id="progressBar" style="width: 0%"></div>
                    </div>
                    <div class="stat">
                        <span>Est. Completion:</span>
                        <span class="stat-value" id="estimatedCompletion">-</span>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h3>🖥️ System Status</h3>
                <div id="systemStats">
                    <div class="stat">
                        <span>Uptime:</span>
                        <span class="stat-value" id="uptime">-</span>
                    </div>
                    <div class="stat">
                        <span>Last Update:</span>
                        <span class="stat-value" id="lastUpdate">-</span>
                    </div>
                    <div class="stat">
                        <span>Site URL:</span>
                        <span class="stat-value" id="siteUrl">-</span>
                    </div>
                    <div class="stat">
                        <span>GitHub Actions:</span>
                        <span class="status" id="githubStatus">-</span>
                    </div>
                    <div class="stat">
                        <span>Cloudflare:</span>
                        <span class="status" id="cloudflareStatus">-</span>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h3>📝 Recent Logs</h3>
            <div class="logs" id="logs">
                <div class="log-entry">Loading logs...</div>
            </div>
        </div>

        <div class="card">
            <h3>⚙️ GitHub Workflow Runs</h3>
            <div id="workflowStatus">
                <div class="workflow-run">Loading workflow data...</div>
            </div>
        </div>
    </div>
    
    <script>
        let autoRefreshInterval;
        
        function refreshData() {
            // Fetch job stats
            fetch('/api/stats')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('totalJobs').textContent = data.total;
                    document.getElementById('completedJobs').textContent = data.completed;
                    document.getElementById('pendingJobs').textContent = data.pending;
                    document.getElementById('failedJobs').textContent = data.failed;
                    document.getElementById('progressPercent').textContent = data.progress_percent.toFixed(1) + '%';
                    document.getElementById('progressBar').style.width = data.progress_percent + '%';
                    document.getElementById('estimatedCompletion').textContent = data.estimated_completion || 'Unknown';
                })
                .catch(error => console.error('Error fetching stats:', error));
            
            // Fetch system stats
            fetch('/api/system')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('uptime').textContent = data.uptime;
                    document.getElementById('lastUpdate').textContent = data.last_update;
                    document.getElementById('siteUrl').textContent = data.site_url;
                    
                    const githubStatus = document.getElementById('githubStatus');
                    githubStatus.textContent = data.github_actions_status;
                    githubStatus.className = 'status ' + (data.github_actions_status === 'Active' ? 'online' : 'offline');
                    
                    const cloudflareStatus = document.getElementById('cloudflareStatus');
                    cloudflareStatus.textContent = data.cloudflare_status;
                    cloudflareStatus.className = 'status ' + (data.cloudflare_status === 'Online' ? 'online' : 'offline');
                })
                .catch(error => console.error('Error fetching system stats:', error));
            
            // Fetch logs
            fetch('/api/logs')
                .then(response => response.json())
                .then(data => {
                    const logsContainer = document.getElementById('logs');
                    logsContainer.innerHTML = '';
                    
                    if (data.length === 0) {
                        logsContainer.innerHTML = '<div class="log-entry">No logs available</div>';
                        return;
                    }
                    
                    data.forEach(log => {
                        const logEntry = document.createElement('div');
                        logEntry.className = 'log-entry';
                        logEntry.innerHTML = `
                            <div class="log-timestamp">${log.timestamp}</div>
                            <div>${log.message}</div>
                        `;
                        logsContainer.appendChild(logEntry);
                    });
                })
                .catch(error => console.error('Error fetching logs:', error));

            // Fetch workflow runs
            fetch('/api/workflows')
                .then(response => response.json())
                .then(data => {
                    const container = document.getElementById('workflowStatus');
                    container.innerHTML = '';
                    if (!Array.isArray(data) || data.length === 0) {
                        container.innerHTML = '<div class="workflow-run">No workflow data available.</div>';
                        return;
                    }
                    data.forEach(entry => {
                        const wrapper = document.createElement('div');
                        wrapper.className = 'workflow-run';
                        const runs = entry.runs || [];
                        const latest = runs[0];
                        const status = latest ? `${latest.status || 'unknown'}${latest.conclusion ? ' / ' + latest.conclusion : ''}` : 'no runs yet';
                        const link = latest && latest.html_url ? `<a href="${latest.html_url}" target="_blank" rel="noopener">#${latest.run_number}</a>` : '';
                        wrapper.innerHTML = `
                            <div class="workflow-name">${entry.workflow}</div>
                            <div>Status: <strong>${status}</strong> ${link}</div>
                            <div class="workflow-meta">
                                ${latest ? `Branch ${latest.head_branch || '-'} · ${latest.event || '-'} · ${latest.created_at || '-'}` : ''}
                            </div>
                        `;
                        container.appendChild(wrapper);
                    });
                })
                .catch(error => console.error('Error fetching workflow data:', error));
        }
        
        function toggleAutoRefresh() {
            const checkbox = document.getElementById('autoRefresh');
            if (checkbox.checked) {
                autoRefreshInterval = setInterval(refreshData, 30000); // 30 seconds
            } else {
                clearInterval(autoRefreshInterval);
            }
        }
        
        // Initial load
        refreshData();
    </script>
</body>
</html>
        """

    def get_login_template(self) -> str:
        """Get login HTML template."""
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ChinaXiv Monitor - Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .login-container { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); width: 100%; max-width: 400px; }
        .login-header { text-align: center; margin-bottom: 2rem; }
        .login-header h1 { color: #2c3e50; margin-bottom: 0.5rem; }
        .form-group { margin-bottom: 1rem; }
        .form-group label { display: block; margin-bottom: 0.5rem; color: #2c3e50; }
        .form-group input { width: 100%; padding: 0.75rem; border: 1px solid #ddd; border-radius: 4px; font-size: 1rem; }
        .form-group input:focus { outline: none; border-color: #3498db; }
        .login-btn { width: 100%; background: #3498db; color: white; border: none; padding: 0.75rem; border-radius: 4px; font-size: 1rem; cursor: pointer; }
        .login-btn:hover { background: #2980b9; }
        .error { color: #e74c3c; text-align: center; margin-top: 1rem; }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-header">
            <h1>🔐 Login</h1>
            <p>ChinaXiv Translations Monitor</p>
        </div>
        
        <form method="POST">
            <div class="form-group">
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" required>
            </div>
            
            <div class="form-group">
                <label for="password">Password:</label>
                <input type="password" id="password" name="password" required>
            </div>
            
            <button type="submit" class="login-btn">Login</button>
        </form>
        
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
    </div>
</body>
</html>
        """

    def get_login_success_template(self) -> str:
        """Get login success template."""
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login Successful</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .success-container { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center; }
        .success-container h1 { color: #27ae60; margin-bottom: 1rem; }
        .success-container p { color: #2c3e50; margin-bottom: 2rem; }
        .success-container a { color: #3498db; text-decoration: none; }
        .success-container a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="success-container">
        <h1>✅ Login Successful!</h1>
        <p>You are now authenticated.</p>
        <a href="/">Go to Dashboard</a>
    </div>
    
    <script>
        setTimeout(() => {
            window.location.href = '/';
        }, 2000);
    </script>
</body>
</html>
        """

    def run(self, host="0.0.0.0", port=MONITORING_PORT, debug=False):
        """Run the monitoring dashboard."""
        print("🚀 Starting ChinaXiv Translations Monitor")
        print(f"📊 Dashboard: http://{host}:{port}")
        print(f"🔐 Username: {MONITORING_USERNAME}")
        print(f"🔑 Password: {MONITORING_PASSWORD}")
        print("🌐 Site: https://chinaxiv-english.pages.dev")

        self.app.run(host=host, port=port, debug=debug)


def main():
    """Main function."""
    dashboard = MonitoringDashboard()
    dashboard.run()


if __name__ == "__main__":
    main()
