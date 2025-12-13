#!/usr/bin/env python3
"""
Environment variable diagnostic and resolution tool.

Simple, robust tool to diagnose and fix API key mismatches.
"""

import argparse
import os
import sys
import shutil
import json
from pathlib import Path
from typing import Dict, Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.env_utils import validate_api_key, get_api_key
from src.logging_utils import log


def check_env_consistency():
    """
    Check if shell environment matches .env file.

    Returns:
        dict: Mismatches found, empty if all good
    """
    mismatches = {}

    # Check if .env exists
    env_file = Path(".env")
    if not env_file.exists():
        return mismatches  # No .env file, nothing to check

    try:
        # Read .env file
        with open(env_file, "r") as f:
            env_vars = {}
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    except Exception as e:
        log(f"❌ Could not read .env file: {e}")
        return mismatches

    # Check API keys
    for key in ["OPENROUTER_API_KEY"]:
        if key in env_vars:
            env_value = env_vars[key]
            shell_value = os.getenv(key)

            if shell_value != env_value:
                mismatches[key] = {"env_value": env_value, "shell_value": shell_value}

    return mismatches


def fix_env_issues():
    """
    Fix environment issues by providing clear instructions.

    Returns:
        bool: True if issues can be auto-fixed, False if manual intervention needed
    """
    mismatches = check_env_consistency()

    if not mismatches:
        log("✅ Environment is consistent")
        return True

    log("❌ Found environment mismatches:")
    for key, values in mismatches.items():
        log(f"  {key}:")
        log(f"    .env file: {values['env_value'][:20]}...")
        log(
            f"    Shell:     {values['shell_value'][:20]}..."
            if values["shell_value"]
            else "    Shell:     None"
        )

    log("\n🔧 To fix:")
    log("  source .env")
    log("  # or restart your shell")

    # Try to auto-fix for current session
    try:
        for key, values in mismatches.items():
            os.environ[key] = values["env_value"]
        log("✅ Auto-fixed current session (run 'source .env' for permanent fix)")
        return True
    except Exception as e:
        log(f"❌ Auto-fix failed: {e}")
        return False


def ensure_api_key():
    """
    Ensure API key is available and valid.

    Returns:
        bool: True if API key is ready, False otherwise
    """
    # Check consistency first
    if not fix_env_issues():
        return False

    # Validate API key
    if not validate_api_key("OPENROUTER_API_KEY"):
        log("❌ API key validation failed")
        return False

    return True


def check_binaries() -> Dict[str, bool]:
    """Check presence of required CLI tools in PATH."""
    required = ["tesseract", "ocrmypdf"]
    optional = ["pandoc"]
    results: Dict[str, bool] = {}
    for name in required + optional:
        results[name] = shutil.which(name) is not None
    return results


