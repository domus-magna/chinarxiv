"""
Discord alerting system for ChinaXiv translation pipeline.
Sends structured alerts to Discord webhook for monitoring and notifications.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional, List

# Import requests
import requests


class DiscordAlerts:
    """Discord webhook alerting system."""

    def __init__(self, webhook_url: Optional[str] = None):
        """Initialize Discord alerts."""
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
        self.enabled = bool(self.webhook_url)

        if not self.enabled:
            print("⚠️  Discord webhook URL not configured. Alerts disabled.")

    def _send_webhook(self, data: Dict[str, Any]) -> bool:
        """Send webhook request to Discord."""
        if not self.enabled:
            return False

        try:
            response = requests.post(
                self.webhook_url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"❌ Discord webhook failed: {e}")
            return False

    def send_alert(
        self,
        alert_type: str,
        title: str,
        description: str = "",
        fields: List[Dict[str, Any]] = None,
        footer: str = "ChinaRxiv Translation Pipeline",
    ) -> bool:
        """Send a Discord alert with rich embed."""

        # Color coding based on alert type
        colors = {
            "critical": 15158332,  # Red
            "warning": 16776960,  # Yellow
            "success": 3066993,  # Green
            "info": 3447003,  # Blue
            "error": 15158332,  # Red
            "debug": 7506394,  # Gray
        }

        # Emoji mapping
        emojis = {
            "critical": "🚨",
            "warning": "⚠️",
            "success": "✅",
            "info": "📊",
            "error": "❌",
            "debug": "🔍",
        }

        color = colors.get(alert_type, 3447003)
        emoji = emojis.get(alert_type, "📊")

        embed = {
            "title": f"{emoji} {title}",
            "description": description,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {"text": footer},
        }

        if fields:
            embed["fields"] = fields

        data = {"embeds": [embed]}
        return self._send_webhook(data)

    def pipeline_failure(self, error: str, stage: str = "Unknown") -> bool:
        """Send pipeline failure alert."""
        return self.send_alert(
            alert_type="critical",
            title="Pipeline Failure",
            description=f"Translation pipeline failed at {stage} stage",
            fields=[
                {"name": "Stage", "value": stage, "inline": True},
                {"name": "Error", "value": error, "inline": False},
                {
                    "name": "Time",
                    "value": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "inline": True,
                },
            ],
        )

    def pipeline_success(self, papers_processed: int, cost: float = 0.0) -> bool:
        """Send pipeline success alert."""
        return self.send_alert(
            alert_type="success",
            title="Pipeline Success",
            description=f"Successfully processed {papers_processed} papers",
            fields=[
                {
                    "name": "Papers Processed",
                    "value": str(papers_processed),
                    "inline": True,
                },
                {"name": "Cost", "value": f"${cost:.4f}", "inline": True},
                {
                    "name": "Time",
                    "value": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "inline": True,
                },
            ],
        )

    def cost_threshold(self, daily_cost: float, threshold: float = 5.0) -> bool:
        """Send cost threshold alert."""
        return self.send_alert(
            alert_type="warning",
            title="Cost Threshold Exceeded",
            description="Daily translation cost exceeded threshold",
            fields=[
                {"name": "Daily Cost", "value": f"${daily_cost:.4f}", "inline": True},
                {"name": "Threshold", "value": f"${threshold:.2f}", "inline": True},
                {
                    "name": "Excess",
                    "value": f"${daily_cost - threshold:.4f}",
                    "inline": True,
                },
            ],
        )

    def site_down(self, error: str, duration_minutes: int = 0) -> bool:
        """Send site down alert."""
        return self.send_alert(
            alert_type="critical",
            title="Site Down",
            description="Site is not responding",
            fields=[
                {"name": "Error", "value": error, "inline": False},
                {
                    "name": "Duration",
                    "value": f"{duration_minutes} minutes",
                    "inline": True,
                },
                {
                    "name": "Time",
                    "value": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "inline": True,
                },
            ],
        )

    def api_error(self, service: str, error: str, status_code: int = None) -> bool:
        """Send API error alert."""
        fields = [
            {"name": "Service", "value": service, "inline": True},
            {"name": "Error", "value": error, "inline": False},
        ]

        if status_code:
            fields.append(
                {"name": "Status Code", "value": str(status_code), "inline": True}
            )

        return self.send_alert(
            alert_type="error",
            title="API Error",
            description=f"{service} API is experiencing issues",
            fields=fields,
        )

    def daily_summary(self, stats: Dict[str, Any]) -> bool:
        """Send daily summary report."""
        fields = []

        # Add key metrics
        if "papers_processed" in stats:
            fields.append(
                {
                    "name": "Papers Processed",
                    "value": str(stats["papers_processed"]),
                    "inline": True,
                }
            )

        if "daily_cost" in stats:
            fields.append(
                {
                    "name": "Daily Cost",
                    "value": f"${stats['daily_cost']:.4f}",
                    "inline": True,
                }
            )

        if "success_rate" in stats:
            fields.append(
                {
                    "name": "Success Rate",
                    "value": f"{stats['success_rate']:.1f}%",
                    "inline": True,
                }
            )

        if "site_status" in stats:
            status_emoji = "✅" if stats["site_status"] == "healthy" else "❌"
            fields.append(
                {
                    "name": "Site Status",
                    "value": f"{status_emoji} {stats['site_status'].title()}",
                    "inline": True,
                }
            )

        if "search_index_size" in stats:
            fields.append(
                {
                    "name": "Search Index",
                    "value": f"{stats['search_index_size']:,} papers",
                    "inline": True,
                }
            )

        if "last_update" in stats:
            fields.append(
                {"name": "Last Update", "value": stats["last_update"], "inline": True}
            )

        return self.send_alert(
            alert_type="info",
            title="Daily Summary",
            description="ChinaRxiv translation pipeline daily report",
            fields=fields,
        )

    def test_alert(self) -> bool:
        """Send test alert to verify webhook configuration."""
        return self.send_alert(
            alert_type="info",
            title="Test Alert",
            description="Discord monitoring integration test",
            fields=[
                {
                    "name": "Status",
                    "value": "Webhook configured successfully",
                    "inline": True,
                },
                {
                    "name": "Time",
                    "value": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "inline": True,
                },
            ],
        )


def test_discord_webhook(webhook_url: str = None) -> bool:
    """Test Discord webhook configuration."""
    alerts = DiscordAlerts(webhook_url)
    return alerts.test_alert()


if __name__ == "__main__":
    # Test the Discord webhook
    import sys

    if len(sys.argv) > 1:
        webhook_url = sys.argv[1]
    else:
        webhook_url = None

    print("Testing Discord webhook...")
    success = test_discord_webhook(webhook_url)

    if success:
        print("✅ Discord webhook test successful!")
    else:
        print("❌ Discord webhook test failed!")
        print("Make sure DISCORD_WEBHOOK_URL is set or provide webhook URL as argument")
