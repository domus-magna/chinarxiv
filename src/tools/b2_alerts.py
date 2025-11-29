"""
Backblaze B2 alert aggregator (Discord) with 15-minute throttle.

Usage:
  python -m src.tools.b2_alerts add "message text"
  python -m src.tools.b2_alerts flush

State files (local workspace; CI ephemeral but sufficient per run):
  data/monitoring/b2_alert_buffer.json  -> list of messages
  data/monitoring/b2_alert_state.json   -> {"last_sent": iso}

Throttle: send at most once per 15 minutes. 'flush' sends if any buffered
messages and last_sent is older than 15 minutes, otherwise no-op.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

# Import DiscordAlerts from src
from ..discord_alerts import DiscordAlerts


BUFFER_PATH = Path("data/monitoring/b2_alert_buffer.json")
STATE_PATH = Path("data/monitoring/b2_alert_state.json")


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def add_message(msg: str) -> None:
    _ensure_dir(BUFFER_PATH)
    buf: List[str] = []
    if BUFFER_PATH.exists():
        try:
            buf = json.loads(BUFFER_PATH.read_text(encoding="utf-8"))
            if not isinstance(buf, list):
                buf = []
        except Exception:
            buf = []
    buf.append(msg)
    BUFFER_PATH.write_text(
        json.dumps(buf, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def flush_if_due() -> bool:
    # Nothing to send
    if not BUFFER_PATH.exists():
        return False
    try:
        buf = json.loads(BUFFER_PATH.read_text(encoding="utf-8"))
    except Exception:
        buf = []
    if not buf:
        return False

    # Check throttle
    last_sent: datetime | None = None
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            ts = state.get("last_sent")
            if ts:
                last_sent = datetime.fromisoformat(ts)
        except Exception:
            last_sent = None

    now = datetime.now(timezone.utc)
    if last_sent and now - last_sent < timedelta(minutes=15):
        return False

    # Send aggregated alert
    alerts = DiscordAlerts()
    if not alerts.enabled:
        return False

    # Prepare compact summary (first 5 lines)
    preview = "\n".join([f"- {m}" for m in buf[:5]])
    extra = "" if len(buf) <= 5 else f"\n(+{len(buf)-5} more)"
    alerts.send_alert(
        alert_type="warning",
        title="B2 operations skipped or failed",
        description=(
            "One or more Backblaze B2 operations were skipped or failed.\n"
            "This alert aggregates events over ~15 minutes."
        ),
        fields=[
            {"name": "Count", "value": str(len(buf)), "inline": True},
            {
                "name": "Preview",
                "value": (preview + extra) or "(none)",
                "inline": False,
            },
        ],
    )

    # Update state and clear buffer
    _ensure_dir(STATE_PATH)
    STATE_PATH.write_text(json.dumps({"last_sent": now.isoformat()}), encoding="utf-8")
    BUFFER_PATH.unlink(missing_ok=True)
    return True


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: b2_alerts add <msg> | flush")
        return 2
    cmd = argv[1]
    if cmd == "add":
        if len(argv) < 3:
            print("usage: b2_alerts add <msg>")
            return 2
        add_message(" ".join(argv[2:]).strip())
        # opportunistically try to flush if due
        flush_if_due()
        return 0
    if cmd == "flush":
        flushed = flush_if_due()
        print("flushed" if flushed else "no-op")
        return 0
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
