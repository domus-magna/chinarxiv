#!/usr/bin/env python3
"""
Harvest Audit and Stabilization Tool

Audits the current harvest pipeline for issues and provides remediation.
"""

import argparse
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any

import requests
from bs4 import BeautifulSoup

from .utils import log, read_json, write_json
from .http_client import get_session


class HarvestAuditor:
    """Audit harvest pipeline for stability issues."""

    def __init__(self):
        self.aggregate_stats = {
            "total_records": 0,
            "valid_records": 0,
            "invalid_records": 0,
            "pdf_fetch_failures": 0,
            "pdf_success": 0,
            "schema_violations": 0,
            "duplicate_ids": 0,
        }
        self.aggregate_issues: Dict[str, List[str]] = defaultdict(list)

    def audit_records(self, records_path: str) -> Dict[str, Any]:
        """Audit harvested records for common issues."""
        log(f"Auditing records from {records_path}")

        if not os.path.exists(records_path):
            message = f"Records file not found: {records_path}"
            self.aggregate_issues["missing_file"].append(message)
            return {
                "status": "failed",
                "issues": {"missing_file": [message]},
            }

        try:
            records = read_json(records_path)
        except Exception as e:
            message = f"Failed to parse records: {e}"
            self.aggregate_issues["parse_error"].append(message)
            return {
                "status": "failed",
                "issues": {"parse_error": [message]},
            }

        total_records = len(records)
        local_stats = {
            "total_records": total_records,
            "valid_records": 0,
            "invalid_records": 0,
            "pdf_fetch_failures": 0,
            "pdf_success": 0,
            "schema_violations": 0,
            "duplicate_ids": 0,
        }
        local_issues: Dict[str, List[str]] = defaultdict(list)
        local_results: Dict[str, Dict[str, Any]] = {}
        seen_ids = set()

        for idx, record in enumerate(records):
            record_id = record.get("id")
            entry_key = record_id or f"idx_{idx}"
            entry_data = {
                "schema": False,
                "pdf_ok": False,
                "resolved_pdf_url": None,
                "issues": [],
            }
            if record_id in seen_ids:
                local_stats["duplicate_ids"] += 1
                msg = f"Duplicate ID: {record_id}"
                local_issues["duplicates"].append(msg)
                entry_data["issues"].append(msg)
            else:
                if record_id:
                    seen_ids.add(record_id)

            if self._validate_record_schema(record, local_issues, entry_data["issues"]):
                local_stats["valid_records"] += 1
                entry_data["schema"] = True
            else:
                local_stats["invalid_records"] += 1
                local_stats["schema_violations"] += 1

            pdf_url = record.get("pdf_url")
            pdf_ok = False
            if pdf_url:
                if pdf_url.lower().startswith("https://chinaxiv.org/user/download.htm"):
                    alt_url = self._discover_pdf_redirect(record)
                    if alt_url:
                        record["resolved_pdf_url"] = alt_url
                        entry_data["resolved_pdf_url"] = alt_url
                        pdf_ok = self._check_pdf_accessibility(alt_url)
                if not pdf_ok:
                    pdf_ok = self._check_pdf_accessibility(pdf_url)

            if pdf_url and not pdf_ok:
                local_stats["pdf_fetch_failures"] += 1
                msg = f"PDF inaccessible: {pdf_url}"
                local_issues["pdf_failures"].append(msg)
                entry_data["issues"].append(msg)
            elif pdf_url and pdf_ok:
                local_stats["pdf_success"] += 1
                entry_data["pdf_ok"] = True
            elif not pdf_url:
                msg = "Missing pdf_url in record"
                local_issues["schema"].append(msg)
                entry_data["issues"].append(msg)

            local_results[entry_key] = entry_data

        self._update_aggregates(local_stats, local_issues)

        return {
            "status": "completed",
            "stats": local_stats,
            "issues": {k: list(v) for k, v in local_issues.items()},
            "summary": self._generate_summary(local_stats, local_issues),
            "records": local_results,
        }

    def _validate_record_schema(
        self,
        record: Dict[str, Any],
        issues: Dict[str, List[str]],
        record_issues: List[str],
    ) -> bool:
        """Validate record against expected schema."""
        required_fields = ["id", "title", "abstract", "creators", "subjects", "date", "source_url"]
        
        for field in required_fields:
            if field not in record or not record[field]:
                msg = f"Missing {field} in record {record.get('id', 'unknown')}"
                issues["schema"].append(msg)
                record_issues.append(msg)
                return False
        
        # Validate ID format
        record_id = record["id"]
        if not re.match(r"^chinaxiv-\d{6}\.\d{5}$", record_id):
            msg = f"Invalid ID format: {record_id}"
            issues["schema"].append(msg)
            record_issues.append(msg)
            return False
        
        # Validate creators is a list
        if not isinstance(record["creators"], list) or not record["creators"]:
            msg = f"Invalid creators format in {record_id}"
            issues["schema"].append(msg)
            record_issues.append(msg)
            return False
        
        # Validate subjects is a list
        if not isinstance(record["subjects"], list) or not record["subjects"]:
            msg = f"Invalid subjects format in {record_id}"
            issues["schema"].append(msg)
            record_issues.append(msg)
            return False
        
        return True
    
    def _check_pdf_accessibility(self, pdf_url: str) -> bool:
        """Check if PDF URL is accessible by streaming a small portion."""
        try:
            session = get_session()
            resp = session.get(
                pdf_url,
                timeout=30,
                stream=True,
                allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (harvest-audit)"},
            )
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "").lower()
            if "pdf" not in content_type:
                # Attempt to confirm via magic bytes
                chunk = next(resp.iter_content(chunk_size=1024), b"")
                return chunk.startswith(b"%PDF-")

            # Drain minimal content to ensure stream works then close
            next(resp.iter_content(chunk_size=1024), b"")
            return True
        except StopIteration:
            return False
        except Exception:
            return False

    def _discover_pdf_redirect(self, record: Dict[str, Any]) -> Optional[str]:
        """Attempt to discover real PDF URL from the landing page."""
        source_url = record.get("source_url")
        if not source_url:
            return None

        try:
            session = get_session()
            response = session.get(source_url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            pdf_link = soup.find("a", href=re.compile(r"\.pdf($|\?)"))
            if pdf_link and pdf_link.get("href"):
                href = pdf_link["href"]
                if not href.startswith("http"):
                    return requests.compat.urljoin(source_url, href)
                return href
        except Exception as e:
            log(f"Failed to discover PDF redirect for {record.get('id')}: {e}")
        return None
    
    def _generate_summary(
        self, stats: Dict[str, int], issues: Dict[str, List[str]]
    ) -> Dict[str, Any]:
        """Generate audit summary for a single records file."""
        total = stats["total_records"]
        if total == 0:
            return {"status": "no_data", "message": "No records to audit"}
        
        valid_pct = (stats["valid_records"] / total) * 100 if total else 0
        pdf_failure_pct = (
            (stats["pdf_fetch_failures"] / total) * 100 if total else 0
        )
        
        return {
            "total_records": total,
            "valid_percentage": round(valid_pct, 2),
            "pdf_failure_percentage": round(pdf_failure_pct, 2),
            "critical_issues": len(issues.get("schema", []))
            + len(issues.get("duplicates", [])),
            "recommendations": self._get_recommendations(stats, issues),
        }
    
    def _get_recommendations(
        self, stats: Dict[str, int], issues: Dict[str, List[str]]
    ) -> List[str]:
        """Generate recommendations based on audit findings."""
        recommendations = []
        
        if stats["schema_violations"] > 0:
            recommendations.append("Fix schema validation in harvest process")
        
        if stats["duplicate_ids"] > 0:
            recommendations.append("Implement deduplication in harvest process")
        
        if stats["pdf_fetch_failures"] > stats["total_records"] * 0.1:
            recommendations.append("Investigate PDF URL generation and accessibility")
        
        if len(issues.get("pdf_failures", [])) > 0:
            recommendations.append("Add retry logic for PDF downloads")
        
        if not recommendations:
            recommendations.append("Harvest pipeline appears stable")
        
        return recommendations

    def _update_aggregates(
        self, stats: Dict[str, int], issues: Dict[str, List[str]]
    ) -> None:
        """Update aggregate stats and issues for overall summary."""
        for key, value in stats.items():
            self.aggregate_stats[key] += value

        for key, value in issues.items():
            self.aggregate_issues[key].extend(value)


def audit_harvest_stability(records_path: Optional[str] = None) -> Dict[str, Any]:
    """Audit harvest stability across all record files."""
    auditor = HarvestAuditor()
    
    if records_path:
        return auditor.audit_records(records_path)
    
    # Audit all record files
    records_dir = Path("data/records")
    if not records_dir.exists():
        return {"status": "failed", "message": "No records directory found"}
    
    all_results = {}
    for record_file in records_dir.glob("chinaxiv_*.json"):
        result = auditor.audit_records(str(record_file))
        all_results[record_file.name] = result
    
    return {
        "status": "completed",
        "files_audited": len(all_results),
        "results": all_results,
        "overall_summary": auditor._generate_summary(
            auditor.aggregate_stats, auditor.aggregate_issues
        ),
    }


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Audit harvest pipeline stability")
    parser.add_argument("--records", help="Specific records file to audit")
    parser.add_argument("--output", default="reports/harvest_audit.json", help="Output file for audit report")
    
    args = parser.parse_args()
    
    log("Starting harvest audit...")
    result = audit_harvest_stability(args.records)
    
    # Write audit report
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    write_json(args.output, result)
    
    log(f"Audit complete. Report saved to {args.output}")
    
    # Print summary
    if "overall_summary" in result:
        summary = result["overall_summary"]
        print("\nHarvest Audit Summary:")
        print(f"  Total records: {summary.get('total_records', 0)}")
        print(f"  Valid percentage: {summary.get('valid_percentage', 0)}%")
        print(f"  PDF failure rate: {summary.get('pdf_failure_percentage', 0)}%")
        print(f"  Critical issues: {summary.get('critical_issues', 0)}")
        print(f"  Recommendations: {', '.join(summary.get('recommendations', []))}")


if __name__ == "__main__":
    run_cli()
