from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup

from .utils import http_get, log, get_config, ensure_dir, read_json, write_json


LICENSE_PATTERNS = [
    (
        re.compile(r"cc[- ]?by[- ]?nd|attribution[- ]?no[- ]?derivatives", re.I),
        "CC BY-ND",
    ),
    (re.compile(r"cc[- ]?by[- ]?sa|attribution[- ]?sharealike", re.I), "CC BY-SA"),
    (
        re.compile(
            r"cc[- ]?by|attribution(?![- ]?no[- ]?derivatives|[- ]?sharealike)", re.I
        ),
        "CC BY",
    ),
    (re.compile(r"cc0|public\s*domain", re.I), "CC0"),
]


def parse_license_string(raw: str) -> Optional[str]:
    if not raw:
        return None
    for pat, label in LICENSE_PATTERNS:
        if pat.search(raw):
            return label
    # crude URL heuristic
    if "creativecommons.org/licenses/by/" in raw.lower():
        return "CC BY"
    if "creativecommons.org/licenses/by-sa/" in raw.lower():
        return "CC BY-SA"
    if "creativecommons.org/publicdomain/zero/" in raw.lower():
        return "CC0"
    if "creativecommons.org/licenses/by-nd/" in raw.lower():
        return "CC BY-ND"
    return None


LICENSE_CACHE_PATH = "data/license_cache.json"


def _load_cache() -> dict:
    try:
        return read_json(LICENSE_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    ensure_dir("data")
    write_json(LICENSE_CACHE_PATH, cache)


def scrape_license_from_landing(url: str) -> Optional[str]:
    # Cache check
    cache = _load_cache()
    if url in cache:
        return cache[url]
    try:
        html = http_get(url).text
    except requests.RequestException as e:
        log(f"license scrape failed: {e}")
        return None
    soup = BeautifulSoup(html, "html.parser")
    # Look for <link rel="license" href="...">
    link = soup.find("link", attrs={"rel": re.compile("license", re.I)})
    if link and link.get("href"):
        guess = parse_license_string(link.get("href") or "")
        if guess:
            return guess
    # meta name or text containing license
    metas = soup.find_all("meta")
    for m in metas:
        content = (m.get("content") or "") + " " + (m.get("name") or "")
        guess = parse_license_string(content)
        if guess:
            return guess
    # Search visible text for CC strings (last resort)
    text = soup.get_text(" ")
    guess = parse_license_string(text)
    # cache result (even None to avoid repeated scrapes)
    cache[url] = guess
    _save_cache(cache)
    return guess


def decide_derivatives_allowed(
    record: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    if cfg is None:
        cfg = get_config()
    raw = (record.get("license") or {}).get("raw") or ""
    label = parse_license_string(raw)
    if not label and record.get("source_url"):
        label = scrape_license_from_landing(record["source_url"])

    mapping = cfg.get("license_mappings", {})
    meta = mapping.get(label or "") if label else None
    if meta:
        allowed = bool(meta.get("derivatives_allowed"))
        badge = meta.get("badge")
    else:
        allowed = False
        badge = None

    record["license"] = {
        "raw": raw or (label or ""),
        "label": label,
        "derivatives_allowed": allowed,
        "badge": badge,
    }
    return record
