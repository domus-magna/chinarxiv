"""
Translation module for ChinaXiv English translation.

This module provides the main translation entry points using synthesis mode,
which produces readable academic prose output.
"""

from __future__ import annotations

import argparse
import glob
import os

from .db_utils import get_paper_for_translation, save_translation_result
from .file_service import read_json, write_json
from .pdf_pipeline import process_paper
from .qa_filter import SynthesisQAFilter
from .services.translation_service import TranslationService


def translate_paper(
    paper_id: str,
    dry_run: bool = False,
) -> str:
    """
    Translate a single paper by ID using synthesis mode.

    Args:
        paper_id: Paper identifier
        dry_run: If True, skip actual translation

    Returns:
        Paper ID on success (translation saved to database and local file)
    """
    return translate_paper_synthesis(paper_id, dry_run=dry_run)


def translate_paper_synthesis(
    paper_id: str,
    dry_run: bool = False,
    db_conn=None,
) -> str:
    """
    Translate a paper using synthesis mode for readable output.

    This mode:
    - Filters watermarks and PDF artifacts
    - Merges fragmented lines into proper paragraphs
    - Produces flowing, readable academic English
    - Uses section-aware chunking

    Data source priority:
    1. Database (_cn columns or _en columns if pending)
    2. Local files (data/selected.json, data/records/*.json) - fallback

    Args:
        paper_id: Paper identifier
        dry_run: If True, skip actual translation
        db_conn: Optional database connection for reuse

    Returns:
        Paper ID (translation saved to database)
    """
    service = TranslationService()

    # Primary: Load from database
    rec = None
    try:
        rec = get_paper_for_translation(paper_id, conn=db_conn)
        if rec:
            print(f"Loaded paper {paper_id} from database")
    except Exception as e:
        print(f"Database lookup failed: {e}, falling back to local files")

    # Fallback: Load from local files (migration period)
    if not rec:
        # Load selected records (if file exists)
        selected_path = os.path.join("data", "selected.json")
        selected = read_json(selected_path) if os.path.exists(selected_path) else []

        # Find the record
        rec = next((r for r in selected if r["id"] == paper_id), None)

        if not rec:
            records_dir = os.path.join("data", "records")
            rec_files = sorted(glob.glob(os.path.join(records_dir, "*.json")), reverse=True)
            for rf in rec_files:
                try:
                    records = read_json(rf)
                except Exception:
                    continue
                rec = next((r for r in records if r.get("id") == paper_id), None)
                if rec:
                    break

    if not rec:
        raise ValueError(f"Paper {paper_id} not found")

    # License gate (V1): if derivatives are disallowed, translate title+abstract only.
    skip_full_text = False
    license_meta = rec.get("license") or {}
    if license_meta.get("derivatives_allowed") is False:
        skip_full_text = True
        print(
            f"License blocks derivatives for {paper_id}; "
            "translating title/abstract only."
        )

    if not skip_full_text:
        # Check for existing PDF in site directory first
        site_pdf_path = f"site/items/{paper_id}/{paper_id}.pdf"
        data_pdf_path = f"data/pdfs/{paper_id}.pdf"

        pdf_path = None
        if os.path.exists(site_pdf_path):
            pdf_path = site_pdf_path
            print(f"Using existing PDF: {site_pdf_path}")
        elif os.path.exists(data_pdf_path):
            pdf_path = data_pdf_path
            print(f"Using existing PDF: {data_pdf_path}")
        elif rec.get("pdf_url"):
            # Download PDF
            try:
                process_result = process_paper(paper_id, rec["pdf_url"])
                if process_result:
                    pdf_path = process_result.get("pdf_path")
            except Exception as e:
                print(f"Warning: PDF processing failed: {e}")

        if pdf_path:
            rec["files"] = {"pdf_path": pdf_path}

    # Translate using synthesis mode
    translation = service.translate_record_synthesis(rec, dry_run=dry_run)

    # Run QA
    qa_filter = SynthesisQAFilter()
    qa_result = qa_filter.check_synthesis_translation(translation)

    translation["_qa_status"] = qa_result.status.value
    translation["_qa_score"] = qa_result.score
    translation["_qa_issues"] = qa_result.issues
    translation["_qa_chinese_ratio"] = qa_result.chinese_ratio

    # Save to database (primary) and local file (backup)
    if not dry_run:
        # Backup: Save to local file first (useful for debugging even if DB save fails).
        #
        # IMPORTANT:
        # - Only QA-pass translations should land in data/translated (uploaded to validated/).
        # - QA-flagged translations should land in data/flagged (uploaded to flagged/).
        out_dir = "data/translated" if qa_result.status.value == "pass" else "data/flagged"
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{paper_id}.json")
        write_json(out_path, translation)

        # Primary: Save to database when available.
        # In CI/orchestrator runs, the database is the source of truth; if DB
        # save fails we want the stage to fail (so we don't spend money and
        # then mark text complete without persisting).
        database_url_set = bool(os.environ.get("DATABASE_URL"))
        if database_url_set or db_conn is not None:
            saved = save_translation_result(paper_id, translation, conn=db_conn)
            if not saved:
                raise RuntimeError("Database save returned False (paper not found?)")
            print(f"Saved translation to database for {paper_id}")
        else:
            # Allow local-only translation flows (e.g., smoke runs) to proceed.
            # These rely on the JSON files under data/translated/.
            print("DATABASE_URL not set; skipping database save.")

    print(
        f"Synthesis translation complete: QA={qa_result.status.value}, score={qa_result.score:.2f}"
    )

    return paper_id


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Translate ChinaXiv papers")
    parser.add_argument("paper_id", help="Paper ID to translate")
    parser.add_argument(
        "--dry-run", action="store_true", help="Dry run (no actual translation)"
    )

    args = parser.parse_args()

    try:
        result_path = translate_paper(args.paper_id, dry_run=args.dry_run)
        print(f"Translation saved to: {result_path}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
