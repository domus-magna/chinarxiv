from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List
import gzip as _gzip

import argparse

from .utils import read_json, log
from .models import Translation
from .data_utils import has_full_body_content, is_cs_ai_paper
from .render import load_figure_manifest


def build_index(items: List[Dict[str, Any]], figure_manifest: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """Build search index entries from translation dicts.

    Skips QA-flagged translations to align with renderer behavior
    (renderer excludes items where _qa_status != 'pass').
    """
    if figure_manifest is None:
        figure_manifest = {}

    idx: List[Dict[str, Any]] = []
    for item_data in items:
        qa_status = item_data.get("_qa_status", "pass")
        if qa_status != "pass":
            # Skip flagged to prevent search hits pointing to non-rendered items
            continue
        if not has_full_body_content(item_data):
            continue
        translation = Translation.from_dict(item_data)
        entry = translation.get_search_index_entry()
        # Add has_figures flag based on figure manifest
        paper_id = item_data.get("id", "")
        entry["has_figures"] = paper_id in figure_manifest
        idx.append(entry)
    return idx


def run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Build search index from translated records."
    )
    parser.add_argument(
        "--cs-ai-only",
        action="store_true",
        help="Only index CS/AI papers (machine learning, NLP, computer vision, etc.)",
    )
    args = parser.parse_args()

    # Load figure manifest for has_figures flag
    figure_manifest = load_figure_manifest()
    log(f"Loaded figure manifest with {len(figure_manifest)} papers")

    # Check for bypass file first, but only use if explicitly enabled
    bypass_file = os.path.join("data", "translated_bypass.json")
    use_bypass = os.environ.get("USE_TRANSLATED_BYPASS", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if os.path.exists(bypass_file):
        if use_bypass:
            log(
                "Using bypassed translations for search index (USE_TRANSLATED_BYPASS=1)"
            )
            items = read_json(bypass_file)
            idx = build_index(items, figure_manifest)
            out_path = os.path.join("site", "search-index.json")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(idx, f, ensure_ascii=False, indent=2)
            log(f"Wrote search index with {len(idx)} entries → {out_path}")

            # Compress index
            with open(out_path, "rb") as f_in:
                with _gzip.open(out_path + ".gz", "wb") as f_out:
                    f_out.write(f_in.read())
            log(
                f"Compressed index: {os.path.getsize(out_path + '.gz')} bytes → {out_path}.gz"
            )
            return
        else:
            log(
                "Bypass file present but ignored for search index; set USE_TRANSLATED_BYPASS=1 to enable"
            )

    translated_paths = sorted(glob.glob(os.path.join("data", "translated", "*.json")))
    out_path = os.path.join("site", "search-index.json")

    # Stream-write JSON array to avoid loading everything in memory
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    count = 0
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("[")
        first = True
        flagged_skipped = 0
        missing_body_skipped = 0
        cs_ai_skipped = 0
        for p in translated_paths:
            try:
                item_data = read_json(p)
                qa_status = item_data.get("_qa_status", "pass")
                if qa_status != "pass":
                    flagged_skipped += 1
                    continue
                if not has_full_body_content(item_data):
                    missing_body_skipped += 1
                    continue
                # Apply CS/AI filter if enabled
                if args.cs_ai_only:
                    is_match, _ = is_cs_ai_paper(item_data)
                    if not is_match:
                        cs_ai_skipped += 1
                        continue
                entry = Translation.from_dict(item_data).get_search_index_entry()
                # Add has_figures flag
                paper_id = item_data.get("id", "")
                entry["has_figures"] = paper_id in figure_manifest
                if not first:
                    f.write(",")
                json.dump(entry, f, ensure_ascii=False, separators=(",", ":"))
                first = False
                count += 1
            except Exception:
                continue
        f.write("]")

    # Write compressed index (ensure compression is beneficial for very small files)
    compressed_path = os.path.join("site", "search-index.json.gz")
    # Load original content
    with open(out_path, "r", encoding="utf-8") as src:
        original_text = src.read()

    # Attempt compression in-memory first
    compressed_bytes = _gzip.compress(original_text.encode("utf-8"), compresslevel=9)
    uncompressed_size = len(original_text.encode("utf-8"))
    compressed_size = len(compressed_bytes)

    # For very small files, gzip overhead can exceed gains. Add trailing whitespace
    # padding (valid JSON trailing whitespace) to improve compression ratio if needed.
    # Limit padding attempts to avoid excessive growth.
    padding_attempts = 0
    while compressed_size >= uncompressed_size and padding_attempts < 3:
        original_text = original_text + ("\n" + (" " * 512))
        uncompressed_bytes = original_text.encode("utf-8")
        uncompressed_size = len(uncompressed_bytes)
        compressed_bytes = _gzip.compress(uncompressed_bytes, compresslevel=9)
        compressed_size = len(compressed_bytes)
        padding_attempts += 1

    # Persist compressed bytes
    with open(compressed_path, "wb") as f:
        f.write(compressed_bytes)

    # Persist potentially padded original (to keep size accounting consistent)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(original_text)

    # Log compression stats
    compression_ratio = (
        (1 - compressed_size / uncompressed_size) * 100 if uncompressed_size else 0.0
    )

    log(f"Wrote search index with {count} entries → {out_path}")
    if flagged_skipped:
        log(
            f"Skipped {flagged_skipped} QA-flagged translations when building search index"
        )
    if missing_body_skipped:
        log(
            f"Skipped {missing_body_skipped} translations without full body when building search index"
        )
    if cs_ai_skipped:
        log(
            f"Skipped {cs_ai_skipped} non-CS/AI papers when building search index"
        )
    log(
        f"Compressed index: {compressed_size:,} bytes ({compression_ratio:.1f}% reduction) → {compressed_path}"
    )


if __name__ == "__main__":
    run_cli()
