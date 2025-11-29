from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Sequence, Tuple

from .utils import write_json


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_markdown_report(
    title: str,
    summary_items: Sequence[Tuple[str, Any]],
    reasons: Sequence[str] | None = None,
) -> str:
    lines = [f"# {title}", ""]
    for label, value in summary_items:
        lines.append(f"{label}: {value}")
    if reasons:
        lines.append("")
        lines.append("Reasons:")
        for reason in reasons:
            lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def save_validation_report(
    report_dir: str,
    basename: str,
    payload: Dict[str, Any],
    markdown: str,
    summary: Dict[str, Any] | None = None,
) -> None:
    report_path = Path(report_dir)
    _ensure_dir(report_path)
    write_json(report_path / f"{basename}.json", payload)
    (report_path / f"{basename}.md").write_text(markdown, encoding="utf-8")

    if summary is None:
        summary = payload.get("summary")
    if summary is None:
        return

    site_dir = Path("site") / "stats" / "validation"
    _ensure_dir(site_dir)
    write_json(site_dir / f"{basename}.json", {"summary": summary})
