#!/usr/bin/env python3
"""Sync validated translations from Backblaze B2 into data/translated."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_VARS = [
    "BACKBLAZE_KEY_ID",
    "BACKBLAZE_APPLICATION_KEY",
    "BACKBLAZE_S3_ENDPOINT",
    "BACKBLAZE_BUCKET",
]

SUMMARY_PATH = Path("reports") / "hydration_summary.json"


def ensure_awscli() -> None:
    if shutil.which("aws") is None:
        raise SystemExit("aws CLI not found in PATH; install awscli before running hydration.")


def build_destination(prefix: str | None) -> str:
    bucket = os.environ["BACKBLAZE_BUCKET"].rstrip("/")
    prefix = (prefix or "").strip("/")
    if prefix:
        return f"s3://{bucket}/{prefix}"
    return f"s3://{bucket}"


def sync_translations(target_dir: Path, prefix: str | None, endpoint: str) -> int:
    dest_root = build_destination(prefix)
    source = f"{dest_root}/validated/translations"

    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "aws",
        "s3",
        "sync",
        source,
        str(target_dir),
        "--exclude",
        "*",
        "--include",
        "*.json",
        "--endpoint-url",
        endpoint,
        "--only-show-errors",
    ]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    return sum(1 for _ in target_dir.glob("*.json"))


def _resolve_expected_count(explicit: int | None, file_path: str | None) -> int | None:
    if explicit is not None:
        return explicit
    if not file_path:
        return None
    candidate = Path(file_path)
    if not candidate.exists():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        return None
    for key in ("validated_uploaded", "expected_validated", "qa_passed"):
        if isinstance(payload.get(key), int):
            return payload[key]
    return None


def _write_summary(hydrated: int, expected: int | None, verified: bool) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(
            {
                "hydrated_count": hydrated,
                "expected_count": expected,
                "verified": verified,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Hydrate validated translations from Backblaze B2.")
    parser.add_argument("--target", default="data/translated", help="Local directory to populate (default: data/translated)")
    parser.add_argument(
        "--expected-count",
        type=int,
        default=None,
        help="Expected validated translation count (overrides --expected-count-file)",
    )
    parser.add_argument(
        "--expected-count-file",
        default=os.getenv("HYDRATE_EXPECTED_COUNT_FILE"),
        help="Path to JSON summary containing expected count (default: HYDRATE_EXPECTED_COUNT_FILE env).",
    )
    parser.add_argument(
        "--allow-mismatch",
        action="store_true",
        default=os.getenv("HYDRATE_ALLOW_MISMATCH", "false").lower() == "true",
        help="Allow count mismatches without exiting non-zero.",
    )
    args = parser.parse_args()

    env_expected = os.getenv("HYDRATE_EXPECTED_COUNT")
    if args.expected_count is None and env_expected:
        try:
            args.expected_count = int(env_expected)
        except ValueError:
            pass

    missing = [var for var in REQUIRED_VARS if not os.getenv(var)]
    if missing:
        raise SystemExit(f"Missing required Backblaze environment variables: {', '.join(missing)}")

    ensure_awscli()

    os.environ.setdefault("AWS_ACCESS_KEY_ID", os.environ["BACKBLAZE_KEY_ID"])
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", os.environ["BACKBLAZE_APPLICATION_KEY"])
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-004")

    target_dir = Path(args.target)
    prefix = os.getenv("BACKBLAZE_PREFIX", "")
    endpoint = os.environ["BACKBLAZE_S3_ENDPOINT"]

    count = sync_translations(target_dir, prefix, endpoint)
    expected = _resolve_expected_count(args.expected_count, args.expected_count_file)
    verified = expected is None or count == expected

    _write_summary(count, expected, verified)

    if count == 0:
        raise SystemExit("Hydration completed but no translation JSON files were synced; aborting.")

    if expected is not None and count != expected:
        msg = f"Hydration parity check failed: expected {expected}, got {count}"
        if args.allow_mismatch:
            print(f"⚠️  {msg} (allowed)")
        else:
            raise SystemExit(msg)

    print(f"Hydrated {count} translation files into {target_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
