from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape
import time

from .utils import ensure_dir, log, read_json, write_text, write_json
from .data_utils import has_full_body_content


def load_figure_manifest() -> Dict[str, Any]:
    """
    Load the figure manifest from B2 or local cache.

    The manifest contains information about which papers have translated figures
    and the URLs to those figures. This is used to:
    1. Set `_has_translated_figures` flag on each paper
    2. Provide figure URLs for the figure gallery

    Returns:
        Dict mapping paper_id to figure info, or empty dict if unavailable.
    """
    manifest_cache = Path("data/figure_manifest.json")
    manifest: Dict[str, Any] = {}

    # Try to download from B2 first (in CI), fall back to local cache
    try:
        from .b2_utils import download_file_from_b2

        b2_key = "figures/manifest.json"
        log(f"Downloading figure manifest from B2: {b2_key}")

        if download_file_from_b2(b2_key, str(manifest_cache)):
            log(f"Figure manifest downloaded to {manifest_cache}")

    except ImportError:
        log("B2 utils not available, using local manifest cache if present")
    except Exception as e:
        log(f"Failed to download figure manifest from B2: {e}")

    # Load from local cache if it exists
    if manifest_cache.exists():
        try:
            with open(manifest_cache) as f:
                data = json.load(f)
                manifest = data.get("papers", {})
                log(
                    f"Loaded figure manifest: {len(manifest)} papers with translated figures"
                )
        except Exception as e:
            log(f"Failed to load figure manifest: {e}")

    return manifest


def enrich_items_with_figures(
    items: List[Dict[str, Any]], figure_manifest: Dict[str, Any]
) -> None:
    """
    Enrich translation items with figure translation data.

    Sets:
    - `_has_translated_figures`: bool - whether paper has translated figures
    - `_translated_figures`: list - figure info dicts with number and url

    Args:
        items: List of translation items (modified in place)
        figure_manifest: Dict mapping paper_id to figure info
    """
    for item in items:
        paper_id = item.get("id", "")
        if paper_id in figure_manifest:
            item["_has_translated_figures"] = True
            item["_translated_figures"] = figure_manifest[paper_id].get("figures", [])
        else:
            item["_has_translated_figures"] = False
            item["_translated_figures"] = []


