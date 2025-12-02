"""
On-demand paper metadata fetcher for ChinaXiv.

This module fetches the abstract page for a paper ID, parses the metadata
(title, authors, abstract, submission date, subjects, PDF URL), and produces a
record compatible with the translation pipeline. It does not rely on
pre-harvested records so it can be used to fill gaps when records are stale or
missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
import re

from bs4 import BeautifulSoup

from .http_client import http_get


@dataclass
class PaperMetadata:
    """Parsed metadata for a single paper."""

    paper_id: str
    title: str
    abstract: str
    creators: List[str]
    subjects: List[str]
    date_iso: str
    pdf_url: str
    source_url: str

    def to_record(self) -> Dict[str, Any]:
        """
        Convert metadata into a translation-ready record dict.

        Returns:
            Dict matching the structure expected by TranslationService.
        """

        return {
            "id": f"chinaxiv-{self.paper_id}",
            "oai_identifier": self.paper_id,
            "title": self.title,
            "abstract": self.abstract,
            "creators": self.creators,
            "subjects": self.subjects,
            "date": self.date_iso
            or f"{self.paper_id[:4]}-{self.paper_id[4:6]}-01T00:00:00Z",
            "source_url": self.source_url,
            "pdf_url": self.pdf_url,
            # License is intentionally permissive for translation purposes.
            "license": {"raw": "", "derivatives_allowed": None},
            "setSpec": None,
        }

    @property
    def referer(self) -> str:
        """Return the abstract page URL (useful for downloader warmups)."""

        return self.source_url


def _parse_date(date_str: str, paper_id: str) -> str:
    """
    Parse submission date string into ISO format.

    Falls back to first-of-month if parsing fails.
    """

    if not date_str:
        return f"{paper_id[:4]}-{paper_id[4:6]}-01T00:00:00Z"

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        return dt.isoformat() + "Z"
    except Exception:
        return f"{paper_id[:4]}-{paper_id[4:6]}-01T00:00:00Z"


def parse_metadata_from_html(html: str, paper_id: str) -> PaperMetadata:
    """
    Parse the ChinaXiv abstract page HTML into PaperMetadata.

    Args:
        html: Raw HTML content of the abstract page
        paper_id: Paper identifier without the "chinaxiv-" prefix

    Raises:
        ValueError: If required fields (title/PDF URL) are missing or invalid
    """

    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_elem = soup.find("h1")
    title = title_elem.get_text(strip=True) if title_elem else ""
    if not title or len(title) < 3:
        raise ValueError(f"Missing or too-short title for {paper_id}")

    # Authors
    author_links = soup.find_all("a", href=lambda x: x and "field=author" in x)
    creators = [
        link.get_text(strip=True)
        for link in author_links
        if link.get_text(strip=True)
    ]

    # Abstract - handles both "摘要：" and "摘要: " (space before colon)
    abstract = ""
    abstract_marker = soup.find("b", string=re.compile(r"摘要\s*[:：]"))
    if abstract_marker:
        parent = abstract_marker.parent
        if parent:
            full_text = parent.get_text(strip=False)
            match = re.search(r"摘要\s*[:：]\s*(.+)", full_text, re.DOTALL)
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
    date_iso = _parse_date(date_str, paper_id)

    # Category/subjects - try multiple sources:
    # 1. Links with field=domain, field=subject, or field=category
    # 2. Keywords links (field=keywords)
    subjects: List[str] = []

    # Try domain/subject/category links first
    subject_links = soup.find_all(
        "a",
        href=lambda x: x and any(
            f"field={f}" in x for f in ("domain", "subject", "category")
        ),
    )
    if subject_links:
        subjects = [
            link.get_text(strip=True)
            for link in subject_links
            if link.get_text(strip=True)
        ]

    # Fallback to keywords if no categories found
    if not subjects:
        keyword_links = soup.find_all(
            "a", href=lambda x: x and "field=keywords" in x
        )
        subjects = [
            link.get_text(strip=True)
            for link in keyword_links
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
    if not pdf_url:
        raise ValueError(f"Missing PDF URL for {paper_id}")

    source_url = f"https://chinaxiv.org/abs/{paper_id}"

    return PaperMetadata(
        paper_id=paper_id,
        title=title,
        abstract=abstract,
        creators=creators,
        subjects=subjects,
        date_iso=date_iso,
        pdf_url=pdf_url,
        source_url=source_url,
    )


def fetch_metadata_for_id(paper_id: str, *, timeout: tuple[int, int] = (10, 60)) -> PaperMetadata:
    """
    Fetch and parse metadata for a paper ID directly from ChinaXiv.

    Args:
        paper_id: Identifier (with or without the "chinaxiv-" prefix)
        timeout: HTTP timeout tuple (connect, read)

    Returns:
        PaperMetadata object populated from live HTML.
    """

    clean_id = paper_id.replace("chinaxiv-", "")
    url = f"https://chinaxiv.org/abs/{clean_id}"
    response = http_get(url, timeout=timeout)
    html = response.text
    return parse_metadata_from_html(html, clean_id)


__all__ = [
    "PaperMetadata",
    "fetch_metadata_for_id",
    "parse_metadata_from_html",
]
