"""
Optimized HTTP client with connection pooling for ChinaXiv English translation.
"""

from __future__ import annotations

from typing import Optional, Tuple, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .config import get_proxies


DEFAULT_TIMEOUT = (10, 60)  # connect, read
USER_AGENT = "chinarxiv/1.0 (+https://github.com/domus-magna/chinarxiv)"


class HttpError(Exception):
    """HTTP-related errors."""

    pass


# Global session with connection pooling
_session = None


def get_session() -> requests.Session:
    """Get or create optimized session with connection pooling."""
    global _session
    if _session is None:
        _session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )

        # Configure adapter with connection pooling
        adapter = HTTPAdapter(
            pool_connections=10, pool_maxsize=20, max_retries=retry_strategy
        )

        _session.mount("http://", adapter)
        _session.mount("https://", adapter)

        # Set default headers
        _session.headers.update({"User-Agent": USER_AGENT})

    return _session


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=20),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(HttpError),
)
def http_get(
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
) -> requests.Response:
    """
    Make HTTP GET request with retry logic and connection pooling.

    Args:
        url: URL to request
        headers: Optional headers
        params: Optional query parameters
        timeout: Request timeout (connect, read)

    Returns:
        Response object

    Raises:
        HttpError: On request failure or non-OK status
    """
    session = get_session()

    # Update headers for this request
    request_headers = {}
    if headers:
        request_headers.update(headers)

    proxies, source = get_proxies()
    eff_timeout = (15, 90) if source != "none" else timeout

    try:
        if source == "config" and proxies:
            resp = session.get(
                url,
                headers=request_headers,
                params=params,
                timeout=eff_timeout,
                proxies=proxies,
            )
        else:
            resp = session.get(
                url, headers=request_headers, params=params, timeout=eff_timeout
            )
    except requests.RequestException as e:
        raise HttpError(str(e))

    if not resp.ok:
        raise HttpError(f"GET {url} -> {resp.status_code}")

    return resp


def openrouter_headers() -> dict:
    """
    Get headers for OpenRouter API requests with automatic env mismatch resolution.

    Returns:
        Headers dictionary

    Raises:
        RuntimeError: If OPENROUTER_API_KEY not set
    """
    from .env_utils import get_api_key

    # Get API key with automatic mismatch resolution
    key = get_api_key("OPENROUTER_API_KEY")

    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "chinarxiv",
    }


def parse_openrouter_error(resp: requests.Response) -> Dict[str, Any]:
    """
    Parse an OpenRouter error response into a structured shape.

    Returns a dict with keys:
      - status: HTTP status code
      - code: provider error code if available
      - message: human-readable error message
      - retry_after: seconds from Retry-After header (int | None)
      - retryable: whether the error is transient and should be retried
      - fallback_ok: whether trying an alternate model could help
    """
    status = resp.status_code
    code: Optional[str] = None
    message: Optional[str] = None
    try:
        data = resp.json()
    except Exception:
        data = None

    if isinstance(data, dict):
        err = data.get("error") or {}
        if isinstance(err, dict):
            code = err.get("code") or err.get("type") or code
            message = err.get("message") or data.get("message") or message
        else:
            # Some providers return an errors list
            errors = data.get("errors") or []
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict):
                    code = first.get("code") or first.get("type") or code
                    message = first.get("message") or message

    if not message:
        # Fallback to raw body text
        try:
            message = (resp.text or "").strip()
        except Exception:
            message = f"HTTP {status}"
    # Trim excessive payload
    if message and len(message) > 500:
        message = message[:500] + "…"

    # Retry-After header
    retry_after_hdr = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    try:
        retry_after = int(retry_after_hdr) if retry_after_hdr else None
    except Exception:
        retry_after = None

    # Heuristic classification
    msg_lc = (message or "").lower()
    retryable = False
    fallback_ok = True

    if status == 429 or status >= 500:
        retryable = True
    elif status == 401:
        # Auth-related; usually not fixed by retry or model fallback
        if (
            code in {"invalid_api_key", "user_not_found"}
            or "user not found" in msg_lc
            or ("invalid" in msg_lc and "key" in msg_lc)
        ) or any(w in msg_lc for w in ("insufficient", "balance", "credit")):
            fallback_ok = False
            code = code or "unauthorized"
    elif status == 402:
        # Payment required / insufficient funds
        fallback_ok = False
        code = code or "payment_required"
    elif status == 403:
        # Access forbidden; could be model-specific restriction
        fallback_ok = True
    elif status == 400:
        # Bad request — likely model slug or request shape; trying alternates may help
        fallback_ok = True

    # Known fatal codes regardless of HTTP status
    if code in {
        "invalid_api_key",
        "user_not_found",
        "insufficient_quota",
        "payment_required",
    }:
        fallback_ok = False

    return {
        "status": status,
        "code": code,
        "message": message,
        "retry_after": retry_after,
        "retryable": retryable,
        "fallback_ok": fallback_ok,
    }


def close_session():
    """Close the global session."""
    global _session
    if _session:
        _session.close()
        _session = None
