"""
Backward compatibility module for utils.py.

This module re-exports functions from the new modular structure
to maintain backward compatibility with existing imports.
"""

# Configuration
from .config import get_config, load_dotenv, getenv_bool, get_proxies, load_yaml

# HTTP client
from .http_client import http_get, openrouter_headers, HttpError

# File service
from .file_service import (
    ensure_dir,
    write_json,
    read_json,
    read_text,
    write_text,
    save_raw_xml,
    read_seen,
    write_seen,
    sanitize_filename,
)

# Cost tracking
from .cost_tracker import compute_cost, append_cost_log, now_iso

# Token utilities
from .token_utils import estimate_tokens, chunk_paragraphs

# Logging
from .logging_utils import log

# Data utilities
from .data_utils import utc_date_range_str, stable_id_from_oai, has_full_body_content

# Constants
DEFAULT_TIMEOUT = (10, 60)  # connect, read
USER_AGENT = "chinarxiv/1.0 (+https://github.com/alexanderhuth/chinarxiv)"

# Re-export everything for backward compatibility
__all__ = [
    # Configuration
    "get_config",
    "load_dotenv",
    "getenv_bool",
    "get_proxies",
    "load_yaml",
    # HTTP client
    "http_get",
    "openrouter_headers",
    "HttpError",
    # File service
    "ensure_dir",
    "write_json",
    "read_json",
    "read_text",
    "write_text",
    "save_raw_xml",
    "read_seen",
    "write_seen",
    "sanitize_filename",
    # Cost tracking
    "compute_cost",
    "append_cost_log",
    "now_iso",
    # Token utilities
    "estimate_tokens",
    "chunk_paragraphs",
    # Logging
    "log",
    # Data utilities
    "utc_date_range_str",
    "stable_id_from_oai",
    "has_full_body_content",
    # Constants
    "DEFAULT_TIMEOUT",
    "USER_AGENT",
]
