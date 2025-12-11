#!/usr/bin/env python3
"""
Generate English PDFs from translated papers.

Downloads translation JSON files from B2 (or uses local cache),
generates PDFs using pandoc + xelatex, and outputs to data/english_pdfs/.

Modes:
- backfill: Generate PDFs for ALL papers missing them
- incremental: Generate for papers translated in last N days (default 7)

Usage:
    # Generate PDFs for all papers missing them
    python scripts/generate_english_pdfs.py --mode backfill

    # Generate for recent papers (last 7 days)
    python scripts/generate_english_pdfs.py --mode incremental

    # Dry run (show what would be generated)
    python scripts/generate_english_pdfs.py --mode backfill --dry-run

    # Limit to specific papers
    python scripts/generate_english_pdfs.py --paper-ids chinaxiv-202201.00001 chinaxiv-202201.00002
"""
import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import boto3
    from dotenv import load_dotenv
except ImportError as e:
    print(f"ERROR: Missing dependencies: {e}")
    print("Run: pip install boto3 python-dotenv")
    sys.exit(1)

load_dotenv()

# Constants
MAX_FIGURES_PER_PDF = 15
B2_BUCKET = os.environ.get("BACKBLAZE_BUCKET", "chinaxiv")


def log(msg: str):
    """Print timestamped log message."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def get_s3_client():
    """Create S3 client for B2."""
    endpoint = os.environ.get("BACKBLAZE_S3_ENDPOINT")
    key_id = os.environ.get("BACKBLAZE_KEY_ID")
    app_key = os.environ.get("BACKBLAZE_APPLICATION_KEY")

    if not all([endpoint, key_id, app_key]):
        print("ERROR: Missing B2 credentials in .env")
        print("Required: BACKBLAZE_S3_ENDPOINT, BACKBLAZE_KEY_ID, BACKBLAZE_APPLICATION_KEY")
        sys.exit(1)

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
    )


def has_binary(name: str) -> bool:
    """Check if a binary is available in PATH."""
    return shutil.which(name) is not None


def check_pdf_tools() -> Optional[str]:
    """
    Check for required PDF generation tools.

    Returns:
        PDF engine name ('xelatex' or 'tectonic') if available, None otherwise.
    """
    if not has_binary("pandoc"):
        log("ERROR: pandoc not found. Install with: apt-get install pandoc")
        return None

    if has_binary("xelatex"):
        return "xelatex"
    elif has_binary("tectonic"):
        log("WARNING: xelatex not found, using tectonic (CJK fonts may need download)")
        return "tectonic"
    else:
        log("ERROR: No compatible LaTeX engine found (need xelatex or tectonic)")
        return None


def build_pdf_markdown(item: Dict[str, Any], body_md: str) -> str:
    """
    Build markdown with PDF-specific header and footer branding.

    Adds YAML front matter for pandoc with fancyhdr footer settings,
    first-page header with ChinaRxiv branding and paper URL.
    """
    paper_id = item.get("id", "")
    chinarxiv_url = f"https://chinarxiv.org/items/{paper_id}"
    display_url = f"chinarxiv.org/items/{paper_id}"

    yaml_header = f"""---
header-includes:
  - \\usepackage{{fontspec}}
  - \\usepackage{{xeCJK}}
  - \\setCJKmainfont{{Noto Sans CJK SC}}
  - \\usepackage{{fancyhdr}}
  - \\usepackage{{hyperref}}
  - \\usepackage{{graphicx}}
  - \\pagestyle{{fancy}}
  - \\fancyhead{{}}
  - \\fancyhead[R]{{\\includegraphics[height=0.6cm]{{assets/logo-wordmark.png}}}}
  - \\fancyfoot{{}}
  - \\fancyfoot[L]{{\\small \\href{{{chinarxiv_url}}}{{{display_url}}}}}
  - \\fancyfoot[R]{{\\small Machine Translation}}
  - \\renewcommand{{\\headrulewidth}}{{0pt}}
  - \\renewcommand{{\\footrulewidth}}{{0.4pt}}
---

\\begin{{center}}
\\rule{{\\textwidth}}{{0.5pt}}

{{\\small AI translation · View original \\& related papers at \\href{{{chinarxiv_url}}}{{{display_url}}}}}

\\rule{{\\textwidth}}{{0.5pt}}
\\end{{center}}

