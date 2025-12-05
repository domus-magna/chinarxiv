"""
Tests for Discord alerting system.

These tests verify webhook payload structure and error handling
without actually sending webhooks to Discord.
"""

from unittest.mock import patch, MagicMock

from src.discord_alerts import DiscordAlerts, test_discord_webhook


class TestDiscordAlertsInit:
    """Test DiscordAlerts initialization."""

    def test_init_with_webhook_url(self):
        """Initialize with explicit webhook URL."""
        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        assert alerts.enabled is True
        assert alerts.webhook_url == "https://discord.com/api/webhooks/123/abc"

    def test_init_without_webhook_url_disabled(self, monkeypatch):
        """Initialize without webhook URL disables alerts."""
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        alerts = DiscordAlerts()
        assert alerts.enabled is False

    def test_init_from_env_var(self, monkeypatch):
        """Initialize from environment variable."""
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/456/def")
        alerts = DiscordAlerts()
        assert alerts.enabled is True
        assert alerts.webhook_url == "https://discord.com/api/webhooks/456/def"


class TestSendWebhook:
    """Test _send_webhook method."""

    def test_send_webhook_disabled(self):
        """Webhook not sent when disabled."""
        alerts = DiscordAlerts(webhook_url=None)
        result = alerts._send_webhook({"test": "data"})
        assert result is False

    @patch("src.discord_alerts.requests.post")
    def test_send_webhook_success(self, mock_post):
        """Successful webhook delivery."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = alerts._send_webhook({"test": "data"})

        assert result is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args.kwargs["json"] == {"test": "data"}
        assert call_args.kwargs["timeout"] == 10

    @patch("src.discord_alerts.requests.post")
    def test_send_webhook_timeout(self, mock_post):
        """Webhook timeout is handled gracefully."""
        import requests
        mock_post.side_effect = requests.Timeout("Connection timed out")

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = alerts._send_webhook({"test": "data"})

        assert result is False

    @patch("src.discord_alerts.requests.post")
    def test_send_webhook_http_error(self, mock_post):
        """HTTP errors are handled gracefully."""
        import requests
        mock_post.side_effect = requests.HTTPError("429 Too Many Requests")

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = alerts._send_webhook({"test": "data"})

        assert result is False

    @patch("src.discord_alerts.requests.post")
    def test_send_webhook_connection_error(self, mock_post):
        """Connection errors are handled gracefully."""
        import requests
        mock_post.side_effect = requests.ConnectionError("DNS resolution failed")

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = alerts._send_webhook({"test": "data"})

        assert result is False


class TestSendAlert:
    """Test send_alert method and embed structure."""

    @patch("src.discord_alerts.requests.post")
    def test_send_alert_embed_structure(self, mock_post):
        """Alert creates proper Discord embed structure."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        alerts.send_alert(
            alert_type="info",
            title="Test Title",
            description="Test Description",
            fields=[{"name": "Field1", "value": "Value1", "inline": True}],
        )

        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]

        # Check embed structure
        assert "embeds" in payload
        assert len(payload["embeds"]) == 1
        embed = payload["embeds"][0]

        assert "Test Title" in embed["title"]
        assert embed["description"] == "Test Description"
        assert "timestamp" in embed
        assert "footer" in embed
        assert embed["fields"] == [{"name": "Field1", "value": "Value1", "inline": True}]

    @patch("src.discord_alerts.requests.post")
    def test_alert_type_colors(self, mock_post):
        """Different alert types have different colors."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")

        colors = {}
        for alert_type in ["critical", "warning", "success", "info", "error", "debug"]:
            alerts.send_alert(alert_type=alert_type, title=f"{alert_type} test")
            call_args = mock_post.call_args
            embed = call_args.kwargs["json"]["embeds"][0]
            colors[alert_type] = embed["color"]

        # Critical and error should be red (same color)
        assert colors["critical"] == colors["error"]
        # Success should be green (different from red)
        assert colors["success"] != colors["critical"]
        # Warning should be yellow (different from both)
        assert colors["warning"] != colors["success"]
        assert colors["warning"] != colors["critical"]

    @patch("src.discord_alerts.requests.post")
    def test_alert_type_emojis(self, mock_post):
        """Different alert types have appropriate emojis."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")

        emoji_map = {
            "critical": "🚨",
            "warning": "⚠️",
            "success": "✅",
            "info": "📊",
            "error": "❌",
            "debug": "🔍",
        }

        for alert_type, expected_emoji in emoji_map.items():
            alerts.send_alert(alert_type=alert_type, title="Test")
            call_args = mock_post.call_args
            title = call_args.kwargs["json"]["embeds"][0]["title"]
            assert expected_emoji in title, f"Missing {expected_emoji} for {alert_type}"


