"""
Model comparison experiment for translation quality.

Compares multiple models on the same paper to evaluate quality vs cost tradeoffs.
"""

import os
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from .services.translation_service import TranslationService
from .body_extract import extract_from_pdf_synthesis
from .file_service import read_json, write_json, ensure_dir
from .config import get_config

# Models to compare
COMPARISON_MODELS = {
    "gpt-5.1": {
        "slug": "openai/gpt-5.1",
        "input_per_m": 1.25,
        "output_per_m": 10.00,
        "baseline": True,
    },
    "deepseek-v3.2": {
        "slug": "deepseek/deepseek-v3.2-exp",
        "input_per_m": 0.22,
        "output_per_m": 0.33,
    },
    "grok-4.1-fast": {
        "slug": "x-ai/grok-4.1-fast",
        "input_per_m": 0.00,
        "output_per_m": 0.00,
    },
    "minimax-m2": {
        "slug": "minimax/minimax-m2",
        "input_per_m": 0.24,
        "output_per_m": 0.96,
    },
    "glm-4.6": {
        "slug": "z-ai/glm-4.6",
        "input_per_m": 0.40,
        "output_per_m": 1.75,
    },
    "kimi-k2-thinking": {
        "slug": "moonshotai/kimi-k2-thinking",
        "input_per_m": 0.45,
        "output_per_m": 2.35,
    },
    "gemini-2.5-flash-sept": {
        "slug": "google/gemini-2.5-flash-preview-09-2025",
        "input_per_m": 0.30,
        "output_per_m": 2.50,
    },
    "gpt-oss-120b": {
        "slug": "openai/gpt-oss-120b",
        "input_per_m": 0.04,
        "output_per_m": 0.20,
    },
}


def run_comparison(
    paper_id: str,
    models: Optional[List[str]] = None,
    output_dir: str = "data/comparison",
) -> Dict[str, Any]:
    """
    Run translation comparison across multiple models.

    Args:
        paper_id: Paper ID to translate
        models: List of model keys to test (default: all)
        output_dir: Directory for comparison outputs

    Returns:
        Comparison results dict
    """
    if models is None:
        models = list(COMPARISON_MODELS.keys())

    ensure_dir(output_dir)

    # Find paper and PDF
    pdf_path = None
    for path in [
        f"site/items/{paper_id}/{paper_id}.pdf",
        f"data/pdfs/{paper_id}.pdf",
    ]:
        if os.path.exists(path):
            pdf_path = path
            break

    if not pdf_path:
        raise ValueError(f"PDF not found for {paper_id}")

    # Extract content once (shared across all models)
    print(f"Extracting content from {pdf_path}...")
    extraction = extract_from_pdf_synthesis(pdf_path)
    if not extraction:
        raise ValueError(f"Failed to extract content from PDF")

    print(f"Extracted {extraction['stats']['merged_paragraphs']} paragraphs in {extraction['stats']['detected_sections']} sections")

    # Get paper metadata
    rec = _find_paper_record(paper_id)

    results = {
        "paper_id": paper_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extraction_stats": extraction["stats"],
        "models": {},
    }

    # Test each model
    for model_key in models:
        if model_key not in COMPARISON_MODELS:
            print(f"Unknown model: {model_key}, skipping")
            continue

        model_info = COMPARISON_MODELS[model_key]
        model_slug = model_info["slug"]

        print(f"\n{'='*60}")
        print(f"Testing: {model_key} ({model_slug})")
        print(f"{'='*60}")

        try:
            start_time = time.time()

            # Create service with this model
            service = TranslationService()

            # Translate using synthesis mode with specific model
            translation = _translate_with_model(
                service, rec, extraction, model_slug
            )

            elapsed = time.time() - start_time

            # Calculate cost estimate
            body_text = translation.get("body_md", "") or ""
            # Rough token estimate: 4 chars per token
            input_tokens = len(str(extraction)) // 4
            output_tokens = len(body_text) // 4

            cost = (
                (input_tokens / 1_000_000) * model_info["input_per_m"] +
                (output_tokens / 1_000_000) * model_info["output_per_m"]
            )

            # Save individual result
            result_path = os.path.join(
                output_dir, f"{paper_id}_{model_key}.json"
            )
            write_json(result_path, translation)

            results["models"][model_key] = {
                "slug": model_slug,
                "success": True,
                "elapsed_seconds": round(elapsed, 2),
                "body_length": len(body_text),
                "estimated_cost": round(cost, 6),
                "output_file": result_path,
            }

            print(f"  Success: {len(body_text):,} chars in {elapsed:.1f}s")
            print(f"  Est. cost: ${cost:.4f}")

        except Exception as e:
            print(f"  FAILED: {e}")
            results["models"][model_key] = {
                "slug": model_slug,
                "success": False,
                "error": str(e),
            }

    # Save comparison summary
    summary_path = os.path.join(output_dir, f"{paper_id}_comparison.json")
    write_json(summary_path, results)
    print(f"\nComparison saved to: {summary_path}")

    return results


