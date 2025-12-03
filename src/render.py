from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape
import time

from .utils import ensure_dir, log, read_json, write_text, write_json
from .data_utils import has_full_body_content, is_cs_ai_paper
from .make_pdf import md_to_pdf, has_binary


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


def load_translated(cs_ai_only: bool = False) -> List[Dict[str, Any]]:
    """
    Load translated papers from data/translated/.

    Args:
        cs_ai_only: If True, filter to only CS/AI papers (ML, NLP, CV, etc.)

    Returns:
        List of translation dicts that pass QA and have full body content.
    """
    items: List[Dict[str, Any]] = []
    flagged_count = 0
    missing_body: List[Dict[str, Any]] = []
    non_cs_ai_count = 0

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

        # Apply CS/AI filter if enabled
        if cs_ai_only:
            is_match, _ = is_cs_ai_paper(item)
            if not is_match:
                non_cs_ai_count += 1
                continue

        items.append(item)

    if flagged_count > 0:
        log(f"Skipped {flagged_count} flagged translations")

    if non_cs_ai_count > 0:
        log(f"Filtered out {non_cs_ai_count} non-CS/AI papers (--cs-ai-only enabled)")

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
    """Collect unique categories from papers, normalized and sorted alphabetically.

    Normalizes subject names (title case for English, preserves Chinese),
    deduplicates case-insensitively, and returns alphabetically sorted list.
    """
    from collections import Counter
    from .data_utils import normalize_subject

    # Count raw subjects first
    raw_counts: Counter = Counter()
    for item in items:
        subjects = item.get("subjects_en") or item.get("subjects") or []
        for subject in subjects:
            if subject and subject.strip():
                raw_counts[subject.strip()] += 1

    # Normalize and merge counts (case-insensitive dedup)
    normalized_counts: Dict[str, int] = {}
    normalized_labels: Dict[str, str] = {}  # lowercase key -> display label

    for raw_name, count in raw_counts.items():
        normalized = normalize_subject(raw_name)
        key = normalized.lower()  # case-insensitive key

        if key not in normalized_labels:
            normalized_labels[key] = normalized
        normalized_counts[key] = normalized_counts.get(key, 0) + count

    # Filter by min_count, sort alphabetically by normalized label
    categories = [
        (normalized_labels[key], count)
        for key, count in normalized_counts.items()
        if count >= min_count
    ]
    categories.sort(key=lambda x: x[0].lower())  # alphabetical sort

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


def render_site(items: List[Dict[str, Any]], skip_pdf: bool = False) -> None:
    """Render the static site from translated items.

    Generates HTML pages for each paper, plus index, sitemap, and auxiliary pages.

    PDF Generation:
        Each paper gets an English PDF generated via pandoc during render.
        This happens inline (after .md write, before HTML render) so that the
        `_has_english_pdf` flag is accurate when the template renders.

        Requirements:
        - pandoc (required)
        - pdflatex OR tectonic (for LaTeX → PDF conversion)

        The template conditionally shows "Download PDF (English)" link based on
        `item._has_english_pdf` which reflects actual PDF generation success.

    Args:
        items: List of translated paper dicts from load_translated()
        skip_pdf: If True, skip PDF generation (faster for testing/validation)
    """
    from .format_translation import format_translation_to_markdown

    # Hoist PDF engine detection once at start (not per-paper)
    can_generate_pdf = False
    pdf_engine: str | None = None
    if skip_pdf:
        log("PDF generation skipped (--skip-pdf)")
    elif not has_binary("pandoc"):
        log("WARNING: pandoc not found - English PDFs will not be generated")
    elif not has_binary("pdflatex") and not has_binary("tectonic"):
        log("WARNING: pdflatex/tectonic not found - PDF generation may fail")
    else:
        can_generate_pdf = True
        pdf_engine = "tectonic" if not has_binary("pdflatex") and has_binary("tectonic") else None
        if pdf_engine:
            log("Using tectonic as PDF engine (pdflatex not found)")

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

    # Clean paper directories to remove orphans from previous builds
    # This ensures each deploy is a complete, clean snapshot of validated content
    for paper_dir in ["items", "abs"]:
        paper_path = os.path.join(base_out, paper_dir)
        if os.path.exists(paper_path):
            shutil.rmtree(paper_path)
        ensure_dir(paper_path)

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
    # Build manifest base URL from environment (includes prefix for CI)
    b2_endpoint = os.environ.get("BACKBLAZE_S3_ENDPOINT", "https://s3.us-west-004.backblazeb2.com")
    b2_bucket = os.environ.get("BACKBLAZE_BUCKET", "chinaxiv")
    b2_prefix = os.environ.get("BACKBLAZE_PREFIX", "").strip("/")
    # Convert S3 endpoint to public file URL
    # e.g., https://s3.us-west-004.backblazeb2.com → https://f004.backblazeb2.com/file
    if "s3." in b2_endpoint and "backblazeb2.com" in b2_endpoint:
        # Extract region code (e.g., "us-west-004")
        match = re.search(r's3\.([^.]+)\.backblazeb2\.com', b2_endpoint)
        if match:
            region = match.group(1)
            # Public URL format: f{region_suffix}.backblazeb2.com/file/{bucket}
            region_suffix = region.split('-')[-1]  # "004" from "us-west-004"
            manifest_base_url = f"https://f{region_suffix}.backblazeb2.com/file/{b2_bucket}"
        else:
            manifest_base_url = f"https://f004.backblazeb2.com/file/{b2_bucket}"
    else:
        # Non-B2 endpoint, use as-is
        manifest_base_url = f"{b2_endpoint}/{b2_bucket}"
    # Append prefix if set
    if b2_prefix:
        manifest_base_url = f"{manifest_base_url}/{b2_prefix}"

    tmpl_monitor = env.get_template("monitor.html")
    html_monitor = tmpl_monitor.render(
        root=".", build_version=build_version, manifest_base_url=manifest_base_url
    )
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

        # Markdown export (prefer formatted body/abstract if present)
        # Must happen before HTML render so we can generate PDF and set flag
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
        md_path = os.path.join(out_dir, f"{it['id']}.md")
        write_text(md_path, md)

        # Generate PDF from markdown and set flag based on result
        pdf_path = os.path.join(out_dir, f"{it['id']}.pdf")
        if can_generate_pdf:
            success = md_to_pdf(md_path, pdf_path, pdf_engine=pdf_engine)
            it['_has_english_pdf'] = success
            if not success:
                log(f"PDF generation failed: {it['id']}")
        else:
            it['_has_english_pdf'] = False

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
    parser.add_argument(
        "--cs-ai-only",
        action="store_true",
        help="Only render CS/AI papers (machine learning, NLP, computer vision, etc.)",
    )
    parser.add_argument(
        "--skip-pdf",
        action="store_true",
        help="Skip PDF generation (faster renders for testing/validation)",
    )
    args = parser.parse_args()

    items = load_translated(cs_ai_only=args.cs_ai_only)
    render_site(items, skip_pdf=args.skip_pdf)

    filter_note = " (CS/AI only)" if args.cs_ai_only else ""
    log(f"Rendered site with {len(items)} items{filter_note} → site/")


if __name__ == "__main__":
    run_cli()