def check_brightdata_access(timeout: int = 10) -> Dict[str, Any]:
    """Attempt a minimal Bright Data Web Unlocker call to validate connectivity.

    Returns a dict with keys: available (bool), status_code (int|None), error (str|None)
    """
    result: Dict[str, Any] = {"available": False, "status_code": None, "error": None}
    try:
        import requests

        api_key = os.getenv("BRIGHTDATA_API_KEY") or get_api_key("BRIGHTDATA_API_KEY")
        zone = os.getenv("BRIGHTDATA_ZONE") or get_api_key("BRIGHTDATA_ZONE")
        if not zone:
            # Keep explicit; BRIGHTDATA_ZONE is required to route the request
            result["error"] = "BRIGHTDATA_ZONE not set"
            return result

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Use a lightweight public page as target; we don't need the body
        payload = {
            "url": "https://chinaxiv.org/",
            "zone": zone,
            "method": "GET",
            "country": "cn",
            "format": "json",
        }
        resp = requests.post(
            "https://api.brightdata.com/request",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        status = resp.status_code
        result["status_code"] = status
        if status in (200, 206):
            result["available"] = True
        elif status == 400:
            # Bright Data recently started requiring additional fields; treat 400 as reachable
            result["available"] = True
            result["error"] = f"HTTP 400 (treated as reachable): {resp.text[:200]}"
        else:
            result["available"] = False
            result["error"] = f"HTTP {status}: {resp.text[:200]}"
    except Exception as e:
        result["error"] = str(e)
    return result


def check_disk_free(path: str = ".", min_gb: float = 1.0) -> Dict[str, Any]:
    """Check free disk space at path; ensure at least min_gb GB available."""
    try:
        usage = shutil.disk_usage(path)
        free_gb = round(usage.free / (1024**3), 2)
        return {"ok": free_gb >= min_gb, "free_gb": free_gb, "min_gb": min_gb}
    except Exception as e:
        return {"ok": False, "free_gb": None, "min_gb": min_gb, "error": str(e)}


def write_preflight_report(report: Dict[str, Any], out_dir: str = "reports") -> None:
    """Write JSON and Markdown preflight reports to out_dir and mirror to site/stats."""
    try:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        json_path = out_path / "preflight_report.json"
        md_path = out_path / "preflight_report.md"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Minimal Markdown summary
        lines = [
            "# Preflight Report",
            "",
            f"Status: {'PASS' if report.get('pass') else 'FAIL'}",
            "",
            "## Checks",
        ]
        for name, value in report.get("checks", {}).items():
            if isinstance(value, dict):
                status = value.get("ok") if "ok" in value else value.get("available")
                lines.append(f"- {name}: {'OK' if status else 'FAIL'}")
            else:
                lines.append(f"- {name}: {value}")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        # Mirror JSON under site/stats/validation for surfacing on the site if present
        site_stats = Path("site/stats/validation")
        site_stats.mkdir(parents=True, exist_ok=True)
        with open(site_stats / "preflight_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"Failed to write preflight report: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Diagnose and resolve environment variable issues"
    )
    parser.add_argument("--check", action="store_true", help="Check for mismatches")
    parser.add_argument("--fix", action="store_true", help="Fix environment issues")
    parser.add_argument(
        "--resolve",
        action="store_true",
        help="Alias for --fix (kept for backwards compatibility)",
    )
    parser.add_argument("--validate", action="store_true", help="Validate API keys")
    parser.add_argument(
        "--preflight", action="store_true", help="Run full preflight and emit reports"
    )
    args = parser.parse_args()

    if args.resolve:
        args.fix = True

    if not any([args.check, args.fix, args.validate, args.preflight]):
        args.check = True  # Default to check mode

    success = True

    if args.check or args.fix:
        if args.fix:
            success = fix_env_issues()
        else:
            mismatches = check_env_consistency()
            if mismatches:
                log("❌ Environment mismatches found")
                success = False
            else:
                log("✅ Environment is consistent")

    if args.validate:
        if validate_api_key("OPENROUTER_API_KEY"):
            log("✅ API key is valid")
        else:
            log("❌ API key validation failed")
            success = False

    if args.preflight:
        checks: Dict[str, Any] = {}
        # Environment keys presence
        checks["env_vars"] = {
            "OPENROUTER_API_KEY": bool(os.getenv("OPENROUTER_API_KEY")),
            "BRIGHTDATA_API_KEY": bool(os.getenv("BRIGHTDATA_API_KEY")),
            "BRIGHTDATA_ZONE": bool(os.getenv("BRIGHTDATA_ZONE")),
            "BACKBLAZE_KEY_ID": bool(os.getenv("BACKBLAZE_KEY_ID")),
            "BACKBLAZE_APPLICATION_KEY": bool(os.getenv("BACKBLAZE_APPLICATION_KEY")),
            "BACKBLAZE_S3_ENDPOINT": bool(os.getenv("BACKBLAZE_S3_ENDPOINT")),
            "BACKBLAZE_BUCKET": bool(os.getenv("BACKBLAZE_BUCKET")),
            "BACKBLAZE_PREFIX": bool(os.getenv("BACKBLAZE_PREFIX")),
            "CF_API_TOKEN": bool(os.getenv("CF_API_TOKEN")),
            "DISCORD_WEBHOOK_URL": bool(os.getenv("DISCORD_WEBHOOK_URL")),
        }
        # API key validation (translation)
        checks["openrouter_api"] = {"ok": validate_api_key("OPENROUTER_API_KEY")}
        # Bright Data reachability (optional; ok also if creds absent)
        bd = check_brightdata_access()
        checks["brightdata"] = {
            "available": bd.get("available"),
            "status_code": bd.get("status_code"),
            "error": bd.get("error"),
        }
        # Binaries
        bins = check_binaries()
        checks["binaries"] = {k: {"ok": v} for k, v in bins.items()}
        # Disk space
        checks["disk"] = check_disk_free()

        # Determine pass/fail: all required items should be ok; BrightData only required if env vars set
        required_ok = (
            checks["openrouter_api"]["ok"]
            and checks["binaries"]["ocrmypdf"]["ok"]
            and checks["binaries"]["tesseract"]["ok"]
            and checks["disk"]["ok"]
        )
        bd_required = (
            checks["env_vars"]["BRIGHTDATA_API_KEY"]
            and checks["env_vars"]["BRIGHTDATA_ZONE"]
        )
        bd_ok = (not bd_required) or bool(checks["brightdata"]["available"])
        overall = required_ok and bd_ok

        report = {"pass": overall, "checks": checks}
        write_preflight_report(report)
        if not overall:
            log("❌ Preflight failed; see reports/preflight_report.* for details")
            success = False
        else:
            log("✅ Preflight passed")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
