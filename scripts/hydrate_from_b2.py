#!/usr/bin/env python3
"""Sync validated translations from Backblaze B2 into data/translated."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


REQUIRED_VARS = [
    "BACKBLAZE_KEY_ID",
    "BACKBLAZE_APPLICATION_KEY",
    "BACKBLAZE_S3_ENDPOINT",
    "BACKBLAZE_BUCKET",
]


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Hydrate validated translations from Backblaze B2.")
    parser.add_argument("--target", default="data/translated", help="Local directory to populate (default: data/translated)")
    args = parser.parse_args()

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
    if count == 0:
        raise SystemExit("Hydration completed but no translation JSON files were synced; aborting.")

    print(f"Hydrated {count} translation files into {target_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
