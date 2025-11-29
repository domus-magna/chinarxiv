#!/usr/bin/env python3
"""
Direct ChinaXiv scraping (optimized mode).

Optimized ChinaXiv harvester with smart ID discovery.
Uses homepage parsing + binary search to find paper ID ranges,
then probes only valid ranges instead of entire 00001-99999 space.
"""

import argparse
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from .utils import log, read_json, write_json


class OptimizedChinaXivScraper:
    """Cost-optimized scraper with intelligent ID discovery."""

    def __init__(self, api_key: str, zone: str, rate_limit: float = 0.5):
        self.api_key = api_key
        self.zone = zone
        self.rate_limit = rate_limit
        self.base_url = "https://chinaxiv.org/abs"
        self.api_url = "https://api.brightdata.com/request"

        self.stats = {
            "total_attempts": 0,
            "successful_scrapes": 0,
            "failed_scrapes": 0,
            "binary_search_requests": 0,
        }

    def fetch_page(self, url: str, max_retries: int = 3) -> Optional[str]:
        """
        Fetch page HTML using BrightData with retry logic.

        Args:
            url: Full URL to fetch
            max_retries: Maximum number of retry attempts

        Returns:
            HTML content if successful, None otherwise
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {"zone": self.zone, "url": url, "format": "raw"}

        for attempt in range(max_retries + 1):
            try:
                time.sleep(self.rate_limit)

                response = requests.post(
                    self.api_url, headers=headers, json=payload, timeout=60
                )

                self.stats["total_attempts"] += 1

                if response.status_code == 200:
                    html = response.text

                    # Check for error responses
                    if "<ErrorResponseData>" in html or len(html) < 1000:
                        if attempt < max_retries:
                            log(
                                f"Error response, retrying {url} (attempt {attempt + 1})"
                            )
                            time.sleep(2**attempt)  # Exponential backoff
                            continue
                        return None

                    return html

                elif response.status_code in [429, 503, 504]:
                    # Rate limiting or server errors - retry with backoff
                    if attempt < max_retries:
                        backoff_time = 2**attempt
                        log(
                            f"Rate limited, retrying {url} in {backoff_time}s (attempt {attempt + 1})"
                        )
                        time.sleep(backoff_time)
                        continue
                    return None

                else:
                    log(f"HTTP {response.status_code} for {url}")
                    return None

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    log(f"Timeout, retrying {url} (attempt {attempt + 1})")
                    time.sleep(2**attempt)
                    continue
                log(f"Timeout after {max_retries} retries for {url}")
                return None

            except Exception as e:
                if attempt < max_retries:
                    log(f"Fetch exception, retrying {url}: {e} (attempt {attempt + 1})")
                    time.sleep(2**attempt)
                    continue
                log(f"Fetch exception after {max_retries} retries for {url}: {e}")
                return None

        return None

    def paper_exists(self, paper_id: str) -> bool:
        """
        Check if paper ID exists (for binary search).

        Args:
            paper_id: Paper ID to check

        Returns:
            True if paper exists, False otherwise
        """
        url = f"{self.base_url}/{paper_id}"
        html = self.fetch_page(url)
        return html is not None

    def extract_homepage_max_ids(self) -> Dict[str, int]:
        """
        Extract known max paper IDs from homepage.

        Returns:
            Dict mapping year_month to max ID number
        """
        log("Extracting max IDs from homepage...")

        html = self.fetch_page("https://chinaxiv.org/home.htm")
        if not html:
            log("Failed to fetch homepage")
            return {}

        soup = BeautifulSoup(html, "html.parser")
        paper_links = soup.find_all("a", href=lambda x: x and "/abs/" in x)

        # Group IDs by month
        by_month = defaultdict(list)
        for link in paper_links:
            href = link.get("href", "")
            if "/abs/" in href:
                paper_id = href.split("/abs/")[-1].split("?")[0]
                if paper_id and "." in paper_id:
                    try:
                        year_month, num_str = paper_id.split(".")
                        num = int(num_str)
                        by_month[year_month].append(num)
                    except Exception:
                        continue

        # Find max for each month
        max_ids = {}
        for year_month, nums in by_month.items():
            max_ids[year_month] = max(nums)
            log(f"  {year_month}: max ID = {max_ids[year_month]:05d}")

        return max_ids

    def find_max_id_binary_search(
        self, year_month: str, estimated_max: int = 500
    ) -> int:
        """
        Use binary search to find the highest paper ID for a month.

        Args:
            year_month: Month to search (e.g., "202504")
            estimated_max: Starting upper bound (default: 500 based on typical month size)

        Returns:
            Highest paper number found (e.g., 412 for 202504.00412)
        """
        log(f"Binary searching for max ID in {year_month}...")

        low, high = 1, estimated_max
        max_found = 0

        self.stats["binary_search_requests"] = 0

        while low <= high:
            mid = (low + high) // 2
            paper_id = f"{year_month}.{mid:05d}"

            if self.paper_exists(paper_id):
                max_found = mid
                low = mid + 1  # Try higher
                log(f"  ✓ {paper_id} exists, searching higher (range: {low}-{high})")
            else:
                high = mid - 1  # Try lower
                log(f"  ✗ {paper_id} missing, searching lower (range: {low}-{high})")

            self.stats["binary_search_requests"] += 1

            # Safety limit
            if self.stats["binary_search_requests"] > 20:
                log("  Stopping binary search after 20 requests")
                break

        # If we maxed out, try doubling and searching again
        if max_found == estimated_max:
            log(f"  Hit estimated max, expanding search to {estimated_max * 2}...")
            return self.find_max_id_binary_search(year_month, estimated_max * 2)

        log(
            f"  Found max ID: {year_month}.{max_found:05d} (took {self.stats['binary_search_requests']} requests)"
        )
        return max_found

    def parse_paper(self, html: str, paper_id: str) -> Optional[Dict]:
        """Parse paper metadata from HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Title
            title_elem = soup.find("h1")
            title = title_elem.get_text(strip=True) if title_elem else ""

            if not title or len(title) < 10:
                return None

            # Authors
            author_links = soup.find_all("a", href=lambda x: x and "field=author" in x)
            creators = [
                link.get_text(strip=True)
                for link in author_links
                if link.get_text(strip=True)
            ]

            # Abstract
            abstract = ""
            abstract_marker = soup.find("b", string=re.compile(r"摘要[:：]"))
            if abstract_marker:
                parent = abstract_marker.parent
                if parent:
                    full_text = parent.get_text(strip=False)
                    match = re.search(r"摘要[:：]\s*(.+)", full_text, re.DOTALL)
                    if match:
                        abstract = match.group(1).strip()

            # Submission date
            date_str = ""
            date_marker = soup.find("b", string=re.compile(r"提交时间[:：]"))
            if date_marker:
                parent = date_marker.parent
                if parent:
                    text = parent.get_text(strip=True)
                    match = re.search(r"提交时间[:：]\s*(.+)", text)
                    if match:
                        date_str = match.group(1).strip()

            # Parse date
            date_iso = ""
            if date_str:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    date_iso = dt.isoformat() + "Z"
                except Exception:
                    year = paper_id[:4]
                    month = paper_id[4:6]
                    date_iso = f"{year}-{month}-01T00:00:00Z"

            # Category/Subjects
            subjects = []
            category_marker = soup.find("b", string=re.compile(r"分类[:：]"))
            if category_marker:
                parent = category_marker.parent
                if parent:
                    category_links = parent.find_all("a")
                    subjects = [
                        link.get_text(strip=True)
                        for link in category_links
                        if link.get_text(strip=True)
                    ]

            # PDF URL
            pdf_url = ""
            pdf_link = soup.find("a", href=lambda x: x and "filetype=pdf" in x)
            if pdf_link:
                href = pdf_link.get("href", "")
                if href.startswith("/"):
                    pdf_url = f"https://chinaxiv.org{href}"
                else:
                    pdf_url = href

            # Build IA-compatible record
            record = {
                "id": f"chinaxiv-{paper_id}",
                "oai_identifier": paper_id,
                "title": title,
                "abstract": abstract,
                "creators": creators,
                "subjects": subjects,
                "date": date_iso or f"{paper_id[:4]}-{paper_id[4:6]}-01T00:00:00Z",
                "source_url": f"https://chinaxiv.org/abs/{paper_id}",
                "pdf_url": pdf_url,
                "license": {"raw": "", "derivatives_allowed": None},
                "setSpec": None,
            }

            return record

        except Exception as e:
            log(f"Parse error for {paper_id}: {e}")
            return None

    def scrape_paper(self, paper_id: str) -> Optional[Dict]:
        """Fetch and parse a single paper."""
        url = f"{self.base_url}/{paper_id}"
        html = self.fetch_page(url)

        if not html:
            return None

        paper = self.parse_paper(html, paper_id)
        if paper:
            self.stats["successful_scrapes"] += 1
            return paper
        else:
            self.stats["failed_scrapes"] += 1
            return None

    def scrape_month_optimized(
        self, year_month: str, max_id: int, checkpoint: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Scrape papers for a month using known max ID.

        Args:
            year_month: Month to scrape (e.g., "202504")
            max_id: Maximum paper number to probe
            checkpoint: Optional checkpoint to resume from

        Returns:
            List of paper metadata dicts
        """
        papers = []
        start_num = 1

        # Resume from checkpoint
        if checkpoint:
            papers = checkpoint.get("papers", [])
            start_num = checkpoint.get("last_id_num", 1) + 1
            log(f"Resuming {year_month} from ID #{start_num:05d}")

        log(f"Scraping {year_month} from #{start_num:05d} to #{max_id:05d}")

        for num in range(start_num, max_id + 1):
            paper_id = f"{year_month}.{num:05d}"

            paper = self.scrape_paper(paper_id)

            if paper:
                papers.append(paper)
                log(f"✓ {paper_id}: {paper['title'][:60]}...")

                # Save checkpoint every 10 papers
                if len(papers) % 10 == 0:
                    self._save_checkpoint(year_month, papers, num)

        log(f"Finished {year_month}: {len(papers)} papers scraped")
        return papers

    def _save_checkpoint(self, year_month: str, papers: List[Dict], last_id_num: int):
        """Save checkpoint for resume capability."""
        checkpoint_dir = Path("data/checkpoints")
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        checkpoint = {
            "year_month": year_month,
            "last_id_num": last_id_num,
            "papers": papers,
            "timestamp": datetime.utcnow().isoformat(),
        }

        checkpoint_path = str(checkpoint_dir / f"chinaxiv_opt_{year_month}.json")
        write_json(checkpoint_path, checkpoint)

    def _load_checkpoint(self, year_month: str) -> Optional[Dict]:
        """Load checkpoint if exists."""
        checkpoint_path = Path("data/checkpoints") / f"chinaxiv_opt_{year_month}.json"
        if checkpoint_path.exists():
            return read_json(str(checkpoint_path))
        return None

    def save_results(self, year_month: str, papers: List[Dict]):
        """Save results to IA-compatible JSON."""
        output_dir = Path("data/records")
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = str(output_dir / f"chinaxiv_{year_month}.json")
        write_json(output_path, papers)
        log(f"Saved {len(papers)} papers to {output_path}")


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Optimized ChinaXiv harvester")
    parser.add_argument("--start", help="Start month (YYYYMM)")
    parser.add_argument("--end", help="End month (YYYYMM)")
    parser.add_argument("--month", help="Single month to scrape (YYYYMM)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="Test mode")
    parser.add_argument(
        "--rate-limit", type=float, default=0.5, help="Seconds between requests"
    )
    args = parser.parse_args()

    # Load environment
    load_dotenv()
    api_key = os.getenv("BRIGHTDATA_API_KEY")
    zone = os.getenv("BRIGHTDATA_ZONE")

    if not api_key or not zone:
        print("Error: BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE must be set")
        return

    # Determine months to scrape
    months = []
    if args.month:
        months = [args.month]
    elif args.start and args.end:
        start_year, start_month = int(args.start[:4]), int(args.start[4:6])
        end_year, end_month = int(args.end[:4]), int(args.end[4:6])

        year, month = start_year, start_month
        while (year, month) <= (end_year, end_month):
            months.append(f"{year}{month:02d}")
            month += 1
            if month > 12:
                month = 1
                year += 1
    else:
        print("Error: Must specify --month OR (--start AND --end)")
        return

    # Initialize scraper
    scraper = OptimizedChinaXivScraper(api_key, zone, rate_limit=args.rate_limit)

    # Phase 1: Get max IDs from homepage
    homepage_maxes = scraper.extract_homepage_max_ids()

    # Phase 2: Find missing max IDs via binary search
    all_max_ids = {}
    for year_month in months:
        if year_month in homepage_maxes:
            all_max_ids[year_month] = homepage_maxes[year_month]
            log(f"{year_month}: Using homepage max = {all_max_ids[year_month]:05d}")
        else:
            log(f"{year_month}: Homepage max not found, using binary search...")
            all_max_ids[year_month] = scraper.find_max_id_binary_search(year_month)

    # Phase 3: Scrape each month
    for year_month in months:
        log(f"\n{'=' * 60}")
        log(f"Processing {year_month}")
        log(f"{'=' * 60}")

        max_id = all_max_ids[year_month]

        # Load checkpoint if resuming
        checkpoint = None
        if args.resume:
            checkpoint = scraper._load_checkpoint(year_month)

        # Scrape month with known max
        papers = scraper.scrape_month_optimized(
            year_month, max_id, checkpoint=checkpoint
        )

        # Save results (unless dry-run)
        if not args.dry_run:
            scraper.save_results(year_month, papers)

        # Print stats
        log(f"\nStats for {year_month}:")
        log(f"  Total attempts: {scraper.stats['total_attempts']}")
        log(f"  Successful: {scraper.stats['successful_scrapes']}")
        log(f"  Failed: {scraper.stats['failed_scrapes']}")
        log(
            f"  Hit rate: {scraper.stats['successful_scrapes'] / max(1, scraper.stats['total_attempts']) * 100:.1f}%"
        )

        # Reset stats for next month
        scraper.stats = {
            "total_attempts": 0,
            "successful_scrapes": 0,
            "failed_scrapes": 0,
            "binary_search_requests": 0,
        }

    log("\nOptimized harvest complete!")


if __name__ == "__main__":
    run_cli()
