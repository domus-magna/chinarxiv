#!/usr/bin/env python3
"""
Sweep B2 validated translation artifacts and clamp pathological titles.

Goal (simplicity-first):
- Detect cases where `title_en` is clearly broken (e.g., contains <PARA ...> tags
  or is extremely long, indicating body text was written into the title).
- Clamp title to a conservative maximum length after stripping PARA wrappers and
  normalizing whitespace.
- Write a report of all changes for manual follow-up; do NOT attempt to guess a
  "better" title.

This script only modifies:
  s3://$BACKBLAZE_BUCKET/validated/translations/{paper_id}.json

It also uploads a report to:
  s3://$BACKBLAZE_BUCKET/reports/title_clamps/{timestamp}.json

Usage:
  python scripts/sweep_b2_title_clamps.py --dry-run
  python scripts/sweep_b2_title_clamps.py
  python scripts/sweep_b2_title_clamps.py --limit 5000

Environment:
  BACKBLAZE_S3_ENDPOINT, BACKBLAZE_BUCKET, BACKBLAZE_KEY_ID, BACKBLAZE_APPLICATION_KEY
  (Optional) BACKBLAZE_PREFIX (not used here; validated/translations is canonical)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import boto3


_PARA_TAG_RE = re.compile(r"</?\s*para\b[^>]*>", re.IGNORECASE)
_MAX_TITLE_LEN = 300


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise SystemExit(f"Missing required env var: {key}")
    return val


def _get_s3_client():
    endpoint = _require_env("BACKBLAZE_S3_ENDPOINT")
    key_id = _require_env("BACKBLAZE_KEY_ID")
    secret = _require_env("BACKBLAZE_APPLICATION_KEY")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
    )


def _normalize_title(title: Any) -> str:
    if not isinstance(title, str):
        title = "" if title is None else str(title)
    cleaned = _PARA_TAG_RE.sub("", title)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= _MAX_TITLE_LEN:
        return cleaned
    return cleaned[: _MAX_TITLE_LEN - 3].rstrip() + "..."


def _extract_title_from_prefix_bytes(prefix: bytes) -> Optional[str]:
    """
    Best-effort extraction of `title_en` from the first N bytes of the JSON.

    We avoid full JSON parsing for the common case. This is intentionally simple:
    translations written by our pipeline serialize `title_en` near the top.
    """
    try:
        text = prefix.decode("utf-8", errors="replace")
    except Exception:
        return None

    marker = '"title_en"'
    idx = text.find(marker)
    if idx == -1:
        return None

    # Find the first quote after the colon.
    colon = text.find(":", idx + len(marker))
    if colon == -1:
        return None

    # Skip whitespace
    j = colon + 1
    while j < len(text) and text[j] in " \t\r\n":
        j += 1

    # Handle null
    if text.startswith("null", j):
        return ""

    if j >= len(text) or text[j] != '"':
        return None

    # Parse a JSON string (very small finite-state parser)
    j += 1
    out_chars: List[str] = []
    escaped = False
    for k in range(j, len(text)):
        ch = text[k]
        if escaped:
            # Preserve escapes by interpreting common ones for readability.
            # We only need approximate content for detection; correctness is not critical here.
            if ch == "n":
                out_chars.append("\n")
            elif ch == "t":
                out_chars.append("\t")
            elif ch == "r":
                out_chars.append("\r")
            else:
                out_chars.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            return "".join(out_chars)
        out_chars.append(ch)

    # Unterminated string (title too large for prefix)
    return None


def _list_keys(s3, bucket: str, prefix: str, limit: Optional[int]) -> Iterable[str]:
    token: Optional[str] = None
    yielded = 0
    while True:
        kwargs: Dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for item in resp.get("Contents", []):
            key = item["Key"]
            if not key.endswith(".json"):
                continue
            yield key
            yielded += 1
            if limit is not None and yielded >= limit:
                return
        if not resp.get("IsTruncated"):
            return
        token = resp.get("NextContinuationToken")


def _get_object_prefix(s3, bucket: str, key: str, nbytes: int) -> bytes:
    # S3 Range is inclusive.
    rng = f"bytes=0-{nbytes - 1}"
    resp = s3.get_object(Bucket=bucket, Key=key, Range=rng)
    return resp["Body"].read()


@dataclass
class TitleClampChange:
    paper_id: str
    key: str
    old_len: int
    new_len: int
    had_para_tags: bool
    old_prefix: str


def _paper_id_from_key(key: str) -> str:
    # validated/translations/{paper_id}.json
    base = key.rsplit("/", 1)[-1]
    return base[: -len(".json")] if base.endswith(".json") else base


def main() -> int:
    parser = argparse.ArgumentParser(description="Clamp pathological title_en in B2")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write changes; only report what would be changed.",
    )
    parser.add_argument(
        "--fail-on-changes",
        action="store_true",
        help="Exit non-zero if any changes are detected/applied (useful for CI).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of objects scanned (for testing).",
    )
    parser.add_argument(
        "--prefix",
        default="validated/translations/",
        help="B2 prefix to scan (default: validated/translations/).",
    )
    parser.add_argument(
        "--max-fixes",
        type=int,
        default=None,
        help="Stop after applying this many fixes (safety valve).",
    )
    args = parser.parse_args()

    bucket = _require_env("BACKBLAZE_BUCKET")
    s3 = _get_s3_client()

    scanned = 0
    changes: List[TitleClampChange] = []
    fixes_applied = 0

    for key in _list_keys(s3, bucket=bucket, prefix=args.prefix, limit=args.limit):
        scanned += 1
        if scanned % 500 == 0:
            print(f"[scan] {scanned} objects, {len(changes)} changes", file=sys.stderr)

        # Fast path: fetch small prefix and try to extract title
        prefix_bytes = _get_object_prefix(s3, bucket, key, nbytes=4096)
        title = _extract_title_from_prefix_bytes(prefix_bytes)

        # If title string is too large to fit, we need a bigger prefix to decide.
        if title is None:
            # 64KiB is enough to contain extremely large titles like the 37k blob case.
            prefix_bytes = _get_object_prefix(s3, bucket, key, nbytes=65536)
            title = _extract_title_from_prefix_bytes(prefix_bytes)
            # If still None, fall back to full JSON (rare).

        needs_full = False
        if title is None:
            needs_full = True
        else:
            if "<PARA" in title.upper() or "</PARA" in title.upper() or len(title) > _MAX_TITLE_LEN:
                needs_full = True

        if not needs_full:
            continue

        # Load full object
        obj = s3.get_object(Bucket=bucket, Key=key)
        raw = obj["Body"].read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            print(f"[warn] failed to parse {key}: {e}", file=sys.stderr)
            continue

        old_title = data.get("title_en", "") or ""
        new_title = _normalize_title(old_title)

        if new_title == old_title:
            continue

        paper_id = data.get("id") or _paper_id_from_key(key)
        change = TitleClampChange(
            paper_id=str(paper_id),
            key=key,
            old_len=len(old_title) if isinstance(old_title, str) else len(str(old_title)),
            new_len=len(new_title),
            had_para_tags=bool(_PARA_TAG_RE.search(old_title if isinstance(old_title, str) else str(old_title))),
            old_prefix=(old_title[:160] if isinstance(old_title, str) else str(old_title)[:160]),
        )
        changes.append(change)

        if args.dry_run:
            continue

        data["title_en"] = new_title
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
        fixes_applied += 1

        if args.max_fixes is not None and fixes_applied >= args.max_fixes:
            print(f"[stop] reached --max-fixes={args.max_fixes}", file=sys.stderr)
            break

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scanned": scanned,
        "prefix": args.prefix,
        "max_title_len": _MAX_TITLE_LEN,
        "changes": [asdict(c) for c in changes],
    }

    report_stamp = _now_stamp()
    local_dir = os.path.join("reports", "maintenance")
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, f"b2_title_clamps_{report_stamp}.json")
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[done] scanned={scanned} changes={len(changes)} dry_run={args.dry_run}")
    print(f"[report] {local_path}")

    # Upload report for visibility (even in dry run)
    remote_key = f"reports/title_clamps/{report_stamp}.json"
    with open(local_path, "rb") as f:
        s3.put_object(
            Bucket=bucket,
            Key=remote_key,
            Body=f.read(),
            ContentType="application/json",
        )
    print(f"[report-uploaded] s3://{bucket}/{remote_key}")

    if args.fail_on_changes and changes:
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
