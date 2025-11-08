#!/usr/bin/env python3
"""
Direct ChinaXiv scraping (smart mode).

Smart ChinaXiv harvester using pre-analyzed max IDs for recent months
to limit probing. Suitable for fast, low-cost incremental harvests.
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

from .utils import log, write_json


# Known/estimated max IDs per month (based on IA data + homepage)
DEFAULT_MAX_IDS = {
    "202504": 400,  # April: IA showed ~382
    "202505": 350,  # May: IA showed ~317
    "202506": 150,  # June: IA showed ~109
    "202507": 400,  # July: Homepage shows 354, use buffer
    "202508": 450,  # Aug: Homepage shows 418, use buffer
    "202509": 300,  # Sept: Homepage shows 257, use buffer
    "202510": 50,  # Oct: Only 1 on homepage, but use buffer
}


class SmartChinaXivScraper:
    """Pragmatic scraper using pre-analyzed max IDs."""

    def __init__(self, api_key: str, zone: str, rate_limit: float = 0.5):
        self.api_key = api_key
        self.zone = zone
        self.rate_limit = rate_limit
        self.base_url = "https://chinaxiv.org/abs"
        self.api_url = "https://api.brightdata.com/request"

        self.stats = {"total_attempts": 0, "successful_scrapes": 0, "failed_scrapes": 0}

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch page HTML using BrightData."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {"zone": self.zone, "url": url, "format": "raw"}

        try:
            time.sleep(self.rate_limit)

            response = requests.post(
                self.api_url, headers=headers, json=payload, timeout=60
            )

            self.stats["total_attempts"] += 1

            if response.status_code == 200:
                html = response.text
                if "<ErrorResponseData>" in html or len(html) < 1000:
                    return None
                return html

            return None

        except Exception as e:
            log(f"Fetch exception: {e}")
            return None

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
                    pass

            if not date_iso:
                year = paper_id[:4]
                month = paper_id[4:6]
                date_iso = f"{year}-{month}-01T00:00:00Z"

            # Subjects
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
                pdf_url = (
                    f"https://chinaxiv.org{href}" if href.startswith("/") else href
                )

            # Build record
            record = {
                "id": f"chinaxiv-{paper_id}",
                "oai_identifier": paper_id,
                "title": title,
                "abstract": abstract,
                "creators": creators,
                "subjects": subjects,
                "date": date_iso,
                "source_url": f"https://chinaxiv.org/abs/{paper_id}",
                "pdf_url": pdf_url,
                "license": {"raw": "", "derivatives_allowed": None},
                "setSpec": None,
            }

            return record

        except Exception as e:
            log(f"Parse error for {paper_id}: {e}")
            return None

    def scrape_month(self, year_month: str, max_id: int) -> List[Dict]:
        """Scrape papers for a month."""
        papers = []

        log(f"Scraping {year_month} from #00001 to #{max_id:05d}")

        for num in range(1, max_id + 1):
            paper_id = f"{year_month}.{num:05d}"
            url = f"{self.base_url}/{paper_id}"

            html = self.fetch_page(url)
            if not html:
                continue

            paper = self.parse_paper(html, paper_id)
            if paper:
                papers.append(paper)
                self.stats["successful_scrapes"] += 1
                log(f"✓ {paper_id}: {paper['title'][:60]}...")
            else:
                self.stats["failed_scrapes"] += 1

        log(f"Finished {year_month}: {len(papers)} papers")
        return papers

    def save_results(self, year_month: str, papers: List[Dict]):
        """Save results to JSON."""
        output_dir = Path("data/records")
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = str(output_dir / f"chinaxiv_{year_month}.json")
        write_json(output_path, papers)
        log(f"Saved {len(papers)} papers to {output_path}")


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Smart ChinaXiv harvester")
    parser.add_argument("--start", help="Start month (YYYYMM)")
    parser.add_argument("--end", help="End month (YYYYMM)")
    parser.add_argument("--month", help="Single month")
    parser.add_argument("--rate-limit", type=float, default=0.5)
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("BRIGHTDATA_API_KEY")
    zone = os.getenv("BRIGHTDATA_ZONE")

    if not api_key or not zone:
        print("Error: BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE required")
        return

    # Determine months
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
        print("Error: Specify --month OR (--start AND --end)")
        return

    scraper = SmartChinaXivScraper(api_key, zone, args.rate_limit)

    # Scrape each month
    for year_month in months:
        max_id = DEFAULT_MAX_IDS.get(year_month, 500)

        log(f"\n{'=' * 60}")
        log(f"Processing {year_month} (max ID: {max_id})")
        log(f"{'=' * 60}")

        papers = scraper.scrape_month(year_month, max_id)
        scraper.save_results(year_month, papers)

        # Stats
        log(f"\nStats for {year_month}:")
        log(f"  Total attempts: {scraper.stats['total_attempts']}")
        log(f"  Successful: {scraper.stats['successful_scrapes']}")
        log(
            f"  Hit rate: {scraper.stats['successful_scrapes'] / max(1, scraper.stats['total_attempts']) * 100:.1f}%"
        )

        # Reset
        scraper.stats = {
            "total_attempts": 0,
            "successful_scrapes": 0,
            "failed_scrapes": 0,
        }

    log("\nHarvest complete!")


if __name__ == "__main__":
    run_cli()
