"""
Environment variable utilities for resolving shell/.env mismatches.
"""

import os
from typing import Dict, List
from .config import load_dotenv
from .logging_utils import log


def detect_env_mismatches(
    keys: List[str], env_file: str = ".env"
) -> Dict[str, Dict[str, str]]:
    """
    Detect mismatches between shell environment variables and .env file.

    Args:
        keys: List of environment variable keys to check
        env_file: Path to .env file

    Returns:
        Dictionary with mismatch details for each key
    """
    mismatches = {}

    # Load .env file into a temporary dict
    # Use try/except instead of exists() check to avoid TOCTOU race condition
    env_file_vars = {}
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                env_file_vars[k] = v
    except FileNotFoundError:
        pass  # File doesn't exist, that's OK
    except PermissionError as e:
        log(f"Cannot read {env_file}: {e}")

    for key in keys:
        shell_value = os.environ.get(key)
        file_value = env_file_vars.get(key)

        if shell_value != file_value:
            mismatches[key] = {
                "shell": shell_value,
                "file": file_value,
                "shell_set": shell_value is not None,
                "file_set": file_value is not None,
            }

    return mismatches


def resolve_env_mismatches(
    keys: List[str], prefer_file: bool = False, env_file: str = ".env"
) -> Dict[str, str]:
    """
    Resolve environment variable mismatches by choosing a source.

    Args:
        keys: List of environment variable keys to resolve
        prefer_file: If True, prefer .env file values over shell values.
            Default is False so that explicit environment variables always win.
        env_file: Path to .env file

    Returns:
        Dictionary of resolved values
    """
    mismatches = detect_env_mismatches(keys, env_file)
    resolved = {}

    for key in keys:
        if key in mismatches:
            mismatch = mismatches[key]

            # Always prefer an explicitly-set shell value
            if mismatch["shell_set"] and not prefer_file:
                resolved[key] = mismatch["shell"]
                file_status = "(set)" if mismatch["file_set"] else "(unset)"
                log(
                    f"Resolved {key} mismatch: using shell value (ignoring .env file {file_status})"
                )
            elif prefer_file and mismatch["file_set"]:
                # Prefer .env file value (opt-in)
                resolved[key] = mismatch["file"]
                # Update shell environment to match only when preferring file
                os.environ[key] = mismatch["file"]
                shell_status = "(set)" if mismatch["shell_set"] else "(unset)"
                log(
                    f"Resolved {key} mismatch: using .env value (was: shell {shell_status})"
                )
            elif mismatch["shell_set"]:
                # Shell set (no mismatch scenario)
                resolved[key] = mismatch["shell"]
            elif mismatch["file_set"]:
                # Shell empty, use .env file and export to environment
                resolved[key] = mismatch["file"]
                os.environ[key] = mismatch["file"]
                log(f"Resolved {key}: loaded from .env into shell (shell was empty)")
            else:
                # Neither set
                resolved[key] = None
                log(f"Warning: {key} not set in shell or .env file")
        else:
            # No mismatch, use current value
            resolved[key] = os.environ.get(key)

    return resolved


def ensure_env_consistency(
    keys: List[str], env_file: str = ".env", *, prefer_file: bool = False
) -> None:
    """
    Ensure environment variables are consistent between shell and .env file.

    This function:
    1. Detects mismatches
    2. Resolves them by preferring explicit environment variables by default
    3. Only loads values from .env when the shell is missing a value
    4. Logs the resolution process

    Args:
        keys: List of environment variable keys to check
        env_file: Path to .env file
    """
    mismatches = detect_env_mismatches(keys, env_file)

    if not mismatches:
        log("Environment variables are consistent between shell and .env file")
        return

    log(f"Found {len(mismatches)} environment variable mismatches:")
    for key, mismatch in mismatches.items():
        shell_status = "(set)" if mismatch["shell_set"] else "(unset)"
        file_status = "(set)" if mismatch["file_set"] else "(unset)"
        log(f"  {key}: shell {shell_status}, file {file_status}")

    # Resolve by preferring selected source (shell by default)
    resolve_env_mismatches(keys, prefer_file=prefer_file, env_file=env_file)

    log(f"Resolved {len(mismatches)} environment variable mismatches")


def get_api_key(key_name: str, env_file: str = ".env") -> str:
    """
    Get an API key with automatic mismatch resolution.

    Args:
        key_name: Name of the API key environment variable
        env_file: Path to .env file

    Returns:
        API key value

    Raises:
        RuntimeError: If API key not found after resolution
    """
    # Always prefer explicit environment variables (e.g., GitHub Secrets)
    ensure_env_consistency([key_name], env_file, prefer_file=False)

    # Load .env without overriding existing env; in CI this preserves secrets
    load_dotenv(env_file, override=False)

    key_value = os.environ.get(key_name)
    if not key_value:
        raise RuntimeError(f"{key_name} not found in shell or .env file")

    return key_value


def validate_api_key(
    key_name: str, test_url: str = "https://openrouter.ai/api/v1/models"
) -> bool:
    """
    Validate an API key by making a test request.

    Args:
        key_name: Name of the API key environment variable
        test_url: URL to test the API key against

    Returns:
        True if API key is valid, False otherwise
    """
    try:
        import requests

        key_value = get_api_key(key_name)

        headers = {
            "Authorization": f"Bearer {key_value}",
            "Content-Type": "application/json",
        }

        resp = requests.get(test_url, headers=headers, timeout=10)
        return resp.status_code == 200

    except (requests.RequestException, RuntimeError) as e:
        log(f"API key validation failed: {e}")
        return False
