#!/usr/bin/env python3
"""
PDF Download + Text Extraction Pipeline

Downloads PDFs from provided URLs and extracts text using pdfminer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from collections import Counter
from contextlib import suppress
from typing import Any, Dict, List, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from .http_client import get_session
from .config import get_proxies, get_config
from .body_extract import extract_from_pdf
from .utils import log, read_json, write_json

try:
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore


@retry(wait=wait_exponential(min=1, max=10), stop=stop_after_attempt(3))
def download_pdf(
    url: str,
    output_path: str,
    *,
    referer: str | None = None,
    session_id: str | None = None,
) -> bool:
    """
    Download a PDF from a URL with validation.

    Args:
        url: PDF URL
        output_path: Local path to save PDF

    Returns:
        True if successful, False otherwise
    """
    try:
        session = get_session()
        proxies, source = get_proxies()
        kwargs = {
            "timeout": 60,
            "stream": True,
            "allow_redirects": True,
        }
        if source == "config" and proxies:
            kwargs["proxies"] = proxies
            kwargs["verify"] = False  # proxy MITM can break cert chain
        elif source == "env" and proxies:
            kwargs["proxies"] = proxies
            kwargs["verify"] = False  # Bright Data proxy uses MITM cert

        # Warm up cookies by hitting referer first if provided
        if referer:
            try:
                ref_resp = session.get(
                    referer,
                    timeout=30,
                    allow_redirects=True,
                    verify=kwargs.get("verify", True),
                    proxies=kwargs.get("proxies"),
                )
                ref_resp.raise_for_status()
                log(f"Referer warmup ok for {referer} (status={ref_resp.status_code})")
            except Exception as ref_err:
                log(f"Referer warmup failed for {referer}: {ref_err}")

        # Add referer header if provided (helps some endpoints)
        if referer:
            kwargs.setdefault("headers", {})
            kwargs["headers"]["Referer"] = referer
        resp = session.get(url, **kwargs)
        try:
            resp.raise_for_status()
        except requests.HTTPError as http_err:
            status = resp.status_code
            content_type = resp.headers.get("content-type", "").lower()
            preview = ""
            with suppress(Exception):
                preview = (resp.text or "")[:200]
            log(
                f"HTTP error fetching {url}: status={status}, "
                f"content_type={content_type}, preview={preview!r}"
            )
            raise http_err

        # Validate PDF content
        content_type = resp.headers.get("content-type", "").lower()
        if "pdf" not in content_type and not resp.content.startswith(b"%PDF-"):
            preview = ""
            with suppress(Exception):
                preview = (resp.text or "")[:200]
            log(
                f"Invalid PDF content for {url}: status={resp.status_code}, "
                f"content_type={content_type}, length={len(resp.content)}, "
                f"preview={preview!r}"
            )
            log("Falling back to Unlocker proxy, then headless browser if needed")
            if _unlocker_raw_fetch(
                url, output_path, referer=referer, session_id=session_id
            ):
                return True
            return bool(_headless_pdf_fetch(url, output_path, referer=referer, session_id=session_id))

        # Check file size (minimum 1KB)
        content_length = len(resp.content)
        if content_length < 1024:
            log(f"PDF too small for {url}: {content_length} bytes")
            return False

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                f.write(chunk)

        # Verify downloaded file
        if os.path.getsize(output_path) < 1024:
            log(f"Downloaded PDF too small: {output_path}")
            os.remove(output_path)
            return False

        return True
    except Exception as e:
        log(f"Failed to download {url}: {e}")
        # Fallback order: Unlocker first, then headless (consistent with invalid-content handler)
        if _unlocker_raw_fetch(
            url, output_path, referer=referer, session_id=session_id
        ):
            return True
        return bool(_headless_pdf_fetch(url, output_path, referer=referer, session_id=session_id))


def _inject_session_into_wss(wss_url: str, session_id: str) -> str:
    """
    Inject session ID into BrightData Browser WSS URL for IP stickiness.

    BrightData's Scraping Browser supports session persistence via the
    `-session-{id}` suffix in the username portion of the WSS URL. This
    ensures all requests within the session use the same exit IP.

    Transforms:
      wss://brd-customer-X-zone-Y:PWD@host:port
    Into:
      wss://brd-customer-X-zone-Y-session-{ID}:PWD@host:port

    Why needed: ChinaXiv UUIDs are IP-bound. The UUID in the PDF link is
    only valid from the same IP that fetched the abstract page. Without
    session stickiness, BrightData may route requests through different
    IPs, causing 404 errors on PDF downloads.
    """
    import re

    # Regex captures: group1 = "-zone-{zone_name}", group2 = ":{password}@"
    # We insert "-session-{id}" between group1 and group2
    pattern = r"(-zone-[^:]+)(:[^@]+@)"
    replacement = rf"\1-session-{session_id}\2"
    return re.sub(pattern, replacement, wss_url)


def _headless_pdf_fetch(
    url: str,
    output_path: str,
    *,
    referer: str | None = None,
    session_id: str | None = None,
) -> bool:
    """
    Fetch a PDF via Bright Data's remote browser endpoint using Playwright.

    Strategy for ChinaXiv (IP-bound UUIDs):
    1. Connect with session ID to maintain same IP throughout
    2. Navigate to abstract page (referer) to get fresh UUID
    3. Extract PDF link from page (fresh UUID)
    4. Use JavaScript fetch() to download PDF (maintains same IP context)
    5. Transfer via base64 and save
    """
    import base64

    endpoint = os.getenv("BRIGHTDATA_BROWSER_WSS")
    if not endpoint:
        return False

    # Inject session ID into WSS URL for IP stickiness
    if session_id:
        endpoint = _inject_session_into_wss(endpoint, session_id)
        log(f"Headless: using session {session_id} for IP stickiness")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional dependency
        log(f"Playwright not available for headless fallback: {exc}")
        return False

    page = None
    browser = None
    try:
        log(f"Headless fallback: connecting to Bright Data browser for {url}")
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(endpoint, timeout=60_000)
            context = browser.new_context(ignore_https_errors=True)
            context.set_default_timeout(90_000)

            page = context.new_page()
            page.set_default_timeout(90_000)

            # STEP 1: Navigate to abstract page to get fresh UUID
            # ChinaXiv UUIDs are IP-bound. The UUID passed in `url` was generated by
            # a different IP (the harvester). We must visit the abstract page from
            # THIS browser's IP to get a UUID that will work for the PDF download.
            pdf_url = url
            if referer:
                try:
                    log(f"Headless: navigating to abstract page {referer}")
                    page.goto(referer, wait_until="domcontentloaded", timeout=90_000)
                    page.wait_for_timeout(1000)

                    # Extract fresh PDF link - this contains a UUID bound to our current IP
                    pdf_link = page.query_selector('a[href*="filetype=pdf"]')
                    if pdf_link:
                        href = pdf_link.get_attribute("href")
                        if href:
                            if href.startswith("/"):
                                pdf_url = f"https://chinaxiv.org{href}"
                            else:
                                pdf_url = href
                            log(f"Headless: extracted fresh PDF URL: {pdf_url}")
                except PlaywrightTimeoutError:
                    log(f"Headless: abstract page timed out for {referer}")
                except Exception as warm_err:
                    log(f"Headless: abstract page failed for {referer}: {warm_err}")

            # STEP 2: Download PDF using JavaScript fetch() API
            # Why JS fetch() instead of page.goto()? BrightData Browser has navigation
            # limits that cause "Page.navigate limit reached" errors on the second nav.
            # Using fetch() within the browser context:
            # - Bypasses navigation limits (fetch is not a navigation)
            # - Maintains same IP/session context (critical for IP-bound UUIDs)
            # - Returns binary data we can transfer back via base64
            log(f"Headless: fetching PDF via JS fetch(): {pdf_url}")
            js_fetch_code = """
                async (url) => {
                    try {
                        const response = await fetch(url, {
                            method: "GET",
                            credentials: "include"
                        });
                        if (!response.ok) {
                            return {error: "HTTP " + response.status, status: response.status};
                        }
                        const buffer = await response.arrayBuffer();
                        const bytes = new Uint8Array(buffer);
                        let binary = "";
                        for (let i = 0; i < bytes.length; i++) {
                            binary += String.fromCharCode(bytes[i]);
                        }
                        const base64 = btoa(binary);
                        return {
                            success: true,
                            status: response.status,
                            contentType: response.headers.get("content-type"),
                            size: bytes.length,
                            base64: base64
                        };
                    } catch (e) {
                        return {error: e.toString()};
                    }
                }
            """
            result = page.evaluate(js_fetch_code, pdf_url)

            if not result:
                log(f"Headless: JS fetch returned null for {pdf_url}")
                return False

            if result.get("error"):
                log(f"Headless: JS fetch error for {pdf_url}: {result['error']}")
                return False

            if not result.get("success") or not result.get("base64"):
                log(f"Headless: JS fetch failed for {pdf_url}: {result}")
                return False

            # STEP 3: Transfer PDF from browser to Python via base64
            # Playwright's evaluate() can only return JSON-serializable data, so we
            # encoded the binary PDF as base64 in JavaScript. Decode it here.
            pdf_bytes = base64.b64decode(result["base64"])
            size = len(pdf_bytes)
            log(f"Headless: received {size:,} bytes from JS fetch")

            if size < 1024:
                log(f"Headless: PDF too small ({size} bytes) for {pdf_url}")
                return False

            if not pdf_bytes.startswith(b"%PDF-"):
                preview = pdf_bytes[:200].decode("utf-8", errors="replace")
                log(f"Headless: response not PDF for {pdf_url}, preview: {preview[:100]}")
                return False

            # Save PDF
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)

            log(f"Headless fallback succeeded for {url} ({size:,} bytes)")
            return True

    except PlaywrightTimeoutError as exc:
        log(f"Headless Playwright timeout for {url}: {exc}")
        return False
    except Exception as exc:
        # If the event loop is already running (common when invoked in some runtimes),
        # skip headless and let the caller fall back to Unlocker.
        if "event loop is already running" in str(exc).lower():
            log(
                f"Headless Playwright fallback failed (loop running) for {url}; skipping headless and relying on Unlocker"
            )
            return False
        log(f"Headless Playwright fallback failed for {url}: {exc}")
        return False
    finally:
        if page:
            with suppress(Exception):
                page.close()
        if browser:
            with suppress(Exception):
                browser.close()


def _unlocker_raw_fetch(
    url: str,
    output_path: str,
    *,
    referer: str | None = None,
    session_id: str | None = None,
) -> bool:
    """Fallback to Bright Data Web Unlocker raw API."""
    api_key = os.getenv("BRIGHTDATA_API_KEY")
    unlocker_zone = os.getenv("BRIGHTDATA_UNLOCKER_ZONE") or os.getenv(
        "BRIGHTDATA_ZONE"
    )
    if not api_key or not unlocker_zone:
        log("Bright Data Unlocker credentials missing; skipping Unlocker fallback")
        return False
    try:
        import requests as _requests
    except Exception as exc:  # pragma: no cover - requests is a core dep
        log(f"Requests not available for Unlocker fallback: {exc}")
        return False

    _sess = _requests.Session()
    _sess.trust_env = False
    _sess.verify = False  # allow proxy MITM cert
    customer_id = os.getenv("BRIGHTDATA_CUSTOMER_ID", "hl_7f044a29")
    username = f"brd-customer-{customer_id}-zone-{unlocker_zone}"
    if session_id:
        username += f"-session-{session_id}"
    password = (
        os.getenv("BRIGHTDATA_UNLOCKER_PASSWORD")
        or os.getenv("BRIGHTDATA_ZONE_PASSWORD")
        or os.getenv("BRIGHTDATA_ZONE")
    )
    if not password:
        log("Bright Data Unlocker password missing; skipping Unlocker fallback")
        return False
    port = os.getenv("BRIGHTDATA_UNLOCKER_PORT", "33335")
    proxy_auth = f"http://{username}:{password}@brd.superproxy.io:{port}"
    proxies = {"http": proxy_auth, "https": proxy_auth}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer

    try:
        log(f"Unlocker fallback: requesting {url} via zone {unlocker_zone}")
        resp = _sess.get(url, headers=headers, proxies=proxies, timeout=90)
        resp.raise_for_status()
    except _requests.exceptions.ProxyError as exc:
        log(f"Unlocker tunnel/proxy error for {url}: {exc}")
        return False
    except _requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else "?"
        log(f"Unlocker HTTP error for {url}: status={status}, error={exc}")
        return False
    except Exception as exc:
        log(f"Unlocker connection error for {url}: {type(exc).__name__}: {exc}")
        return False

    body = resp.content or b""
    content_type = resp.headers.get("content-type", "")
    if not body.startswith(b"%PDF-"):
        preview = body[:120].decode("utf-8", errors="replace")
        log(
            f"Unlocker non-PDF response for {url}: "
            f"status={resp.status_code}, content-type={content_type}, "
            f"len={len(body)}, preview={preview!r}"
        )
        return False

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(body)

    if os.path.getsize(output_path) < 1024:
        log(f"Downloaded PDF too small after Unlocker fallback: {output_path}")
        os.remove(output_path)
        return False
    log(f"Unlocker fallback succeeded for {url}")
    return True


def fix_pdf_url(pdf_url: str, paper_id: str) -> str:
    """Return PDF URL unchanged (no IA-specific rewriting)."""
    return pdf_url


def _write_ocr_record(report_dir: str, paper_id: str, record: Dict[str, Any]) -> None:
    """Persist OCR detection/execution details with coarse file locking."""
    report_path = os.path.join(report_dir, "ocr_report.json")
    os.makedirs(report_dir, exist_ok=True)
    fh = open(report_path, "a+", encoding="utf-8")
    try:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        raw = fh.read()
        data: Dict[str, Any] = {}
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log(f"OCR report malformed; resetting {report_path}")
                data = {}
        data[paper_id] = record
        fh.seek(0)
        fh.truncate()
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    finally:
        if fcntl:
            with suppress(OSError):
                fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _compute_text_metrics(paragraphs: List[str]) -> Dict[str, float]:
    """Compute simple text quality metrics for OCR evaluation."""
    text = "".join(paragraphs) if paragraphs else ""
    char_count = len(text)
    if not text:
        return {
            "char_count": 0,
            "alpha_ratio": 0.0,
            "most_common_ratio": 1.0,
        }

    alpha_chars = sum(1 for ch in text if ch.isalpha())
    tokens = [ch for ch in text if not ch.isspace()]
    if tokens:
        most_common_count = Counter(tokens).most_common(1)[0][1]
        most_common_ratio = most_common_count / len(tokens)
    else:
        most_common_ratio = 1.0

    return {
        "char_count": char_count,
        "alpha_ratio": alpha_chars / char_count if char_count else 0.0,
        "most_common_ratio": most_common_ratio,
    }


def process_paper(
    paper_id: str,
    pdf_url: str,
    pdf_dir: str = "data/pdfs",
    *,
    referer: str | None = None,
    session_id: str | None = None,
) -> Optional[Dict]:
    """
    Download PDF and extract text for a single paper.

    Args:
        paper_id: Paper identifier
        pdf_url: URL to PDF
        pdf_dir: Directory to store PDFs
        referer: Optional referer header to warm cookies/on-site auth
        session_id: Optional session identifier for sticky proxy sessions

    Returns:
        Dict with pdf_path and paragraphs, or None if failed
    """
    # Fix PDF URL if needed
    pdf_url = fix_pdf_url(pdf_url, paper_id)

    # Download PDF
    pdf_path = os.path.join(pdf_dir, f"{paper_id}.pdf")

    if not os.path.exists(pdf_path):
        log(f"Downloading {paper_id}...")
        download_kwargs: dict[str, Any] = {}
        if referer:
            download_kwargs["referer"] = referer
        download_kwargs["session_id"] = session_id or paper_id
        try:
            success = download_pdf(pdf_url, pdf_path, **download_kwargs)
        except TypeError:
            # Backward compatibility for monkeypatched downloaders without new kwargs
            success = download_pdf(pdf_url, pdf_path)
        if not success:
            return None
    else:
        log(f"PDF exists: {paper_id}")

    cfg = get_config()
    threshold_cfg = cfg.get("validation_thresholds", {})
    detection_cfg = threshold_cfg.get("pdf_detection", {})
    ocr_cfg = threshold_cfg.get("ocr", {})
    detect_char_threshold = int(detection_cfg.get("min_char_threshold", 1500))
    min_char_gain = int(ocr_cfg.get("min_char_gain", 500))
    min_multiplier = float(ocr_cfg.get("min_multiplier", 5.0))
    min_alpha_ratio = float(ocr_cfg.get("min_alpha_ratio", 0.0))
    max_most_common_ratio = float(ocr_cfg.get("max_most_common_ratio", 1.0))

    # Extract text
    log(f"Extracting text from {paper_id}...")
    paragraphs = extract_from_pdf(pdf_path)
    pre_metrics = _compute_text_metrics(paragraphs)
    total_chars = pre_metrics["char_count"]

    # OCR detection thresholds (configurable)
    need_ocr = not paragraphs or total_chars < detect_char_threshold

    report_dir = os.path.join("reports")
    ocr_record: Dict[str, Any] = {
        "pdf_path": pdf_path,
        "need_ocr": bool(need_ocr),
        "pre_ocr_chars": pre_metrics["char_count"],
        "pre_alpha_ratio": round(pre_metrics["alpha_ratio"], 4),
        "pre_most_common_ratio": round(pre_metrics["most_common_ratio"], 4),
        "ran_ocr": False,
        "ocr_pdf_path": None,
        "post_ocr_chars": pre_metrics["char_count"],
        "post_alpha_ratio": round(pre_metrics["alpha_ratio"], 4),
        "post_most_common_ratio": round(pre_metrics["most_common_ratio"], 4),
        "improved": False,
        "improvement": 0,
        "quality_ok": pre_metrics["alpha_ratio"] >= min_alpha_ratio
        and pre_metrics["most_common_ratio"] <= max_most_common_ratio,
    }

    # Run OCR if needed and possible
    if need_ocr and shutil.which("ocrmypdf") and shutil.which("tesseract"):
        original_paragraphs = paragraphs
        try:
            ocr_dir = os.path.join(pdf_dir, "ocr")
            os.makedirs(ocr_dir, exist_ok=True)
            ocr_out = os.path.join(ocr_dir, f"{paper_id}.pdf")
            # Use chi_sim+eng to cover Chinese and English; skip pages with text
            cmd = [
                "ocrmypdf",
                "--skip-text",
                "--optimize",
                "0",
                "--language",
                "chi_sim+eng",
                pdf_path,
                ocr_out,
            ]
            log(f"Running OCR for {paper_id}…")
            subprocess.run(
                cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            # Re-extract
            paragraphs = extract_from_pdf(ocr_out)
            post_metrics = _compute_text_metrics(paragraphs)
            post_chars = post_metrics["char_count"]
            char_gain = post_chars - pre_metrics["char_count"]
            ratio_gain = (
                (post_chars / pre_metrics["char_count"])
                if pre_metrics["char_count"] > 0
                else (float("inf") if post_chars > 0 else 0.0)
            )
            char_gain_ok = (char_gain >= min_char_gain) or (
                pre_metrics["char_count"] > 0 and ratio_gain >= min_multiplier
            )
            quality_ok = (
                post_metrics["alpha_ratio"] >= min_alpha_ratio
                and post_metrics["most_common_ratio"] <= max_most_common_ratio
            )
            improved_flag = char_gain_ok and quality_ok
            ocr_record.update(
                {
                    "ran_ocr": True,
                    "ocr_pdf_path": ocr_out,
                    "post_ocr_chars": post_chars,
                    "post_alpha_ratio": round(post_metrics["alpha_ratio"], 4),
                    "post_most_common_ratio": round(
                        post_metrics["most_common_ratio"], 4
                    ),
                    "improved": improved_flag,
                    "improvement": char_gain,
                    "quality_ok": quality_ok,
                }
            )

            # Prefer OCR output if improved
            if improved_flag:
                pdf_path = ocr_out
                total_chars = post_chars
                ocr_record["pdf_path"] = pdf_path
            else:
                paragraphs = original_paragraphs
                ocr_record["pdf_path"] = pdf_path
                log(
                    f"OCR did not meet improvement thresholds for {paper_id} "
                    f"(char_gain_ok={char_gain_ok}, quality_ok={quality_ok})"
                )
        except Exception as e:
            log(f"OCR failed for {paper_id}: {e}")
            paragraphs = original_paragraphs

    if not paragraphs:
        ocr_record["post_ocr_chars"] = total_chars
        _write_ocr_record(report_dir, paper_id, ocr_record)
        log(f"No text extracted from {paper_id}")
        return None

    log(f"Extracted {len(paragraphs)} paragraphs from {paper_id}")

    result = {
        "pdf_path": pdf_path,
        "paragraphs": paragraphs,
        "num_paragraphs": len(paragraphs),
        "total_chars": sum(len(p) for p in paragraphs),
    }

    # Mirror minimal detection/execution outcome under site/stats for monitoring
    try:
        os.makedirs(os.path.join("site", "stats", "validation"), exist_ok=True)
        # Append/update single-paper info into aggregate files already written above
        # (no-op here; aggregate reports are maintained earlier)
        pass
    except Exception:
        pass

    ocr_record["post_ocr_chars"] = total_chars
    _write_ocr_record(report_dir, paper_id, ocr_record)

    return result


def batch_download_and_extract(
    paper_ids: List[str],
    records_file: str = "data/records/ia_all_20251004_215726.json",
    pdf_dir: str = "data/pdfs",
    output_file: Optional[str] = None,
) -> Dict[str, Dict]:
    """
    Download and extract text from multiple papers.

    Args:
        paper_ids: List of paper IDs to process
        records_file: Path to records JSON with pdf_url
        pdf_dir: Directory to store PDFs
        output_file: Optional path to save extraction results

    Returns:
        Dict mapping paper_id to extraction results
    """
    # Load records
    records = read_json(records_file)
    id_to_rec = {r["id"]: r for r in records}

    results = {}

    for paper_id in paper_ids:
        if paper_id not in id_to_rec:
            log(f"Paper {paper_id} not found in records")
            continue

        rec = id_to_rec[paper_id]
        pdf_url = rec.get("pdf_url")

        if not pdf_url:
            log(f"No PDF URL for {paper_id}")
            continue

        result = process_paper(paper_id, pdf_url, pdf_dir)
        if result:
            results[paper_id] = result

        # Optional pacing for remote servers
        time.sleep(0.2)

    # Save results
    if output_file:
        write_json(output_file, results)
        log(f"Saved extraction results to {output_file}")

    return results


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Download PDFs and extract text")
    parser.add_argument("--paper-ids", nargs="+", help="Specific paper IDs to process")
    parser.add_argument(
        "--records", default="data/records/records.json", help="Path to records JSON"
    )
    parser.add_argument("--pdf-dir", default="data/pdfs", help="Directory for PDFs")
    parser.add_argument("--output", help="Output JSON file for extraction results")
    parser.add_argument("--test", action="store_true", help="Test on first 10 papers")

    args = parser.parse_args()

    if args.test:
        # Get first 10 papers from records
        records = read_json(args.records)
        paper_ids = [r["id"] for r in records[:10]]
        log(f"Testing on {len(paper_ids)} papers")
    elif args.paper_ids:
        paper_ids = args.paper_ids
    else:
        parser.error("Must specify --paper-ids or --test")

    results = batch_download_and_extract(
        paper_ids=paper_ids,
        records_file=args.records,
        pdf_dir=args.pdf_dir,
        output_file=args.output,
    )

    log(f"\nProcessed {len(results)}/{len(paper_ids)} papers successfully")

    # Show summary
    if results:
        total_paras = sum(r["num_paragraphs"] for r in results.values())
        total_chars = sum(r["total_chars"] for r in results.values())
        log(f"Total paragraphs: {total_paras:,}")
        log(f"Total characters: {total_chars:,}")


if __name__ == "__main__":
    run_cli()
