"""
Configuration management for ChinaXiv English translation.
"""

from __future__ import annotations

import os
from typing import Optional

import yaml


def load_yaml(path: str) -> dict:
    """Load YAML configuration file."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Global configuration cache
_CONFIG_CACHE: Optional[dict] = None
_CONFIG_MTIME: Optional[float] = None
_DOTENV_LOADED: bool = (
    False  # retained for backward compatibility; no longer used to short-circuit loads
)


def get_config(path: str = os.path.join("src", "config.yaml")) -> dict:
    """
    Get configuration with caching.

    Args:
        path: Path to config file

    Returns:
        Configuration dictionary
    """
    global _CONFIG_CACHE, _CONFIG_MTIME
    try:
        mtime = os.path.getmtime(path)
    except FileNotFoundError:
        return {}

    if _CONFIG_CACHE is not None and mtime == _CONFIG_MTIME:
        return _CONFIG_CACHE

    cfg = load_yaml(path)
    _CONFIG_CACHE = cfg
    _CONFIG_MTIME = mtime
    return cfg


def load_dotenv(path: str = ".env", *, override: bool = False) -> None:
    """
    Minimal .env loader: KEY=VALUE lines, ignores comments and blanks.

    If override=False, existing environment variables are not overwritten.

    Args:
        path: Path to .env file
        override: Whether to override existing environment variables
    """
    # Always attempt to load the specified .env file.
    # Respect override semantics per-key: do not overwrite existing env unless override=True.
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if override or (k not in os.environ):
                os.environ[k] = v


def getenv_bool(key: str, default: bool = False) -> bool:
    """
    Get boolean environment variable.

    Args:
        key: Environment variable name
        default: Default value if not set

    Returns:
        Boolean value
    """
    v = os.getenv(key)
    if v is None:
        return default
    return v.lower() in {"1", "true", "yes", "on"}


def get_proxies() -> tuple[Optional[dict], str]:
    """
    Get proxy configuration and its source.

    Returns (proxies, source) where source is one of: 'env', 'config', 'none'.
    Behavior:
    - If env proxies are set (HTTP(S)_PROXY or SOCKS5_PROXY), return ('env') and let
      requests use trust_env (do NOT pass proxies= to preserve NO_PROXY semantics).
    - If config.yaml has proxy.enabled: true, return ('config') with explicit proxies
      to override env and NO_PROXY.
    - Otherwise return (None, 'none').
    """
    load_dotenv()

    # Environment variables first
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    socks_proxy = os.getenv("SOCKS5_PROXY") or os.getenv("socks5_proxy")

    if http_proxy or https_proxy or socks_proxy:
        # Prefer SOCKS if provided; recommend socks5h for DNS over proxy
        if socks_proxy:
            return ({"http": socks_proxy, "https": socks_proxy}, "env")
        proxies = {}
        if http_proxy:
            proxies["http"] = http_proxy
        if https_proxy:
            proxies["https"] = https_proxy
        return (proxies if proxies else None, "env")

    # Config fallback
    try:
        cfg = get_config()
        proxy_cfg = cfg.get("proxy", {})
        if proxy_cfg and proxy_cfg.get("enabled"):
            return (
                {
                    "http": proxy_cfg.get("http"),
                    "https": proxy_cfg.get("https"),
                },
                "config",
            )
    except Exception:
        pass

    return (None, "none")
