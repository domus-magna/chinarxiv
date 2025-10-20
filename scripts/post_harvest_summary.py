#!/usr/bin/env python3
"""Post harvest gate summary to Discord."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import requests


def load_report(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def summarize_failures(results: Dict[str, Dict], limit: int = 3) -> List[Tuple[str, List[str], List[str]]]:
    failures: List[Tuple[str, List[str], List[str]]] = []
    for record_id, payload in results.items():
        schema_errors = payload.get("schema_errors") or []
        pdf_info = payload.get("pdf") or {}
        pdf_issues = pdf_info.get("issues") or []

        if schema_errors or pdf_issues:
            failures.append((record_id, list(schema_errors), list(pdf_issues)))

    return failures[:limit]


def format_message(
    summary: Dict,
    results: Dict[str, Dict],
    repo: str,
    run_id: str,
) -> str:
    status_emoji = "✅" if summary.get("pass") else "❌"
    title = summary.get("records_path") or "Unknown records"
    total = summary.get("total", 0)
    schema_pass = summary.get("schema_pass", 0)
    pdf_ok = summary.get("pdf_ok", 0)
    schema_rate = summary.get("schema_rate")
    pdf_rate = summary.get("pdf_rate")

    local_cached = 0
    for payload in results.values():
        resolved = (payload.get("pdf") or {}).get("resolved_url")
        if isinstance(resolved, str) and not resolved.startswith("http"):
            local_cached += 1

    lines: List[str] = [
        f"{status_emoji} Harvest Gate ({title})",
        "",
        f"Records processed: {total} ({schema_pass} schema-clean, {max(total - schema_pass, 0)} flagged)",
        f"PDFs retrieved: {pdf_ok}/{total} ({pdf_rate}% passing gate)",
    ]

    if local_cached:
        lines.append(f"Local cache coverage: {local_cached}/{pdf_ok or total}")

    lines.append(f"Duplicates detected: {summary.get('dup_ids', 0)}")
    lines.append(f"Gate status: {'PASS' if summary.get('pass') else 'FAIL'}")

    reasons = summary.get("reasons") or []
    if reasons:
        lines.append(f"Reasons: {', '.join(reasons)}")

    failures = summarize_failures(results)
    if failures:
        lines.append("")
        lines.append("Sample failures:")
        for record_id, schema_errors, pdf_issues in failures:
            details = []
            if schema_errors:
                details.append(f"schema={'; '.join(schema_errors)}")
            if pdf_issues:
                details.append(f"pdf={'; '.join(pdf_issues)}")
            lines.append(f"• {record_id}: {', '.join(details)}")

    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"
    lines.append("")
    lines.append(f"Run: {run_url}")

    return "\n".join(lines)


def post_to_discord(webhook: str, message: str) -> None:
    response = requests.post(webhook, json={"content": message}, timeout=10)
    response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="Post harvest gate summary to Discord.")
    parser.add_argument("--report", required=True, help="Path to harvest_report.json")
    parser.add_argument("--webhook", required=False, help="Discord webhook URL")
    parser.add_argument("--repo", required=True, help="GitHub repository (owner/repo)")
    parser.add_argument("--run-id", required=True, help="GitHub Actions run ID")
    args = parser.parse_args()

    webhook = args.webhook or os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("post_harvest_summary: webhook not provided; skipping notification.")
        return 0

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"post_harvest_summary: report not found at {report_path}")
        return 0

    try:
        payload = load_report(report_path)
    except Exception as exc:
        print(f"post_harvest_summary: failed to read report: {exc}")
        return 1

    summary = payload.get("summary") or {}
    results = payload.get("results") or {}
    message = format_message(summary, results, args.repo, args.run_id)

    try:
        post_to_discord(webhook, message)
    except Exception as exc:
        print(f"post_harvest_summary: failed to post to discord: {exc}")
        return 1

    print("post_harvest_summary: notification sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
