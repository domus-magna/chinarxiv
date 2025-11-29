"""
Parse local GitHub workflow YAML files to extract workflow_dispatch inputs
for building a simple dispatch form.
"""

from __future__ import annotations

from typing import Any, Dict
from pathlib import Path
import yaml


def load_yaml(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return data or {}


def get_dispatch_inputs(path: str | Path) -> Dict[str, Dict[str, Any]]:
    """Return inputs spec from a workflow file.

    Structure:
      { input_name: { type, description, default, required, options } }
    Missing fields will be absent.
    """
    data = load_yaml(path)
    on_block = data.get("on") or data.get("on:") or {}
    wd = on_block.get("workflow_dispatch") or {}
    inputs = wd.get("inputs") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, spec in inputs.items():
        if not isinstance(spec, dict):
            out[key] = {}
            continue
        one: Dict[str, Any] = {}
        for k in ("type", "description", "default", "required", "options"):
            if k in spec:
                one[k] = spec[k]
        out[key] = one
    return out


def describe_workflow(path: str | Path) -> str:
    """Return a simple, human-friendly description for a workflow.

    Uses lightweight heuristics:
      - Recognize common filenames and provide curated descriptions
      - Otherwise, summarize triggers (on: keys), job names, and inputs
    """
    p = Path(path)
    try:
        data = load_yaml(p)
    except Exception:
        data = {}

    name = data.get("name") or p.stem.replace("-", " ")
    on_block = data.get("on") or {}
    triggers: list[str] = sorted(on_block.keys()) if isinstance(on_block, dict) else []
    inputs = (
        (on_block.get("workflow_dispatch") or {}).get("inputs")
        if isinstance(on_block, dict)
        else None
    )
    jobs = sorted((data.get("jobs") or {}).keys())

    # Curated by filename keywords (simple and effective)
    low = p.name.lower()
    curated: dict[str, str] = {
        "harvest-gate": "Validates harvested records (schema, dedupe, and PDF accessibility) and publishes a harvest report.",
        "ocr-gate": "Evaluates OCR/text quality for PDFs and decides whether OCR is needed; outputs OCR report.",
        "translation-gate": "Runs translation QA over produced English output and flags issues; outputs translation report.",
        "render-gate": "Checks rendered site artifacts (HTML, index) for completeness and consistency.",
        "validation-gate": "Reusable validation workflow invoked by other gates; standardizes setup and artifact upload.",
        "pipeline-orchestrator": "Triggers and waits for each pipeline stage (harvest → OCR → translation → render), failing on errors.",
        "build": "Builds and deploys the site (CI/CD) depending on the repo settings.",
        "backfill": "Runs a configurable backfill across historical records with parallelization options.",
    }
    for key, desc in curated.items():
        if key in low:
            return desc

    # Generic description
    parts: list[str] = []
    if triggers:
        parts.append(f"Triggers: {', '.join(triggers)}.")
    if jobs:
        parts.append(f"Jobs: {', '.join(jobs[:3])}{'…' if len(jobs) > 3 else ''}.")
    if inputs:
        parts.append(f"Dispatchable with inputs: {', '.join(inputs.keys())}.")
    if not parts:
        parts.append(f"Workflow '{name}'.")
    return " ".join(parts)
