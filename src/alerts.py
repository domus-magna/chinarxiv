"""
Unified alerting system for ChinaXiv translation pipeline.

Provides a single AlertManager that:
- Aggregates similar alerts within a time window (60s default)
- Sends immediate alerts for critical events (circuit breaker trips)
- Formats consistent, informative Discord messages
"""

from __future__ import annotations

import atexit
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import requests

from .logging_utils import log


@dataclass
class BufferedAlert:
    """An alert waiting to be sent (for aggregation)."""

    level: str
    title: str
    message: str
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)


class AlertManager:
    """
    Unified alerting with aggregation and rate limiting.

    - Similar alerts are grouped by key and aggregated within a time window
    - Critical events (circuit trips, site down) bypass aggregation
    - Thread-safe for concurrent usage
    """

    # Color coding for Discord embeds
    COLORS = {
        "critical": 0xE74C3C,  # Red
        "error": 0xE74C3C,  # Red
        "warning": 0xF39C12,  # Yellow/Orange
        "success": 0x2ECC71,  # Green
        "info": 0x3498DB,  # Blue
    }

    EMOJIS = {
        "critical": "\U0001f6a8",  # Rotating light
        "error": "\u274c",  # X mark
        "warning": "\u26a0\ufe0f",  # Warning
        "success": "\u2705",  # Check mark
        "info": "\U0001f4ca",  # Chart
    }

    def __init__(self, aggregation_window: int = 60):
        """
        Initialize alert manager.

        Args:
            aggregation_window: Seconds to aggregate similar alerts (default 60)
        """
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.enabled = bool(self.webhook_url)
        self.window_seconds = aggregation_window

        # Buffer for aggregated alerts: key -> list of alerts
        self._buffer: Dict[str, List[BufferedAlert]] = {}
        self._buffer_start: Dict[str, datetime] = {}
        self._lock = threading.Lock()

        # Keys that bypass aggregation (immediate send)
        self._immediate_keys: Set[str] = {"circuit_trip", "site_down"}

        # Flush timers
        self._timers: Dict[str, threading.Timer] = {}

        # Register cleanup on exit
        atexit.register(self._flush_all)

        if not self.enabled:
            log("Discord webhook URL not configured. Alerts will be logged only.")

    def alert(
        self,
        level: str,
        title: str,
        message: str,
        key: Optional[str] = None,
        immediate: bool = False,
        **metadata: Any,
    ) -> None:
        """
        Queue an alert. Similar alerts are aggregated by key.

        Args:
            level: Alert level (critical, error, warning, success, info)
            title: Alert title
            message: Alert message
            key: Grouping key for aggregation (defaults to title)
            immediate: If True, send immediately without buffering
            **metadata: Additional data to include in the alert
        """
        key = key or title

        # Critical level or explicit immediate flag bypasses aggregation
        if immediate or key in self._immediate_keys or level == "critical":
            self._send_immediate(level, title, message, **metadata)
            return

        # Buffer for aggregation
        self._buffer_alert(key, level, title, message, **metadata)

    def _buffer_alert(
        self, key: str, level: str, title: str, message: str, **metadata: Any
    ) -> None:
        """Add alert to buffer and schedule flush."""
        alert = BufferedAlert(
            level=level,
            title=title,
            message=message,
            timestamp=datetime.now(timezone.utc),
            metadata=metadata,
        )

        with self._lock:
            if key not in self._buffer:
                self._buffer[key] = []
                self._buffer_start[key] = datetime.now(timezone.utc)
                # Schedule flush after window expires
                self._schedule_flush(key)

            self._buffer[key].append(alert)

    def _schedule_flush(self, key: str) -> None:
        """Schedule a flush for this key after window expires."""
        # Cancel existing timer if any
        if key in self._timers:
            self._timers[key].cancel()

        timer = threading.Timer(self.window_seconds, self._flush_key, args=[key])
        timer.daemon = True
        timer.start()
        self._timers[key] = timer

    def _flush_key(self, key: str) -> None:
        """Flush all buffered alerts for a key as a single aggregated message."""
        with self._lock:
            alerts = self._buffer.pop(key, [])
            self._buffer_start.pop(key, None)
            self._timers.pop(key, None)

        if not alerts:
            return

        # Build aggregated message
        if len(alerts) == 1:
            # Single alert - send as-is
            a = alerts[0]
            self._send_immediate(a.level, a.title, a.message, **a.metadata)
        else:
            # Multiple alerts - aggregate
            self._send_aggregated(key, alerts)

    def _send_aggregated(self, key: str, alerts: List[BufferedAlert]) -> None:
        """Send aggregated summary of multiple alerts."""
        # Use highest severity level from buffered alerts
        level_priority = {"critical": 4, "error": 3, "warning": 2, "info": 1, "success": 0}
        highest_level = max(alerts, key=lambda a: level_priority.get(a.level, 0)).level

        # Special handling for stage failures - cleaner format
        if key.startswith("stage_failure_"):
            self._send_stage_failure_summary(key, alerts, highest_level)
            return

        # Group by message/error type
        by_message: Dict[str, int] = {}
        for a in alerts:
            msg = a.message or a.title
            by_message[msg] = by_message.get(msg, 0) + 1

        # Build summary
        lines = [f"**{len(alerts)} alerts** in the last {self.window_seconds}s:"]
        for msg, count in sorted(by_message.items(), key=lambda x: -x[1]):
            if count > 1:
                lines.append(f"  \u2022 {msg}: {count}x")
            else:
                lines.append(f"  \u2022 {msg}")

        title = f"Alert Summary: {key}"
        self._send_to_discord(highest_level, title, "\n".join(lines))

    def _send_stage_failure_summary(
        self, key: str, alerts: List[BufferedAlert], level: str
    ) -> None:
        """Send formatted summary for stage failures."""
        # Extract stage name from key (e.g., "stage_failure_harvest" -> "Harvest")
        stage = key.replace("stage_failure_", "").title()

        # Group by error message
        by_error: Dict[str, int] = {}
        for a in alerts:
            error_msg = a.message or "Unknown error"
            by_error[error_msg] = by_error.get(error_msg, 0) + 1

        # Build summary lines
        lines = [f"**{len(alerts)} papers** failed in the last {self.window_seconds}s:"]
        for error_msg, count in sorted(by_error.items(), key=lambda x: -x[1]):
            if count > 1:
                lines.append(f"  \u2022 {error_msg}: {count}x")
            else:
                lines.append(f"  \u2022 {error_msg}")

        # Add GitHub Actions link if available
        run_id = os.environ.get("GITHUB_RUN_ID")
        repo = os.environ.get("GITHUB_REPOSITORY", "domus-magna/chinaxiv-english")
        if run_id:
            lines.append(f"\n[View logs](https://github.com/{repo}/actions/runs/{run_id})")

        title = f"{stage} Stage Failures"
        self._send_to_discord(level, title, "\n".join(lines))

    def _flush_all(self) -> None:
        """Flush all pending alerts (called at exit)."""
        keys = list(self._buffer.keys())
        for key in keys:
            try:
                self._flush_key(key)
            except Exception as e:
                log(f"Failed to flush alerts for {key}: {e}")

    def _send_immediate(
        self, level: str, title: str, message: str, **metadata: Any
    ) -> None:
        """Send alert immediately without buffering."""
        # Build fields from metadata
        fields = []
        for k, v in metadata.items():
            if v is not None:
                fields.append({"name": k.replace("_", " ").title(), "value": str(v), "inline": True})

        self._send_to_discord(level, title, message, fields=fields)

    def _send_to_discord(
        self,
        level: str,
        title: str,
        message: str,
        fields: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Send formatted message to Discord webhook."""
        # Always log
        log_msg = f"[{level.upper()}] {title}: {message}"
        if level in ("critical", "error"):
            log(f"\u274c {log_msg}")
        elif level == "warning":
            log(f"\u26a0\ufe0f {log_msg}")
        else:
            log(f"\u2139\ufe0f {log_msg}")

        if not self.enabled:
            return False

        emoji = self.EMOJIS.get(level, "\U0001f4ca")
        color = self.COLORS.get(level, 0x3498DB)

        embed = {
            "title": f"{emoji} {title}",
            "description": message,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "ChinaRxiv Translation Pipeline"},
        }

        if fields:
            embed["fields"] = fields

        try:
            response = requests.post(
                self.webhook_url,
                json={"embeds": [embed]},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            log(f"Discord webhook failed: {e}")
            return False

    # Convenience methods
    def critical(self, title: str, message: str, **kw: Any) -> None:
        """Send critical alert (always immediate)."""
        self.alert("critical", title, message, immediate=True, **kw)

    def error(self, title: str, message: str, **kw: Any) -> None:
        """Send error alert."""
        self.alert("error", title, message, **kw)

    def warning(self, title: str, message: str, **kw: Any) -> None:
        """Send warning alert."""
        self.alert("warning", title, message, **kw)

    def success(self, title: str, message: str, **kw: Any) -> None:
        """Send success alert."""
        self.alert("success", title, message, **kw)

    def info(self, title: str, message: str, **kw: Any) -> None:
        """Send info alert."""
        self.alert("info", title, message, **kw)

    # Specialized methods
    def circuit_tripped(
        self,
        api: str,
        error_code: str,
        consecutive_count: int,
        details: Optional[str] = None,
    ) -> None:
        """
        Send immediate alert for circuit breaker trip.

        Args:
            api: Name of the API (e.g., "OpenRouter", "Gemini")
            error_code: The error code that caused the trip
            consecutive_count: Number of consecutive failures
            details: Additional error details
        """
        message = (
            f"**Error:** {error_code} ({consecutive_count} consecutive)\n"
            f"Pipeline stopped to prevent further API costs."
        )
        if details:
            # Truncate long details
            details = details[:300] + "..." if len(details) > 300 else details
            message += f"\n**Details:** {details}"

        self.critical(
            f"Circuit Breaker Tripped: {api}",
            message,
            key="circuit_trip",
            api=api,
            error_code=error_code,
        )

    def pipeline_complete(
        self,
        successes: int,
        failures: int,
        flagged: int = 0,
        total: Optional[int] = None,
        source: str = "pipeline",
    ) -> None:
        """
        Send pipeline completion alert with appropriate level based on results.

        Args:
            successes: Number of successfully translated papers
            failures: Number of failed papers
            flagged: Number of papers flagged by QA
            total: Total papers processed (defaults to successes + failures + flagged)
            source: Source of the pipeline run
        """
        total = total or (successes + failures + flagged)

        if total == 0:
            self.info("Pipeline Complete", "No papers processed.", source=source)
            return

        # Determine level based on results
        if failures == 0:
            level = "success"
            title = "Pipeline Complete"
        elif failures < total:
            level = "warning"
            title = "Pipeline Complete with Failures"
        else:
            level = "error"
            title = "Pipeline Failed"

        # Build message
        lines = [f"**Processed:** {total} papers"]
        lines.append(f"  \u2022 Translated: {successes}")
        if flagged > 0:
            lines.append(f"  \u2022 Flagged (QA): {flagged}")
        if failures > 0:
            lines.append(f"  \u2022 Failed: {failures} \u26a0\ufe0f")

        self.alert(
            level,
            title,
            "\n".join(lines),
            immediate=True,
            source=source,
            success_rate=f"{successes / total * 100:.1f}%" if total > 0 else "N/A",
        )

    def api_error(
        self,
        api: str,
        error_code: str,
        message: str,
        status_code: Optional[int] = None,
    ) -> None:
        """
        Record API error (aggregated by API name).

        Args:
            api: Name of the API
            error_code: Error code
            message: Error message
            status_code: HTTP status code if applicable
        """
        key = f"api_error_{api.lower()}"
        self.alert(
            "warning",
            f"{api} API Error",
            f"{error_code}: {message}",
            key=key,
            api=api,
            error_code=error_code,
            status_code=status_code,
        )

    def site_down(self, error: str, duration_minutes: int = 0) -> None:
        """Send immediate alert for site down."""
        message = f"Site is not responding.\n**Error:** {error}"
        if duration_minutes > 0:
            message += f"\n**Duration:** {duration_minutes} minutes"

        self.critical(
            "Site Down",
            message,
            key="site_down",
            error=error,
            duration=f"{duration_minutes}m",
        )

    def cost_threshold(self, daily_cost: float, threshold: float = 5.0) -> None:
        """Send cost threshold alert (aggregated)."""
        self.warning(
            "Cost Threshold Exceeded",
            f"Daily cost ${daily_cost:.2f} exceeds threshold ${threshold:.2f}",
            key="cost_warning",
            daily_cost=f"${daily_cost:.2f}",
            threshold=f"${threshold:.2f}",
            excess=f"${daily_cost - threshold:.2f}",
        )

    def pipeline_started(
        self,
        papers_count: int,
        source: str = "pipeline",
        month: Optional[str] = None,
        with_figures: bool = False,
    ) -> None:
        """
        Send alert when pipeline begins processing.

        Args:
            papers_count: Number of papers to process
            source: Source/name of the pipeline
            month: Optional month being processed (YYYYMM format)
            with_figures: Whether figure translation is enabled
        """
        message = f"Starting translation of **{papers_count} papers**"
        if month:
            message += f" from {month}"
        if with_figures:
            message += " (with figures)"

        self.info(
            f"Pipeline Started: {source}",
            message,
            immediate=True,  # Don't aggregate start alerts
            source=source,
            papers=papers_count,
        )

    def stage_failure(
        self,
        stage: str,
        paper_id: str,
        error: str,
    ) -> None:
        """
        Record a pipeline stage failure (batched by stage type).

        Multiple failures within the aggregation window are combined into
        a single Discord message grouped by error type.

        Args:
            stage: Stage name (harvest, text, figures, pdf)
            paper_id: Paper that failed
            error: Error message
        """
        key = f"stage_failure_{stage}"
        # Truncate error for grouping (full error still logged)
        error_short = error[:80] if len(error) > 80 else error

        self.alert(
            "error",  # NOT critical - enables batching
            f"{stage.title()} Stage Failure",
            error_short,  # Used for grouping in aggregation
            key=key,
            paper_id=paper_id,
            stage=stage,
        )


# Global singleton instance
_manager: Optional[AlertManager] = None
_lock = threading.Lock()


def get_alert_manager() -> AlertManager:
    """Get the global AlertManager instance."""
    global _manager
    if _manager is None:
        with _lock:
            if _manager is None:
                _manager = AlertManager()
    return _manager


# Module-level convenience functions
def alert_critical(title: str, message: str, **kw: Any) -> None:
    """Send critical alert (always immediate)."""
    get_alert_manager().critical(title, message, **kw)


def alert_error(title: str, message: str, **kw: Any) -> None:
    """Send error alert."""
    get_alert_manager().error(title, message, **kw)


def alert_warning(title: str, message: str, **kw: Any) -> None:
    """Send warning alert."""
    get_alert_manager().warning(title, message, **kw)


def alert_success(title: str, message: str, **kw: Any) -> None:
    """Send success alert."""
    get_alert_manager().success(title, message, **kw)


def alert_info(title: str, message: str, **kw: Any) -> None:
    """Send info alert."""
    get_alert_manager().info(title, message, **kw)


def circuit_tripped(
    api: str, error_code: str, consecutive_count: int, details: Optional[str] = None
) -> None:
    """Send circuit breaker trip alert."""
    get_alert_manager().circuit_tripped(api, error_code, consecutive_count, details)


def pipeline_complete(
    successes: int,
    failures: int,
    flagged: int = 0,
    total: Optional[int] = None,
    source: str = "pipeline",
) -> None:
    """Send pipeline completion alert."""
    get_alert_manager().pipeline_complete(successes, failures, flagged, total, source)


def api_error(
    api: str,
    error_code: str,
    message: str,
    status_code: Optional[int] = None,
) -> None:
    """Record API error (aggregated)."""
    get_alert_manager().api_error(api, error_code, message, status_code)


def site_down(error: str, duration_minutes: int = 0) -> None:
    """Send site down alert."""
    get_alert_manager().site_down(error, duration_minutes)


def cost_threshold(daily_cost: float, threshold: float = 5.0) -> None:
    """Send cost threshold alert."""
    get_alert_manager().cost_threshold(daily_cost, threshold)


def pipeline_started(
    papers_count: int,
    source: str = "pipeline",
    month: Optional[str] = None,
    with_figures: bool = False,
) -> None:
    """Send pipeline started alert."""
    get_alert_manager().pipeline_started(papers_count, source, month, with_figures)


def stage_failure(stage: str, paper_id: str, error: str) -> None:
    """
    Record a pipeline stage failure (batched by stage type).

    Multiple failures within 60s are combined into a single Discord message.
    """
    get_alert_manager().stage_failure(stage, paper_id, error)