def load_translated() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    flagged_count = 0
    missing_body: List[Dict[str, Any]] = []

    # Check for bypass file first, but only use if explicitly enabled
    bypass_file = os.path.join("data", "translated_bypass.json")
    use_bypass = os.environ.get("USE_TRANSLATED_BYPASS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if os.path.exists(bypass_file):
        if use_bypass:
            log("Using bypassed translations (USE_TRANSLATED_BYPASS=1)")
            return read_json(bypass_file)
        else:
            log(
                "Bypass file present but ignored; set USE_TRANSLATED_BYPASS=1 to enable"
            )

    for path in sorted(glob.glob(os.path.join("data", "translated", "*.json"))):
        item = read_json(path)

        # Skip items flagged by QA filter
        qa_status = item.get("_qa_status", "pass")
        if qa_status != "pass":
            flagged_count += 1
            log(
                f"Skipping flagged translation: {item.get('id', 'unknown')} ({qa_status})"
            )
            continue

        if not has_full_body_content(item):
            missing_body.append(
                {
                    "id": item.get("id"),
                    "reason": item.get("_full_body_reason", "missing_full_text"),
                    "pdf_url": item.get("pdf_url"),
                    "source_url": item.get("source_url"),
                    "body_paragraphs": item.get("_body_paragraphs", 0),
                    "path": path,
                }
            )
            continue

        items.append(item)

    if flagged_count > 0:
        log(f"Skipped {flagged_count} flagged translations")

    report_path = os.path.join("reports", "missing_full_body.json")
    if missing_body:
        log(f"Skipped {len(missing_body)} translations without full-text body")
        ensure_dir(os.path.dirname(report_path))
        write_json(report_path, missing_body)
    else:
        if os.path.exists(report_path):
            os.remove(report_path)

    return items


def collect_categories(items: List[Dict[str, Any]], min_count: int = 10) -> List[tuple]:
    """Collect unique categories from papers, filtered by minimum count."""
    from collections import Counter

    category_counts: Counter = Counter()
    for item in items:
        subjects = item.get("subjects_en") or item.get("subjects") or []
        for subject in subjects:
            if subject and subject.strip():
                category_counts[subject.strip()] += 1

    # Filter to categories with at least min_count papers, sort by count desc
    categories = [
        (name, count)
        for name, count in category_counts.most_common()
        if count >= min_count
    ]
    return categories


def generate_figure_manifest(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate a manifest of all papers with figures for later processing.

    This scans all items for figure metadata (added by add_figure_metadata)
    and creates a queryable manifest for figure extraction pipeline.

    Args:
        items: List of translation items

    Returns:
        Manifest dict with papers that have figures
    """
    from datetime import datetime

    papers_with_figures: List[Dict[str, Any]] = []
    total_figures = 0
    total_tables = 0

    for item in items:
        figures = item.get("_figures", [])
        figure_count = item.get("_figure_count", 0)
        table_count = item.get("_table_count", 0)

        if figures:
            papers_with_figures.append(
                {
                    "id": item.get("id"),
                    "figure_count": figure_count,
                    "table_count": table_count,
                    "figures": figures,
                }
            )
            total_figures += figure_count
            total_tables += table_count

    # Sort by total figure/table count (most first)
    papers_with_figures.sort(key=lambda p: -(p["figure_count"] + p["table_count"]))

    manifest = {
        "generated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_papers_scanned": len(items),
        "total_papers_with_figures": len(papers_with_figures),
        "total_figures": total_figures,
        "total_tables": total_tables,
        "papers": papers_with_figures,
    }

    return manifest


def render_site(items: List[Dict[str, Any]]) -> None:
    from .format_translation import format_translation_to_markdown

    env = Environment(
        loader=FileSystemLoader(os.path.join("src", "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )

    # Add markdown filter
    try:
        import markdown

        def markdown_filter(text):
            return markdown.markdown(text, extensions=["extra", "codehilite"])

        env.filters["markdown"] = markdown_filter
    except ImportError:
        # Fallback: wrap paragraphs and line breaks for valid HTML
        def simple_markdown(text: str) -> str:
            if not text:
                return ""
            paragraphs = [p.strip() for p in str(text).split("\n\n")]
            html = "".join(
                "<p>{}</p>".format(p.replace("\n", "<br>")) for p in paragraphs if p
            )
            return html

        env.filters["markdown"] = simple_markdown

    base_out = "site"
    ensure_dir(base_out)

    # Copy assets
    assets_src = "assets"
    assets_dst = os.path.join(base_out, "assets")
    if os.path.exists(assets_dst):
        shutil.rmtree(assets_dst)
    if os.path.exists(assets_src):
        shutil.copytree(assets_src, assets_dst)

    # Copy admin templates (backfill dashboard, etc.)
    admin_templates_src = os.path.join("templates", "admin")
    admin_dst = os.path.join(base_out, "admin")
    if os.path.exists(admin_dst):
        shutil.rmtree(admin_dst)
    if os.path.exists(admin_templates_src):
        shutil.copytree(admin_templates_src, admin_dst)
        log(f"Copied admin templates → {admin_dst}")

    # Load figure manifest and enrich items with translated figure data
    figure_translation_manifest = load_figure_manifest()
    enrich_items_with_figures(items, figure_translation_manifest)
    figures_with_translations = sum(1 for it in items if it.get("_has_translated_figures"))
    log(f"Papers with translated figures: {figures_with_translations}/{len(items)}")

    build_version = int(time.time())

    # Collect categories for dynamic filter (min 10 papers)
    categories = collect_categories(items, min_count=10)
    log(f"Found {len(categories)} categories with 10+ papers")

    # Generate figure manifest for future extraction pipeline
    figure_manifest = generate_figure_manifest(items)
    figure_manifest_path = os.path.join("reports", "figure_manifest.json")
    ensure_dir(os.path.dirname(figure_manifest_path))
    write_json(figure_manifest_path, figure_manifest)
    log(
        f"Figure manifest: {figure_manifest['total_papers_with_figures']} papers with "
        f"{figure_manifest['total_figures']} figures, {figure_manifest['total_tables']} tables"
    )

    # Index page
    tmpl_index = env.get_template("index.html")
    html_index = tmpl_index.render(
        items=items, root=".", build_version=build_version, categories=categories
    )
    write_text(os.path.join(base_out, "index.html"), html_index)

    # Monitor page
    tmpl_monitor = env.get_template("monitor.html")
    html_monitor = tmpl_monitor.render(root=".", build_version=build_version)
    write_text(os.path.join(base_out, "monitor.html"), html_monitor)

    # Donations page
    try:
        tmpl_donations = env.get_template("donations.html")
    except TemplateNotFound:
        tmpl_donations = None
    if tmpl_donations is not None:
        html_donations = tmpl_donations.render(root=".", build_version=build_version)
        write_text(os.path.join(base_out, "donation.html"), html_donations)

    # Item pages
    tmpl_item = env.get_template("item.html")
    site_base = "https://chinarxiv.org"
    for it in items:
        out_dir = os.path.join(base_out, "items", it["id"])
        ensure_dir(out_dir)

        # Compute whether we have meaningful full text content.
        has_full_text = False
        body_md = it.get("body_md")
        if isinstance(body_md, str) and body_md.strip():
            # Consider content meaningful if there is non-heading text beyond trivial length.
            lines = body_md.splitlines()
            non_heading = [ln for ln in lines if not ln.strip().startswith("#")]
            non_heading_text = "\n".join(non_heading).strip()
            title_text = (it.get("title_en") or "").strip()
            # If the only content is a heading matching the title, treat as not meaningful.
            heading_only = (
                len([ln for ln in lines if ln.strip().startswith("#")]) >= 1
                and len(non_heading_text) == 0
            )
            if non_heading_text and len(non_heading_text) > 100 or not heading_only and len(body_md.strip()) > 200:
                has_full_text = True
        # Fallback: treat body_en arrays with sufficient content as full text
        if not has_full_text:
            body_en = it.get("body_en")
            if isinstance(body_en, list) and any((p or "").strip() for p in body_en):
                long_para = any(len((p or "").strip()) > 100 for p in body_en)
                enough_paras = sum(1 for p in body_en if (p or "").strip()) >= 2
                if long_para or enough_paras:
                    has_full_text = True

        it["_has_full_text"] = has_full_text

        # Choose best-available body markdown for preview only if meaningful
        if has_full_text:
            if body_md:
                it["formatted_body_md"] = body_md
            elif it.get("body_en"):
                it["formatted_body_md"] = format_translation_to_markdown(it)

        # Page metadata (arXiv-style polish): use absolute canonical
        title_text = it.get("title_en") or ""
        canonical_abs = f"{site_base}/items/{it['id']}/"
        html = tmpl_item.render(
            item=it,
            root="../..",
            build_version=build_version,
            title=f"{title_text} — ChinaXiv {it['id']}",
            canonical_url=canonical_abs,
            og_title=title_text,
            og_description=(it.get("abstract_en") or "")[:200],
            og_url=canonical_abs,
        )
        write_text(os.path.join(out_dir, "index.html"), html)
        # Markdown export (prefer formatted body/abstract if present)
        abstract_md = it.get("abstract_md") or (it.get("abstract_en") or "")
        if it.get("body_md"):
            full_body_md = it["body_md"]
        elif it.get("body_en"):
            # fallback: derive from heuristics
            full_body_md = format_translation_to_markdown(it)
        else:
            full_body_md = ""

        md_parts = [
            f"# {it.get('title_en') or ''}",
            f"**Authors:** {', '.join(it.get('creators') or [])}",
            f"**Date:** {it.get('date') or ''}",
            f"## Abstract\n\n{abstract_md}",
        ]
        if full_body_md:
            md_parts.append("## Full Text\n")
            md_parts.append(full_body_md)
        md_parts.append(
            "\n_Source: ChinaXiv — Machine translation. Verify with original._"
        )
        md = "\n\n".join(md_parts) + "\n"
        write_text(os.path.join(out_dir, f"{it['id']}.md"), md)

        # Optional arXiv-style alias: /abs/<id>/ in addition to /items/<id>/
        abs_dir = os.path.join(base_out, "abs", it["id"])
        ensure_dir(abs_dir)
        write_text(os.path.join(abs_dir, "index.html"), html)

    # Generate sitemap including all item and alias pages
    try:
        from datetime import datetime

        lastmod = datetime.utcnow().strftime("%Y-%m-%d")
        urls: List[str] = []
        # Static top-level pages that currently exist (only include files we actually generated)
        for rel_path in ("donation.html", "monitor.html"):
            if os.path.exists(os.path.join(base_out, rel_path)):
                urls.append(f"{site_base}/{rel_path}")
        # Item pages and /abs aliases
        for it in items:
            pid = it.get("id")
            if not pid:
                continue
            urls.append(f"{site_base}/items/{pid}/")
            urls.append(f"{site_base}/abs/{pid}/")

        def url_entry(u: str, priority: str = "0.5", changefreq: str = "weekly") -> str:
            return (
                "  <url>\n"
                f"    <loc>{u}</loc>\n"
                f"    <lastmod>{lastmod}</lastmod>\n"
                f"    <changefreq>{changefreq}</changefreq>\n"
                f"    <priority>{priority}</priority>\n"
                "  </url>\n"
            )

        sitemap_xml = [
            '<?xml version="1.0" encoding="UTF-8"?>\n',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n',
        ]
        # Home gets higher priority
        sitemap_xml.append(
            url_entry(f"{site_base}/", priority="1.0", changefreq="daily")
        )
        # Add the rest (skip duplicate home which we already added)
        for u in urls:
            if u == f"{site_base}/":
                continue
            sitemap_xml.append(url_entry(u))
        sitemap_xml.append("</urlset>\n")
        write_text(os.path.join(base_out, "sitemap.xml"), "".join(sitemap_xml))
    except Exception as e:
        log(f"Failed to generate sitemap: {e}")


def run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Render static site from translated records."
    )
    parser.parse_args()
    items = load_translated()
    render_site(items)
    log(f"Rendered site with {len(items)} items → site/")


if __name__ == "__main__":
    run_cli()
