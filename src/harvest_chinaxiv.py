#!/usr/bin/env python3
"""
Direct ChinaXiv scraping via BrightData (default harvester).

Harvest fresh papers from ChinaXiv using BrightData Web Unlocker.
Scrapes papers by sequential ID probing for specified month ranges.
Outputs normalized JSON for integration with the translation pipeline.
"""

import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from .utils import log, read_json, write_json


class ChinaXivScraper:
    """Scraper for ChinaXiv papers using BrightData Web Unlocker."""

    def __init__(self, api_key: str, zone: str, rate_limit: float = 0.5):
        """
        Initialize scraper.

        Args:
            api_key: BrightData API key
            zone: BrightData zone name
            rate_limit: Seconds between requests (default: 0.5 = 2 req/sec)
        """
        self.api_key = api_key
        self.zone = zone
        self.rate_limit = rate_limit
        self.base_url = "https://chinaxiv.org/abs"
        self.api_url = "https://api.brightdata.com/request"

        self.stats = {
            "total_attempts": 0,
            "successful_scrapes": 0,
            "failed_scrapes": 0,
            "consecutive_404s": 0,
        }

    def fetch_page(self, paper_id: str) -> Optional[str]:
        """
        Fetch paper page HTML using BrightData.

        Args:
            paper_id: Paper ID (e.g., "202504.00123")

        Returns:
            HTML content if successful, None if 404 or error
        """
        url = f"{self.base_url}/{paper_id}"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {"zone": self.zone, "url": url, "format": "raw"}

        try:
            response = requests.post(
                self.api_url, headers=headers, json=payload, timeout=60
            )

            self.stats["total_attempts"] += 1

            if response.status_code == 200:
                html = response.text

                # Check for error responses from ChinaXiv
                if "<ErrorResponseData>" in html or len(html) < 1000:
                    self.stats["consecutive_404s"] += 1
                    return None

                self.stats["consecutive_404s"] = 0
                return html

            else:
                log(
                    f"BrightData error {response.status_code} for {paper_id}: {response.text[:200]}"
                )
                self.stats["consecutive_404s"] += 1
                return None

        except Exception as e:
            log(f"Exception fetching {paper_id}: {e}")
            self.stats["consecutive_404s"] += 1
            return None

    def parse_paper(self, html: str, paper_id: str) -> Optional[Dict]:
        """
        Parse paper metadata from HTML.

        Args:
            html: Page HTML content
            paper_id: Paper ID (e.g., "202504.00123")

        Returns:
            Paper metadata dict, or None if parsing fails
        """
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Title
            title_elem = soup.find("h1")
            title = title_elem.get_text(strip=True) if title_elem else ""

            if not title or len(title) < 10:
                log(f"Invalid title for {paper_id}: '{title}'")
                return None

            # Authors
            author_links = soup.find_all("a", href=lambda x: x and "field=author" in x)
            creators = [
                link.get_text(strip=True)
                for link in author_links
                if link.get_text(strip=True)
            ]

            # Abstract (find text after "摘要:")
            abstract = ""
            abstract_marker = soup.find("b", string=re.compile(r"摘要[:：]"))
            if abstract_marker:
                # Get parent element and extract text
                parent = abstract_marker.parent
                if parent:
                    full_text = parent.get_text(strip=False)
                    # Extract text after marker
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

            # Try to parse date
            date_iso = ""
            if date_str:
                try:
                    # Parse format: "2025-03-29 22:43:15"
                    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    date_iso = dt.isoformat() + "Z"
                except Exception:
                    # Use paper ID to infer date (YYYYMM)
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
        """
        Fetch and parse a single paper.

        Args:
            paper_id: Paper ID (e.g., "202504.00123")

        Returns:
            Paper metadata dict, or None if failed
        """
        # Rate limiting
        time.sleep(self.rate_limit)

        html = self.fetch_page(paper_id)
        if not html:
            return None

        paper = self.parse_paper(html, paper_id)
        if paper:
            self.stats["successful_scrapes"] += 1
            return paper
        else:
            self.stats["failed_scrapes"] += 1
            return None

    def scrape_month(
        self,
        year_month: str,
        checkpoint: Optional[Dict] = None,
        max_consecutive_404s: int = 50,
    ) -> List[Dict]:
        """
        Scrape all papers for a given month.

        Args:
            year_month: Month to scrape (e.g., "202504")
            checkpoint: Optional checkpoint to resume from
            max_consecutive_404s: Stop after this many consecutive 404s

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

        log(f"Scraping {year_month} starting at ID #{start_num:05d}")

        for num in range(start_num, 100000):  # Max 99,999 papers per month
            paper_id = f"{year_month}.{num:05d}"

            paper = self.scrape_paper(paper_id)

            if paper:
                papers.append(paper)
                log(f"✓ {paper_id}: {paper['title'][:60]}...")

                # Save checkpoint every 10 papers
                if len(papers) % 10 == 0:
                    self._save_checkpoint(year_month, papers, num)

            else:
                # Check if we should stop
                if self.stats["consecutive_404s"] >= max_consecutive_404s:
                    log(
                        f"Stopping {year_month}: {max_consecutive_404s} consecutive 404s"
                    )
                    break

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

        checkpoint_path = str(checkpoint_dir / f"chinaxiv_{year_month}.json")
        write_json(checkpoint_path, checkpoint)

    def _load_checkpoint(self, year_month: str) -> Optional[Dict]:
        """Load checkpoint if exists."""
        checkpoint_path = Path("data/checkpoints") / f"chinaxiv_{year_month}.json"
        if checkpoint_path.exists():
            return read_json(checkpoint_path)
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
    parser = argparse.ArgumentParser(
        description="Harvest fresh ChinaXiv papers via BrightData"
    )
    parser.add_argument("--start", help="Start month (YYYYMM)")
    parser.add_argument("--end", help="End month (YYYYMM)")
    parser.add_argument("--month", help="Single month to scrape (YYYYMM)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument(
        "--dry-run", action="store_true", help="Test mode (scrape but don't save)"
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0.5,
        help="Seconds between requests (default: 0.5)",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Process months in reverse order (most recent first)",
    )
    args = parser.parse_args()

    # Load environment
    load_dotenv()
    api_key = os.getenv("BRIGHTDATA_API_KEY")
    zone = os.getenv("BRIGHTDATA_ZONE")

    if not api_key or not zone:
        print("Error: BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE must be set in .env")
        return

    # Determine months to scrape
    months = []
    if args.month:
        months = [args.month]
    elif args.start and args.end:
        # Generate month range
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

    # Reverse order if requested (most recent first)
    if args.reverse:
        months.reverse()

    # Initialize scraper
    scraper = ChinaXivScraper(api_key, zone, rate_limit=args.rate_limit)

    # Scrape each month
    for year_month in months:
        log(f"\n{'=' * 60}")
        log(f"Processing {year_month}")
        log(f"{'=' * 60}")

        # Load checkpoint if resuming
        checkpoint = None
        if args.resume:
            checkpoint = scraper._load_checkpoint(year_month)

        # Scrape month
        papers = scraper.scrape_month(year_month, checkpoint=checkpoint)

        # Save results (unless dry-run)
        if not args.dry_run:
            scraper.save_results(year_month, papers)

        # Print stats
        log(f"\nStats for {year_month}:")
        log(f"  Total attempts: {scraper.stats['total_attempts']}")
        log(f"  Successful: {scraper.stats['successful_scrapes']}")
        log(f"  Failed: {scraper.stats['failed_scrapes']}")

        # Reset stats for next month
        scraper.stats = {
            "total_attempts": 0,
            "successful_scrapes": 0,
            "failed_scrapes": 0,
            "consecutive_404s": 0,
        }

    log("\nHarvest complete!")


if __name__ == "__main__":
    run_cli()