class TestPipelineAlerts:
    """Test pipeline-specific alert methods."""

    @patch("src.discord_alerts.requests.post")
    def test_pipeline_failure(self, mock_post):
        """Pipeline failure alert has correct structure."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = alerts.pipeline_failure(error="Connection refused", stage="Translation")

        assert result is True
        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        assert "Pipeline Failure" in embed["title"]
        assert "Translation" in embed["description"]

        # Check fields
        field_names = [f["name"] for f in embed["fields"]]
        assert "Stage" in field_names
        assert "Error" in field_names
        assert "Time" in field_names

    @patch("src.discord_alerts.requests.post")
    def test_pipeline_success(self, mock_post):
        """Pipeline success alert has correct structure."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = alerts.pipeline_success(papers_processed=42, cost=1.2345)

        assert result is True
        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        assert "Pipeline Success" in embed["title"]
        assert "42" in embed["description"]

        # Check cost formatting
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert fields["Papers Processed"] == "42"
        assert "$1.2345" in fields["Cost"]


class TestMonitoringAlerts:
    """Test monitoring and operational alerts."""

    @patch("src.discord_alerts.requests.post")
    def test_cost_threshold(self, mock_post):
        """Cost threshold alert includes correct calculations."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        alerts.cost_threshold(daily_cost=7.5, threshold=5.0)

        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert "$7.5" in fields["Daily Cost"]
        assert "$5.0" in fields["Threshold"]
        assert "$2.5" in fields["Excess"]  # 7.5 - 5.0 = 2.5

    @patch("src.discord_alerts.requests.post")
    def test_site_down(self, mock_post):
        """Site down alert includes duration."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        alerts.site_down(error="503 Service Unavailable", duration_minutes=15)

        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert "503 Service Unavailable" in fields["Error"]
        assert "15 minutes" in fields["Duration"]

    @patch("src.discord_alerts.requests.post")
    def test_api_error_with_status(self, mock_post):
        """API error alert includes status code when provided."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        alerts.api_error(service="OpenRouter", error="Rate limited", status_code=429)

        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        field_names = [f["name"] for f in embed["fields"]]
        assert "Status Code" in field_names

        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert fields["Status Code"] == "429"

    @patch("src.discord_alerts.requests.post")
    def test_api_error_without_status(self, mock_post):
        """API error alert omits status code when not provided."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        alerts.api_error(service="OpenRouter", error="Connection refused")

        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        field_names = [f["name"] for f in embed["fields"]]
        assert "Status Code" not in field_names


class TestDailySummary:
    """Test daily summary report."""

    @patch("src.discord_alerts.requests.post")
    def test_daily_summary_all_fields(self, mock_post):
        """Daily summary includes all stats when provided."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        alerts.daily_summary({
            "papers_processed": 100,
            "daily_cost": 2.5,
            "success_rate": 95.5,
            "site_status": "healthy",
            "search_index_size": 5000,
            "last_update": "2025-01-01 12:00:00",
        })

        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        field_names = [f["name"] for f in embed["fields"]]
        assert "Papers Processed" in field_names
        assert "Daily Cost" in field_names
        assert "Success Rate" in field_names
        assert "Site Status" in field_names
        assert "Search Index" in field_names
        assert "Last Update" in field_names

    @patch("src.discord_alerts.requests.post")
    def test_daily_summary_partial_fields(self, mock_post):
        """Daily summary handles partial stats."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        alerts.daily_summary({"papers_processed": 50})

        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        field_names = [f["name"] for f in embed["fields"]]
        assert "Papers Processed" in field_names
        assert "Daily Cost" not in field_names  # Not provided

    @patch("src.discord_alerts.requests.post")
    def test_daily_summary_site_status_emoji(self, mock_post):
        """Daily summary shows correct emoji for site status."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")

        # Test healthy status
        alerts.daily_summary({"site_status": "healthy"})
        call_args = mock_post.call_args
        fields = {f["name"]: f["value"] for f in call_args.kwargs["json"]["embeds"][0]["fields"]}
        assert "✅" in fields["Site Status"]

        # Test unhealthy status
        alerts.daily_summary({"site_status": "down"})
        call_args = mock_post.call_args
        fields = {f["name"]: f["value"] for f in call_args.kwargs["json"]["embeds"][0]["fields"]}
        assert "❌" in fields["Site Status"]


class TestTestAlert:
    """Test the test_alert method."""

    @patch("src.discord_alerts.requests.post")
    def test_test_alert(self, mock_post):
        """Test alert sends correctly formatted message."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        alerts = DiscordAlerts(webhook_url="https://discord.com/api/webhooks/123/abc")
        result = alerts.test_alert()

        assert result is True
        call_args = mock_post.call_args
        embed = call_args.kwargs["json"]["embeds"][0]

        assert "Test Alert" in embed["title"]
        field_names = [f["name"] for f in embed["fields"]]
        assert "Status" in field_names


class TestConvenienceFunction:
    """Test module-level convenience function."""

    @patch("src.discord_alerts.requests.post")
    def test_test_discord_webhook(self, mock_post):
        """Convenience function works correctly."""
        mock_post.return_value = MagicMock(status_code=204)
        mock_post.return_value.raise_for_status = MagicMock()

        result = test_discord_webhook("https://discord.com/api/webhooks/123/abc")
        assert result is True

    def test_test_discord_webhook_no_url(self, monkeypatch):
        """Convenience function returns False without URL."""
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        result = test_discord_webhook()
        assert result is False
