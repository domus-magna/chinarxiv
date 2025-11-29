"""
Paper data model for ChinaXiv English translation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .license import License


@dataclass
class Paper:
    """Paper data model."""

    id: str
    oai_identifier: Optional[str] = None
    title: Optional[str] = None
    creators: Optional[List[str]] = None
    abstract: Optional[str] = None
    subjects: Optional[List[str]] = None
    date: Optional[str] = None
    pdf_url: Optional[str] = None
    source_url: Optional[str] = None
    license: Optional[License] = None
    set_spec: Optional[str] = None
    files: Optional[Dict[str, str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Paper:
        """Create Paper from dictionary."""
        license_data = data.get("license")
        license_obj = License.from_dict(license_data) if license_data else None

        return cls(
            id=data["id"],
            oai_identifier=data.get("oai_identifier"),
            title=data.get("title"),
            creators=data.get("creators"),
            abstract=data.get("abstract"),
            subjects=data.get("subjects"),
            date=data.get("date"),
            pdf_url=data.get("pdf_url"),
            source_url=data.get("source_url"),
            license=license_obj,
            set_spec=data.get("setSpec") or data.get("set_spec"),
            files=data.get("files"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert Paper to dictionary."""
        result = {"id": self.id}

        if self.oai_identifier is not None:
            result["oai_identifier"] = self.oai_identifier
        if self.title is not None:
            result["title"] = self.title
        if self.creators is not None:
            result["creators"] = self.creators
        if self.abstract is not None:
            result["abstract"] = self.abstract
        if self.subjects is not None:
            result["subjects"] = self.subjects
        if self.date is not None:
            result["date"] = self.date
        if self.pdf_url is not None:
            result["pdf_url"] = self.pdf_url
        if self.source_url is not None:
            result["source_url"] = self.source_url
        if self.license is not None:
            result["license"] = self.license.to_dict()
        if self.set_spec is not None:
            result["setSpec"] = self.set_spec
        if self.files is not None:
            result["files"] = self.files

        return result

    def has_pdf(self) -> bool:
        """Check if paper has a PDF URL."""
        return bool(self.pdf_url)

    def has_latex_source(self) -> bool:
        """Check if paper has LaTeX source files."""
        return bool(
            self.files
            and any(
                filename.endswith((".tex", ".tar.gz", ".zip"))
                for filename in self.files
            )
        )

    def get_authors_string(self) -> str:
        """Get authors as a comma-separated string."""
        if not self.creators:
            return ""
        return ", ".join(self.creators)

    def get_subjects_string(self) -> str:
        """Get subjects as a comma-separated string."""
        if not self.subjects:
            return ""
        return ", ".join(self.subjects)

    def is_derivatives_allowed(self) -> bool:
        """DISABLED: We do not care about licenses. Always return True."""
        # DISABLED: We do not care about licenses. All papers translated in full.
        return True
        # if not self.license:
        #     return False
        # return self.license.is_derivatives_allowed()