\\vspace{{1em}}

"""
    return yaml_header + body_md


def inject_figures_into_markdown(
    body_md: str,
    figures: List[Dict[str, Any]],
    max_figures: int | None = None,
) -> str:
    """
    Inject translated figures into markdown body.

    Strategy:
    1. Optionally cap figures (for PDF builds)
    2. Replace [FIGURE:N] markers with ![Figure N](url)
    3. Append unplaced figures at the end
    4. If no figures available, replace markers with placeholder text
    """
    # Strip table markers (we don't translate tables)
    body_md = re.sub(r"\[TABLE:\d+[A-Za-z]?\]", "", body_md)

    if not figures:
        # Replace figure markers with placeholder (not strip) - so user knows figure was there
        body_md = re.sub(
            r"\[FIGURE:(\d+[A-Za-z]?)\]",
            r"[Figure \1: see original paper]",
            body_md
        )
        return body_md

    # Optionally cap figures
    truncated_count = 0
    if max_figures is not None and len(figures) > max_figures:
        truncated_count = len(figures) - max_figures
        figures = figures[:max_figures]

    # Build figure lookup
    figure_urls: Dict[str, List[str]] = defaultdict(list)
    for fig in figures:
        figure_urls[str(fig["number"])].append(fig["url"])

    placed: set = set()

    def replace_marker(match: re.Match) -> str:
        num = match.group(1)
        if num in figure_urls:
            placed.add(num)
            imgs = "\n\n".join(f"![Figure {num}]({url})" for url in figure_urls[num])
            return f"\n\n{imgs}\n\n"
        return match.group(0)

    result = re.sub(r"\[FIGURE:(\d+)\]", replace_marker, body_md)

    # Append unplaced figures
    unplaced_nums = [n for n in figure_urls if n not in placed]
    if unplaced_nums:
        result += "\n\n---\n\n## Figures\n\n"
        for num in sorted(unplaced_nums, key=lambda x: int(x) if x.isdigit() else 0):
            for url in figure_urls[num]:
                result += f"![Figure {num}]({url})\n\n"

    if truncated_count > 0:
        result += f"\n\n_Note: {truncated_count} additional figures available online._\n"

    return result


def md_to_pdf(md_path: str, pdf_path: str, pdf_engine: str) -> bool:
    """
    Convert markdown to PDF using pandoc.

    Returns True on success, False on failure.
    """
    try:
        # Resource path includes: markdown's directory, assets/, and cwd
        md_parent = str(Path(md_path).parent)
        resource_path = f"{md_parent}:assets:."
        cmd = [
            "pandoc", md_path, "-o", pdf_path,
            "--pdf-engine", pdf_engine,
            f"--resource-path={resource_path}"
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr[:300] + "...") if e.stderr and len(e.stderr) > 300 else (e.stderr or "")
        log(f"  PDF generation failed: {stderr}")
        return False
    except subprocess.TimeoutExpired:
        log("  PDF generation timed out (>120s)")
        return False
    except Exception as e:
        log(f"  Unexpected error: {e}")
        return False


def get_figure_manifest(s3) -> Dict[str, Any]:
    """Download figure manifest from B2."""
    try:
        response = s3.get_object(Bucket=B2_BUCKET, Key="figures/manifest.json")
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        return {"papers": {}}


def get_existing_pdf_manifest(s3) -> Dict[str, Any]:
    """Download existing English PDF manifest from B2."""
    try:
        response = s3.get_object(Bucket=B2_BUCKET, Key="english_pdfs/manifest.json")
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        return {"papers": {}}


def load_translation(path: Path) -> Optional[Dict]:
    """Load a translation JSON file."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        log(f"  Failed to load {path}: {e}")
        return None


