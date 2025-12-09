"""
Translation data model for ChinaXiv English translation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .paper import Paper

# Pattern to match PARA tags: <PARA id="N">content</PARA>
_PARA_TAG_PATTERN = re.compile(r"<PARA[^>]*>(.*?)</PARA>", re.IGNORECASE | re.DOTALL)


@dataclass
class Translation:
    """Translation data model."""

    id: str
    oai_identifier: Optional[str] = None
    title_en: Optional[str] = None
    abstract_en: Optional[str] = None
    body_en: Optional[List[str]] = None
    creators: Optional[List[str]] = None
    creators_en: Optional[List[str]] = None
    subjects: Optional[List[str]] = None
    subjects_en: Optional[List[str]] = None
    date: Optional[str] = None
    license: Optional[Dict[str, Any]] = None
    source_url: Optional[str] = None
    pdf_url: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Translation:
        """Create Translation from dictionary."""
        return cls(
            id=data["id"],
            oai_identifier=data.get("oai_identifier"),
            title_en=data.get("title_en"),
            abstract_en=data.get("abstract_en"),
            body_en=data.get("body_en"),
            creators=data.get("creators"),
            creators_en=data.get("creators_en"),
            subjects=data.get("subjects"),
            subjects_en=data.get("subjects_en"),
            date=data.get("date"),
            license=data.get("license"),
            source_url=data.get("source_url"),
            pdf_url=data.get("pdf_url"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert Translation to dictionary."""
        result = {"id": self.id}

        if self.oai_identifier is not None:
            result["oai_identifier"] = self.oai_identifier
        if self.title_en is not None:
            result["title_en"] = self.title_en
        if self.abstract_en is not None:
            result["abstract_en"] = self.abstract_en
        result["body_en"] = self.body_en
        result["creators"] = self.creators
        result["creators_en"] = self.creators_en
        result["subjects"] = self.subjects
        result["subjects_en"] = self.subjects_en
        result["date"] = self.date
        result["license"] = self.license
        result["source_url"] = self.source_url
        result["pdf_url"] = self.pdf_url

        return result

    @classmethod
    def from_paper(cls, paper: Paper) -> Translation:
        """Create Translation from Paper."""
        return cls(
            id=paper.id,
            oai_identifier=paper.oai_identifier,
            creators=paper.creators,
            subjects=paper.subjects,
            date=paper.date,
            license=paper.license.to_dict() if paper.license else None,
            source_url=paper.source_url,
            pdf_url=paper.pdf_url,
        )

    def has_full_text(self) -> bool:
        """Check if translation includes full text."""
        return bool(self.body_en)

    def get_title(self) -> str:
        """Get English title, fallback to empty string."""
        return self.title_en or ""

    def get_abstract(self) -> str:
        """Get English abstract, fallback to empty string."""
        return self.abstract_en or ""

    def get_body_text(self) -> str:
        """Get body text as a single string."""
        if not self.body_en:
            return ""
        return "\n\n".join(self.body_en)

    def get_authors_string(self) -> str:
        """Get authors as a comma-separated string (prefers English)."""
        # Prefer English translation if available
        if self.creators_en:
            return ", ".join(self.creators_en)
        if not self.creators:
            return ""
        return ", ".join(self.creators)

    def get_subjects_string(self) -> str:
        """Get subjects as a comma-separated string (normalized, prefers English)."""
        from ..data_utils import normalize_subject

        # Prefer English translation if available
        subjects = self.subjects_en if self.subjects_en else self.subjects
        if not subjects:
            return ""
        return ", ".join(normalize_subject(s) for s in subjects if s)

    def is_derivatives_allowed(self) -> bool:
        """Check if derivatives are allowed based on license."""
        if not self.license:
            return False
        return bool(self.license.get("derivatives_allowed", False))

    @staticmethod
    def _strip_para_tags(text: str) -> str:
        """Remove PARA markup tags from text, keeping content."""
        if not text:
            return ""
        # Replace <PARA id="N">content</PARA> with just content
        result = _PARA_TAG_PATTERN.sub(r"\1", text)
        # Clean up any extra whitespace
        return " ".join(result.split())

    def get_search_index_entry(self) -> Dict[str, Any]:
        """Get search index entry for this translation (cleaned of markup)."""
        return {
            "id": self.id,
            "title": self._strip_para_tags(self.get_title()),
            "authors": self._strip_para_tags(self.get_authors_string()),
            "abstract": self._strip_para_tags(self.get_abstract()),
            "subjects": self._strip_para_tags(self.get_subjects_string()),
            "date": self.date or "",
            "pdf_url": self.pdf_url or "",  # Include PDF link for search results
        }