def _find_paper_record(paper_id: str) -> Dict[str, Any]:
    """Find paper record from selected.json or records files."""
    import glob

    # Try selected.json first
    selected_path = "data/selected.json"
    if os.path.exists(selected_path):
        selected = read_json(selected_path)
        rec = next((r for r in selected if r.get("id") == paper_id), None)
        if rec:
            return rec

    # Try records files
    for rf in sorted(glob.glob("data/records/*.json"), reverse=True):
        try:
            records = read_json(rf)
            rec = next((r for r in records if r.get("id") == paper_id), None)
            if rec:
                return rec
        except Exception:
            continue

    # Return minimal record
    return {"id": paper_id}


def _translate_with_model(
    service: TranslationService,
    rec: Dict[str, Any],
    extraction: Dict[str, Any],
    model_slug: str,
) -> Dict[str, Any]:
    """Translate paper using specific model."""
    from .services.translation_service import SYNTHESIS_SYSTEM_PROMPT
    from .http_client import openrouter_headers, get_proxies
    from .tex_guard import mask_math, unmask_math
    import requests

    config = get_config()
    glossary = config.get("glossary", [])

    # Build content from extraction
    sections = extraction.get("sections", [])
    content_parts = []
    for section in sections:
        if section.get("name"):
            content_parts.append(f"\n## {section['name']}\n")
        content_parts.extend(section.get("paragraphs", []))

    source_text = "\n\n".join(content_parts)

    # Mask math
    masked, math_map = mask_math(source_text)

    # Build prompt
    glossary_text = ""
    if glossary:
        glossary_text = "\n\nGlossary:\n" + "\n".join(
            f"- {g['zh']} → {g['en']}" for g in glossary
        )

    payload = {
        "model": model_slug,
        "messages": [
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT + glossary_text},
            {"role": "user", "content": f"Translate this Chinese academic paper to English:\n\n{masked}"},
        ],
        "temperature": 0.3,
    }

    # Call OpenRouter with specific model
    # NOTE: Don't use proxy for OpenRouter - it strips auth headers
    headers = openrouter_headers()

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=(10, 600),
    )
    resp.raise_for_status()
    data = resp.json()

    translated = data["choices"][0]["message"]["content"]

    # Unmask math
    body_md = unmask_math(translated, math_map)

    # Build result
    result = {
        "id": rec.get("id", ""),
        "title_en": rec.get("title", ""),  # Could translate separately
        "abstract_en": rec.get("abstract", ""),  # Could translate separately
        "body_md": body_md,
        "_model": model_slug,
        "_synthesis_mode": True,
        "_extraction_stats": extraction.get("stats", {}),
    }

    return result


def print_comparison_summary(results: Dict[str, Any]) -> None:
    """Print formatted comparison summary."""
    print("\n" + "=" * 70)
    print(f"COMPARISON SUMMARY: {results['paper_id']}")
    print("=" * 70)

    # Sort by cost
    models = []
    for key, data in results.get("models", {}).items():
        if data.get("success"):
            models.append((key, data))

    models.sort(key=lambda x: x[1].get("estimated_cost", 999))

    print(f"\n{'Model':<25} {'Cost':>10} {'Length':>12} {'Time':>10}")
    print("-" * 60)

    baseline_length = None
    for key, data in models:
        cost = data.get("estimated_cost", 0)
        length = data.get("body_length", 0)
        elapsed = data.get("elapsed_seconds", 0)

        if COMPARISON_MODELS.get(key, {}).get("baseline"):
            baseline_length = length

        length_pct = ""
        if baseline_length and length:
            pct = (length / baseline_length) * 100
            length_pct = f" ({pct:.0f}%)"

        print(f"{key:<25} ${cost:>9.4f} {length:>8,}{length_pct:>4} {elapsed:>8.1f}s")

    # Failed models
    failed = [k for k, d in results.get("models", {}).items() if not d.get("success")]
    if failed:
        print(f"\nFailed: {', '.join(failed)}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Compare translation models")
    parser.add_argument("paper_id", help="Paper ID to test")
    parser.add_argument(
        "--models",
        nargs="+",
        help="Specific models to test (default: all)"
    )
    parser.add_argument(
        "--output-dir",
        default="data/comparison",
        help="Output directory for results"
    )

    args = parser.parse_args()

    results = run_comparison(
        args.paper_id,
        models=args.models,
        output_dir=args.output_dir,
    )

    print_comparison_summary(results)
