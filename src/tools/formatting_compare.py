"""
Generate before/after formatting samples for selected translated papers.

Before: Unformatted translation (raw output).
After: LLM formatting pass (math-safe). Fails loudly if LLM unavailable.

Outputs side-by-side HTML pages into `site/samples/` and an index page.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Any, Dict, List, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..utils import ensure_dir, read_json, write_text

# Removed heuristic formatting imports - LLM formatting only
from ..services.formatting_service import FormattingService
from ..config import get_config


def pick_candidates(count: int) -> List[str]:
    """Pick up to N ids with non-empty body_en for better visibility."""
    paths = sorted(glob.glob(os.path.join("data", "translated", "*.json")))
    selected: List[str] = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if (
                d.get("body_en")
                and isinstance(d.get("body_en"), list)
                and len(d["body_en"]) > 20
            ):
                selected.append(d["id"])  # id should match filename stem
                if len(selected) >= count:
                    break
        except Exception:
            continue
    # fallback if not enough
    if len(selected) < count:
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("body_en") and d["id"] not in selected:
                    selected.append(d["id"])
                    if len(selected) >= count:
                        break
            except Exception:
                continue
    return selected


def build_markdown_variants(
    rec: Dict[str, Any],
) -> Tuple[Dict[str, str], Dict[str, str], str]:
    """Return (before_md, after_md, note). Each is a dict with 'abstract' and 'body'."""
    # Before: Raw unformatted translation
    before_abs = rec.get("abstract_en") or ""
    before_body_paras = rec.get("body_en") or []
    before_body = "\n\n".join(p or "" for p in before_body_paras if p and p.strip())
    before = {"abstract": before_abs, "body": before_body}

    # After: LLM formatting pass
    cfg = get_config() or {}
    note = ""
    try:
        svc = FormattingService(cfg)
        after_rec = svc.format_translation(rec, dry_run=False)
        after_abs = after_rec.get("abstract_md") or before_abs
        after_body = after_rec.get("body_md") or before_body
        after = {"abstract": after_abs, "body": after_body}
    except Exception as e:
        note = f"LLM formatting failed: {e}"
        after = before

    return before, after, note


def render_sample_page(
    item: Dict[str, Any],
    before: Dict[str, str],
    after: Dict[str, str],
    note: str,
    out_dir: str,
) -> None:
    env = Environment(
        loader=FileSystemLoader(os.path.join("src", "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )

    # local inline template to keep changes minimal
    template_html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>Formatting Sample – {{ item.id }}</title>
      <style>
        body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; margin: 0; padding: 16px; }
        .hdr { margin-bottom: 16px; }
        .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .panel { border: 1px solid #e1e4e8; border-radius: 8px; padding: 16px; background: #fff; }
        .panel h2 { margin-top: 0; }
        .note { color: #b45309; margin: 8px 0 16px; font-size: 0.9em; }
        .abstract { margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px dashed #ddd; }
        pre, code { background: #f6f8fa; }
        @media (max-width: 900px) { .cols { grid-template-columns: 1fr; } }
      </style>
    </head>
    <body>
      <div class="hdr">
        <h1>{{ item.title_en or 'Untitled' }}</h1>
        <div><strong>ID:</strong> {{ item.id }}</div>
        {% if item.creators %}<div><strong>Authors:</strong> {{ ', '.join(item.creators) }}</div>{% endif %}
        {% if item.date %}<div><strong>Date:</strong> {{ item.date }}</div>{% endif %}
        {% if note %}<div class="note">{{ note }}</div>{% endif %}
      </div>
      <div class="cols">
        <div class="panel">
          <h2>Before (Heuristic)</h2>
          <div class="abstract">
            <h3>Abstract</h3>
            {{ before.abstract | markdown | safe }}
          </div>
          <h3>Full Text</h3>
          {{ before.body | markdown | safe }}
        </div>
        <div class="panel">
          <h2>After (LLM)</h2>
          <div class="abstract">
            <h3>Abstract</h3>
            {{ after.abstract | markdown | safe }}
          </div>
          <h3>Full Text</h3>
          {{ after.body | markdown | safe }}
        </div>
      </div>
    </body>
    </html>
    """

    # Add markdown filter
    try:
        import markdown

        def markdown_filter(text: str) -> str:
            return markdown.markdown(text or "", extensions=["extra", "codehilite"])  # type: ignore

        env.filters["markdown"] = markdown_filter
    except Exception:
        env.filters["markdown"] = lambda s: (s or "")

    tmpl = env.from_string(template_html)
    html = tmpl.render(item=item, before=before, after=after, note=note)
    ensure_dir(out_dir)
    write_text(os.path.join(out_dir, f"{item['id']}.html"), html)


def render_index(ids: List[str], out_dir: str) -> None:
    links = "\n".join(f'<li><a href="{pid}.html">{pid}</a></li>' for pid in ids)
    html = """
    <!doctype html>
    <html><head><meta charset='utf-8'/><title>Formatting Samples</title>
    <style>body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;padding:16px}}</style>
    </head><body>
    <h1>Formatting Samples</h1>
    <ol>{links}</ol>
    <p>Open each link to compare unformatted vs LLM formatting.</p>
    </body></html>
    """.format(links=links)
    ensure_dir(out_dir)
    write_text(os.path.join(out_dir, "index.html"), html)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate before/after formatting samples"
    )
    parser.add_argument(
        "--ids", nargs="*", help="Paper IDs to include (defaults to auto-pick)"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="How many to auto-pick if --ids not provided",
    )
    args = parser.parse_args()

    ids: List[str] = args.ids or pick_candidates(args.count)
    if not ids:
        print("No candidate translations found in data/translated/")
        return 1

    out_dir = os.path.join("site", "samples")
    for pid in ids:
        path = os.path.join("data", "translated", f"{pid}.json")
        if not os.path.exists(path):
            # try file listing by stem search
            matches = glob.glob(os.path.join("data", "translated", f"*{pid}*.json"))
            if not matches:
                print(f"Skip {pid}: not found")
                continue
            path = matches[0]
        rec = read_json(path)
        before, after, note = build_markdown_variants(rec)
        render_sample_page(rec, before, after, note, out_dir)

    render_index(ids, out_dir)
    print(f"Wrote samples for {len(ids)} items to {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