def generate_pdf_for_paper(
    paper: Dict[str, Any],
    figure_manifest: Dict[str, Any],
    output_dir: Path,
    pdf_engine: str,
) -> tuple[bool, int]:
    """
    Generate PDF for a single paper.

    Returns (success, figure_count) tuple.
    figure_count = number of figures embedded in the PDF (0 if none available).
    """
    paper_id = paper.get("id", "")
    if not paper_id:
        return False, 0

    # Get body markdown
    body_md = paper.get("body_md", "")
    if not body_md:
        # Fallback: derive from body_en array
        body_en = paper.get("body_en", [])
        if isinstance(body_en, list):
            body_md = "\n\n".join(body_en)

    abstract = paper.get("abstract_en", "")
    title = paper.get("title_en", "")
    creators = paper.get("creators_en", paper.get("creators", []))
    date = paper.get("date", "")

    # Get translated figures if available
    figures = []
    paper_figures = figure_manifest.get("papers", {}).get(paper_id, {})
    if paper_figures:
        for fig in paper_figures.get("figures", []):
            url = fig.get("translated_url") or fig.get("url")
            if url:
                figures.append({
                    "number": fig.get("number", ""),
                    "url": url,
                })

    # Inject figures into body (with cap for PDF), or replace markers with placeholders
    body_md = inject_figures_into_markdown(body_md, figures, max_figures=MAX_FIGURES_PER_PDF)

    # Track figure count for metadata - use capped count that actually appears in PDF
    figure_count = min(len(figures), MAX_FIGURES_PER_PDF) if figures else 0

    # Build PDF content
    pdf_parts = [
        f"# {title}",
        f"**Authors:** {', '.join(creators) if isinstance(creators, list) else str(creators)}",
        f"**Date:** {date}",
        f"## Abstract\n\n{abstract}",
    ]
    if body_md:
        pdf_parts.append("## Full Text\n")
        pdf_parts.append(body_md)

    # Add disclaimer for papers without translated figures
    if not figures:
        pdf_parts.append("\n_Note: Figure translations are in progress. See original paper for figures._")

    pdf_parts.append("\n_Source: ChinaXiv — Machine translation. Verify with original._")

    pdf_content = "\n\n".join(pdf_parts) + "\n"
    pdf_md = build_pdf_markdown(paper, pdf_content)

    # Write temp markdown and generate PDF
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{paper_id}.pdf"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(pdf_md)
        md_path = f.name

    try:
        success = md_to_pdf(md_path, str(pdf_path), pdf_engine)
        final_figure_count = figure_count

        # Fallback: retry without figures if failed and had figures
        if not success and figures:
            log(f"  Retrying {paper_id} without figures...")
            body_md_plain = paper.get("body_md", "")
            if not body_md_plain:
                body_en = paper.get("body_en", [])
                if isinstance(body_en, list):
                    body_md_plain = "\n\n".join(body_en)

            # Apply placeholder text for figures in fallback too
            body_md_plain = inject_figures_into_markdown(body_md_plain, [], max_figures=None)

            pdf_parts_fallback = [
                f"# {title}",
                f"**Authors:** {', '.join(creators) if isinstance(creators, list) else str(creators)}",
                f"**Date:** {date}",
                f"## Abstract\n\n{abstract}",
            ]
            if body_md_plain:
                pdf_parts_fallback.append("## Full Text\n")
                pdf_parts_fallback.append(body_md_plain)
            pdf_parts_fallback.append("\n_Note: Figure translations are in progress. See original paper for figures._")
            pdf_parts_fallback.append("\n_Source: ChinaXiv — Machine translation. Verify with original._")

            pdf_content_fallback = "\n\n".join(pdf_parts_fallback) + "\n"
            pdf_md_fallback = build_pdf_markdown(paper, pdf_content_fallback)

            with open(md_path, "w") as f:
                f.write(pdf_md_fallback)

            success = md_to_pdf(md_path, str(pdf_path), pdf_engine)
            # If fallback succeeded, PDF has no figures
            if success:
                final_figure_count = 0

        # Write metadata file alongside PDF for upload script
        if success:
            meta_path = output_dir / f"{paper_id}.meta.json"
            meta_data = {
                "paper_id": paper_id,
                "figure_count": final_figure_count,
                "has_figures": final_figure_count > 0,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(meta_path, "w") as f:
                json.dump(meta_data, f)

        return success, final_figure_count
    finally:
        # Clean up temp file
        with contextlib.suppress(OSError):
            os.remove(md_path)


def main():
    parser = argparse.ArgumentParser(description="Generate English PDFs from translations")
    parser.add_argument(
        "--mode",
        choices=["backfill", "incremental", "figures-update"],
        default="incremental",
        help="backfill=all missing, incremental=recent only, figures-update=regenerate PDFs that now have figures"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="For incremental mode: number of days to look back (default 7)"
    )
    parser.add_argument(
        "--paper-ids",
        nargs="+",
        help="Generate PDFs for specific paper IDs only"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be generated without generating"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/translated"),
        help="Directory containing translation JSON files (default: data/translated)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/english_pdfs"),
        help="Directory to output PDFs (default: data/english_pdfs)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("English PDF Generation")
    print("=" * 60)
    print(f"Mode: {args.mode}")
    print(f"Input: {args.input_dir}")
    print(f"Output: {args.output_dir}")
    print()

    # Check PDF tools
    pdf_engine = check_pdf_tools()
    if not pdf_engine and not args.dry_run:
        sys.exit(1)
    if pdf_engine:
        log(f"Using PDF engine: {pdf_engine}")

    # Create S3 client
    s3 = get_s3_client()

    # Get figure manifest for translated figures
    log("Downloading figure manifest from B2...")
    figure_manifest = get_figure_manifest(s3)
    figure_count = len(figure_manifest.get("papers", {}))
    log(f"Found {figure_count} papers with translated figures")

    # Get existing PDF manifest to skip already-generated PDFs
    log("Downloading existing PDF manifest from B2...")
    pdf_manifest = get_existing_pdf_manifest(s3)
    existing_pdfs = set(pdf_manifest.get("papers", {}).keys())
    log(f"Found {len(existing_pdfs)} existing PDFs in B2")

    # Find translation files
    if not args.input_dir.exists():
        log(f"ERROR: Input directory does not exist: {args.input_dir}")
        sys.exit(1)

    translation_files = list(args.input_dir.glob("*.json"))
    log(f"Found {len(translation_files)} translation files")

    # For figures-update mode, identify papers that:
    # 1. Have existing PDF WITHOUT figures (has_figures=false in manifest)
    # 2. Now have translated figures available
    papers_needing_figure_update = set()
    if args.mode == "figures-update":
        figure_papers = set(figure_manifest.get("papers", {}).keys())
        for paper_id, pdf_info in pdf_manifest.get("papers", {}).items():
            # Paper has PDF but no figures, and figures now available
            if not pdf_info.get("has_figures", False) and paper_id in figure_papers:
                papers_needing_figure_update.add(paper_id)
        log(f"Found {len(papers_needing_figure_update)} PDFs needing figure update")

    # Filter papers based on mode and arguments
    papers_to_process = []

    for tf in translation_files:
        paper_id = tf.stem

        # Filter by specific paper IDs if provided
        if args.paper_ids and paper_id not in args.paper_ids:
            continue

        # Mode-specific filtering
        if args.mode == "figures-update":
            # Only process papers that need figure update
            if paper_id not in papers_needing_figure_update:
                continue
        elif not args.paper_ids:
            # Skip if PDF already exists in B2 (for backfill/incremental)
            if paper_id in existing_pdfs:
                continue

        # For incremental mode, filter by file modification time
        if args.mode == "incremental" and not args.paper_ids:
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
            mtime = datetime.fromtimestamp(tf.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue

        papers_to_process.append(tf)

    log(f"Papers to process: {len(papers_to_process)}")
    print()

    if args.dry_run:
        print("DRY RUN - Would generate PDFs for:")
        for tf in papers_to_process[:20]:
            print(f"  - {tf.stem}")
        if len(papers_to_process) > 20:
            print(f"  ... and {len(papers_to_process) - 20} more")
        return

    if not papers_to_process:
        log("No papers to process")
        return

    # Ensure output directory exists
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Generate PDFs
    success_count = 0
    fail_count = 0

    for i, tf in enumerate(papers_to_process, 1):
        paper_id = tf.stem
        log(f"[{i}/{len(papers_to_process)}] Generating PDF for {paper_id}...")

        paper = load_translation(tf)
        if not paper:
            fail_count += 1
            continue

        success, fig_count = generate_pdf_for_paper(paper, figure_manifest, args.output_dir, pdf_engine)
        if success:
            fig_info = f"with {fig_count} figures" if fig_count > 0 else "without figures"
            log(f"  ✓ Generated {paper_id}.pdf ({fig_info})")
            success_count += 1
        else:
            log(f"  ✗ Failed {paper_id}")
            fail_count += 1

    print()
    print("=" * 60)
    print(f"Done! Generated {success_count} PDFs, {fail_count} failures")
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
