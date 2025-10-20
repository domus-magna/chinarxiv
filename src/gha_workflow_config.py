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
