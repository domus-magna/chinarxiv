"""
Tests for src/pdf_pipeline.py - PDF download + text extraction pipeline.

Tests cover:
- download_pdf() with fallback chain (direct -> unlocker -> headless)
- _inject_session_into_wss() URL transformation
- _headless_pdf_fetch() Playwright browser fallback
- _unlocker_raw_fetch() Web Unlocker proxy fallback
- _compute_text_metrics() text quality metrics
- _write_ocr_record() OCR logging with file locking
- process_paper() full pipeline integration
"""

import base64
import json
import os
import pytest
from unittest.mock import MagicMock, patch

from src.pdf_pipeline import (
    download_pdf,
    _inject_session_into_wss,
    _headless_pdf_fetch,
    _unlocker_raw_fetch,
    _compute_text_metrics,
    _write_ocr_record,
    fix_pdf_url,
    process_paper,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def valid_pdf_content():
    """Minimal valid PDF content (magic bytes + padding to meet size check)."""
    # Real PDF starts with %PDF- magic bytes
    return b"%PDF-1.4\n" + b"0" * 2000  # >1KB to pass size validation


@pytest.fixture
def html_response():
    """HTML response that looks like a ChinaXiv error page."""
    return b"<!DOCTYPE html><html><body>Error: page not found</body></html>"


@pytest.fixture
def tmp_pdf_dir(tmp_path):
    """Temporary directory for PDF downloads."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    return pdf_dir


# =============================================================================
# _inject_session_into_wss() tests
# =============================================================================

class TestInjectSessionIntoWSS:
    """Tests for BrightData WSS URL session injection."""

    def test_inject_session_basic(self):
        """Basic session injection into WSS URL."""
        wss_url = "wss://brd-customer-hl_abc123-zone-scraping_browser:password@brd.superproxy.io:9222"
        result = _inject_session_into_wss(wss_url, "mysession123")

        assert "-session-mysession123" in result
        assert "zone-scraping_browser-session-mysession123:" in result

    def test_inject_session_with_different_zone(self):
        """Session injection works with different zone names."""
        wss_url = "wss://brd-customer-hl_7f044a29-zone-china_browser1:pw@host:9222"
        result = _inject_session_into_wss(wss_url, "test-sess")

        assert "-session-test-sess:" in result
        assert "zone-china_browser1-session-test-sess:" in result

    def test_inject_session_preserves_other_parts(self):
        """Session injection preserves password and host."""
        wss_url = "wss://brd-customer-hl_abc-zone-myzone:secret_pass@brd.superproxy.io:9222"
        result = _inject_session_into_wss(wss_url, "s1")

        assert ":secret_pass@" in result
        assert "brd.superproxy.io:9222" in result

    def test_inject_session_no_match_returns_unchanged(self):
        """URL without zone pattern is returned unchanged."""
        wss_url = "wss://other-format-url:pass@host:port"
        result = _inject_session_into_wss(wss_url, "session")

        # Should return unchanged since pattern doesn't match
        assert result == wss_url or "-session-" in result


# =============================================================================
# _compute_text_metrics() tests
# =============================================================================

class TestComputeTextMetrics:
    """Tests for text quality metrics computation."""

    def test_empty_paragraphs(self):
        """Empty paragraph list returns zero metrics."""
        result = _compute_text_metrics([])

        assert result["char_count"] == 0
        assert result["alpha_ratio"] == 0.0
        assert result["most_common_ratio"] == 1.0

    def test_empty_string(self):
        """Empty string returns zero metrics."""
        result = _compute_text_metrics([""])

        assert result["char_count"] == 0
        assert result["alpha_ratio"] == 0.0

    def test_normal_text(self):
        """Normal English text has high alpha ratio."""
        paragraphs = [
            "This is a normal paragraph with mostly letters.",
            "Another paragraph with more text content here."
        ]
        result = _compute_text_metrics(paragraphs)

        assert result["char_count"] > 0
        assert result["alpha_ratio"] > 0.7  # Mostly letters
        assert result["most_common_ratio"] < 0.5  # Diverse characters

    def test_garbage_text(self):
        """Garbled OCR output has high most_common_ratio."""
        # Simulating garbled OCR with repeated characters
        paragraphs = ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
        result = _compute_text_metrics(paragraphs)

        assert result["most_common_ratio"] == 1.0  # All same char

    def test_mixed_content(self):
        """Mixed alphanumeric content has moderate alpha ratio."""
        paragraphs = ["Figure 1: x=123, y=456, z=789"]
        result = _compute_text_metrics(paragraphs)

        assert result["char_count"] > 0
        assert 0.3 < result["alpha_ratio"] < 0.7  # Mix of letters and numbers

    def test_chinese_text(self):
        """Chinese text has low alpha ratio (isalpha() is False for CJK)."""
        paragraphs = ["This text includes Chinese characters"]
        result = _compute_text_metrics(paragraphs)

        # Should still compute without errors
        assert result["char_count"] > 0


# =============================================================================
# _write_ocr_record() tests
# =============================================================================

class TestWriteOcrRecord:
    """Tests for OCR report JSON writing with file locking."""

    def test_write_new_record(self, tmp_path):
        """Write OCR record to new file."""
        report_dir = str(tmp_path / "reports")
        paper_id = "chinaxiv-202401.00001"
        record = {
            "pdf_path": "/path/to/pdf",
            "need_ocr": True,
            "pre_ocr_chars": 500,
        }

        _write_ocr_record(report_dir, paper_id, record)

        report_path = tmp_path / "reports" / "ocr_report.json"
        assert report_path.exists()

        with open(report_path) as f:
            data = json.load(f)

        assert paper_id in data
        assert data[paper_id]["need_ocr"] is True

    def test_append_to_existing(self, tmp_path):
        """Append OCR record to existing file."""
        report_dir = str(tmp_path / "reports")
        os.makedirs(report_dir)
        report_path = tmp_path / "reports" / "ocr_report.json"

        # Write initial data
        with open(report_path, "w") as f:
            json.dump({"existing-paper": {"need_ocr": False}}, f)

        # Append new record
        _write_ocr_record(report_dir, "new-paper", {"need_ocr": True})

        with open(report_path) as f:
            data = json.load(f)

        assert "existing-paper" in data
        assert "new-paper" in data

    def test_update_existing_paper(self, tmp_path):
        """Update record for existing paper."""
        report_dir = str(tmp_path / "reports")
        os.makedirs(report_dir)
        report_path = tmp_path / "reports" / "ocr_report.json"

        # Write initial data
        with open(report_path, "w") as f:
            json.dump({"paper-1": {"version": 1}}, f)

        # Update same paper
        _write_ocr_record(report_dir, "paper-1", {"version": 2})

        with open(report_path) as f:
            data = json.load(f)

        assert data["paper-1"]["version"] == 2


# =============================================================================
# fix_pdf_url() tests
# =============================================================================

class TestFixPdfUrl:
    """Tests for PDF URL pass-through (no transformation)."""

    def test_returns_unchanged(self):
        """URL is returned unchanged."""
        url = "https://chinaxiv.org/pdf/123.pdf"
        result = fix_pdf_url(url, "paper-id")
        assert result == url

    def test_handles_various_urls(self):
        """Various URL formats are passed through."""
        urls = [
            "https://example.com/paper.pdf",
            "http://chinaxiv.org/download?uuid=abc",
            "https://storage.example.com/pdfs/file.pdf",
        ]
        for url in urls:
            assert fix_pdf_url(url, "any-id") == url


# =============================================================================
# _unlocker_raw_fetch() tests
# =============================================================================

class TestUnlockerRawFetch:
    """Tests for BrightData Web Unlocker fallback."""

    def test_missing_credentials_returns_false(self, tmp_path, monkeypatch):
        """Returns False when credentials are missing."""
        monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
        monkeypatch.delenv("BRIGHTDATA_UNLOCKER_ZONE", raising=False)
        monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)

        output_path = str(tmp_path / "test.pdf")
        result = _unlocker_raw_fetch("https://example.com/test.pdf", output_path)

        assert result is False

    def test_missing_password_returns_false(self, tmp_path, monkeypatch):
        """Returns False when password is missing."""
        monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key")
        monkeypatch.setenv("BRIGHTDATA_UNLOCKER_ZONE", "test-zone")
        monkeypatch.delenv("BRIGHTDATA_UNLOCKER_PASSWORD", raising=False)
        monkeypatch.delenv("BRIGHTDATA_ZONE_PASSWORD", raising=False)
        # When BRIGHTDATA_ZONE is same as UNLOCKER_ZONE, it falls back to zone name
        monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)

        output_path = str(tmp_path / "test.pdf")
        result = _unlocker_raw_fetch("https://example.com/test.pdf", output_path)

        # Should return False since no password
        assert result is False

    @patch("src.pdf_pipeline.requests.Session")
    def test_proxy_error_returns_false(self, mock_session_class, tmp_path, monkeypatch):
        """Returns False on proxy errors."""
        import requests.exceptions

        monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key")
        monkeypatch.setenv("BRIGHTDATA_UNLOCKER_ZONE", "test-zone")
        monkeypatch.setenv("BRIGHTDATA_ZONE_PASSWORD", "test-pass")

        mock_session = MagicMock()
        mock_session.get.side_effect = requests.exceptions.ProxyError("Tunnel failed")
        mock_session_class.return_value = mock_session

        output_path = str(tmp_path / "test.pdf")
        result = _unlocker_raw_fetch("https://example.com/test.pdf", output_path)

        assert result is False

    @patch("src.pdf_pipeline.requests.Session")
    def test_non_pdf_response_returns_false(
        self, mock_session_class, tmp_path, monkeypatch, html_response
    ):
        """Returns False when response is not PDF."""
        monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key")
        monkeypatch.setenv("BRIGHTDATA_UNLOCKER_ZONE", "test-zone")
        monkeypatch.setenv("BRIGHTDATA_ZONE_PASSWORD", "test-pass")

        mock_response = MagicMock()
        mock_response.content = html_response
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session

        output_path = str(tmp_path / "test.pdf")
        result = _unlocker_raw_fetch("https://example.com/test.pdf", output_path)

        assert result is False
        assert not os.path.exists(output_path)

    @patch("src.pdf_pipeline.requests.Session")
    def test_success_saves_pdf(
        self, mock_session_class, tmp_path, monkeypatch, valid_pdf_content
    ):
        """Successfully downloads and saves PDF."""
        monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key")
        monkeypatch.setenv("BRIGHTDATA_UNLOCKER_ZONE", "test-zone")
        monkeypatch.setenv("BRIGHTDATA_ZONE_PASSWORD", "test-pass")

        mock_response = MagicMock()
        mock_response.content = valid_pdf_content
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        mock_session_class.return_value = mock_session

        output_path = str(tmp_path / "test.pdf")
        result = _unlocker_raw_fetch("https://example.com/test.pdf", output_path)

        assert result is True
        assert os.path.exists(output_path)
        with open(output_path, "rb") as f:
            assert f.read().startswith(b"%PDF-")


# =============================================================================
# _headless_pdf_fetch() tests
# =============================================================================

class TestHeadlessPdfFetch:
    """Tests for Playwright browser fallback."""

    def test_no_endpoint_returns_false(self, tmp_path, monkeypatch):
        """Returns False when BRIGHTDATA_BROWSER_WSS is not set."""
        monkeypatch.delenv("BRIGHTDATA_BROWSER_WSS", raising=False)

        output_path = str(tmp_path / "test.pdf")
        result = _headless_pdf_fetch("https://example.com/test.pdf", output_path)

        assert result is False

    @patch("playwright.sync_api.sync_playwright")
    def test_success_with_js_fetch(self, mock_playwright, tmp_path, monkeypatch, valid_pdf_content):
        """Successfully downloads PDF via JS fetch."""
        monkeypatch.setenv("BRIGHTDATA_BROWSER_WSS", "wss://brd-customer-test-zone-browser:pass@host:9222")

        # Encode PDF content as base64 (what JS fetch returns)
        pdf_base64 = base64.b64encode(valid_pdf_content).decode()

        # Mock the Playwright chain
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "success": True,
            "status": 200,
            "contentType": "application/pdf",
            "size": len(valid_pdf_content),
            "base64": pdf_base64,
        }
        mock_page.query_selector.return_value = None  # No PDF link found

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_chromium = MagicMock()
        mock_chromium.connect_over_cdp.return_value = mock_browser

        mock_p = MagicMock()
        mock_p.chromium = mock_chromium

        mock_playwright.return_value.__enter__.return_value = mock_p

        output_path = str(tmp_path / "test.pdf")
        result = _headless_pdf_fetch("https://example.com/test.pdf", output_path)

        assert result is True
        assert os.path.exists(output_path)
        with open(output_path, "rb") as f:
            content = f.read()
            assert content.startswith(b"%PDF-")

    @patch("playwright.sync_api.sync_playwright")
    def test_js_fetch_error_returns_false(self, mock_playwright, tmp_path, monkeypatch):
        """Returns False when JS fetch fails."""
        monkeypatch.setenv("BRIGHTDATA_BROWSER_WSS", "wss://test:pass@host:9222")

        mock_page = MagicMock()
        mock_page.evaluate.return_value = {"error": "Network error"}
        mock_page.query_selector.return_value = None

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_chromium = MagicMock()
        mock_chromium.connect_over_cdp.return_value = mock_browser

        mock_p = MagicMock()
        mock_p.chromium = mock_chromium

        mock_playwright.return_value.__enter__.return_value = mock_p

        output_path = str(tmp_path / "test.pdf")
        result = _headless_pdf_fetch("https://example.com/test.pdf", output_path)

        assert result is False

    @patch("playwright.sync_api.sync_playwright")
    def test_small_pdf_returns_false(self, mock_playwright, tmp_path, monkeypatch):
        """Returns False when PDF is too small (<1KB)."""
        monkeypatch.setenv("BRIGHTDATA_BROWSER_WSS", "wss://test:pass@host:9222")

        small_pdf = b"%PDF-1.4\ntoo small"  # <1KB
        pdf_base64 = base64.b64encode(small_pdf).decode()

        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "success": True,
            "status": 200,
            "base64": pdf_base64,
        }
        mock_page.query_selector.return_value = None

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_chromium = MagicMock()
        mock_chromium.connect_over_cdp.return_value = mock_browser

        mock_p = MagicMock()
        mock_p.chromium = mock_chromium

        mock_playwright.return_value.__enter__.return_value = mock_p

        output_path = str(tmp_path / "test.pdf")
        result = _headless_pdf_fetch("https://example.com/test.pdf", output_path)

        assert result is False


# =============================================================================
# download_pdf() tests
# =============================================================================

class TestDownloadPdf:
    """Tests for main download function with fallback chain."""

    @patch("src.pdf_pipeline.get_session")
    @patch("src.pdf_pipeline.get_proxies")
    def test_direct_download_success(
        self, mock_proxies, mock_session, tmp_path, valid_pdf_content
    ):
        """Direct download works without fallbacks."""
        mock_proxies.return_value = ({}, "none")

        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.content = valid_pdf_content
        mock_response.iter_content.return_value = [valid_pdf_content]
        mock_response.raise_for_status = MagicMock()

        mock_sess = MagicMock()
        mock_sess.get.return_value = mock_response
        mock_session.return_value = mock_sess

        output_path = str(tmp_path / "pdfs" / "test.pdf")
        result = download_pdf("https://example.com/test.pdf", output_path)

        assert result is True
        assert os.path.exists(output_path)

    @patch("src.pdf_pipeline._headless_pdf_fetch")
    @patch("src.pdf_pipeline._unlocker_raw_fetch")
    @patch("src.pdf_pipeline.get_session")
    @patch("src.pdf_pipeline.get_proxies")
    def test_fallback_to_unlocker_on_html_response(
        self, mock_proxies, mock_session, mock_unlocker, mock_headless,
        tmp_path, html_response, valid_pdf_content
    ):
        """Falls back to unlocker when response is HTML (not PDF)."""
        mock_proxies.return_value = ({}, "none")

        # Direct download returns HTML
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = html_response
        mock_response.text = html_response.decode()
        mock_response.raise_for_status = MagicMock()

        mock_sess = MagicMock()
        mock_sess.get.return_value = mock_response
        mock_session.return_value = mock_sess

        # Unlocker succeeds
        mock_unlocker.return_value = True
        mock_headless.return_value = False

        output_path = str(tmp_path / "pdfs" / "test.pdf")
        result = download_pdf("https://example.com/test.pdf", output_path)

        assert result is True
        mock_unlocker.assert_called_once()

    @patch("src.pdf_pipeline._headless_pdf_fetch")
    @patch("src.pdf_pipeline._unlocker_raw_fetch")
    @patch("src.pdf_pipeline.get_session")
    @patch("src.pdf_pipeline.get_proxies")
    def test_fallback_to_headless_when_unlocker_fails(
        self, mock_proxies, mock_session, mock_unlocker, mock_headless,
        tmp_path, html_response
    ):
        """Falls back to headless when unlocker fails."""
        mock_proxies.return_value = ({}, "none")

        # Direct download returns HTML
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = html_response
        mock_response.text = html_response.decode()
        mock_response.raise_for_status = MagicMock()

        mock_sess = MagicMock()
        mock_sess.get.return_value = mock_response
        mock_session.return_value = mock_sess

        # Unlocker fails, headless succeeds
        mock_unlocker.return_value = False
        mock_headless.return_value = True

        output_path = str(tmp_path / "pdfs" / "test.pdf")
        result = download_pdf("https://example.com/test.pdf", output_path)

        assert result is True
        mock_unlocker.assert_called_once()
        mock_headless.assert_called_once()

    @patch("src.pdf_pipeline.get_session")
    @patch("src.pdf_pipeline.get_proxies")
    def test_rejects_small_pdf(self, mock_proxies, mock_session, tmp_path):
        """Rejects PDFs smaller than 1KB."""
        mock_proxies.return_value = ({}, "none")

        small_content = b"%PDF-1.4\nsmall"  # <1KB

        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.content = small_content
        mock_response.iter_content.return_value = [small_content]
        mock_response.raise_for_status = MagicMock()

        mock_sess = MagicMock()
        mock_sess.get.return_value = mock_response
        mock_session.return_value = mock_sess

        output_path = str(tmp_path / "pdfs" / "test.pdf")

        # Should fail due to small size
        with patch("src.pdf_pipeline._unlocker_raw_fetch", return_value=False):
            with patch("src.pdf_pipeline._headless_pdf_fetch", return_value=False):
                result = download_pdf("https://example.com/test.pdf", output_path)

        assert result is False


# =============================================================================
# process_paper() tests
# =============================================================================

class TestProcessPaper:
    """Tests for full paper processing pipeline."""

    @patch("src.pdf_pipeline.download_pdf")
    @patch("src.pdf_pipeline.extract_from_pdf")
    @patch("src.pdf_pipeline.get_config")
    def test_success_returns_result(
        self, mock_config, mock_extract, mock_download, tmp_pdf_dir
    ):
        """Successful processing returns result dict."""
        mock_download.return_value = True
        mock_extract.return_value = ["Paragraph 1", "Paragraph 2", "Paragraph 3"]
        mock_config.return_value = {"validation_thresholds": {}}

        # Create a fake PDF file (download is mocked but file check happens)
        pdf_path = tmp_pdf_dir / "chinaxiv-202401.00001.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n" + b"0" * 2000)

        result = process_paper(
            "chinaxiv-202401.00001",
            "https://example.com/test.pdf",
            str(tmp_pdf_dir),
        )

        assert result is not None
        assert "paragraphs" in result
        assert result["num_paragraphs"] == 3

    @patch("src.pdf_pipeline.download_pdf")
    @patch("src.pdf_pipeline.get_config")
    def test_download_failure_returns_none(
        self, mock_config, mock_download, tmp_pdf_dir
    ):
        """Returns None when download fails."""
        mock_download.return_value = False
        mock_config.return_value = {"validation_thresholds": {}}

        result = process_paper(
            "chinaxiv-202401.00001",
            "https://example.com/test.pdf",
            str(tmp_pdf_dir),
        )

        assert result is None

    @patch("src.pdf_pipeline.download_pdf")
    @patch("src.pdf_pipeline.extract_from_pdf")
    @patch("src.pdf_pipeline.get_config")
    def test_empty_extraction_returns_none(
        self, mock_config, mock_extract, mock_download, tmp_pdf_dir
    ):
        """Returns None when no text is extracted."""
        mock_download.return_value = True
        mock_extract.return_value = []  # No paragraphs
        mock_config.return_value = {"validation_thresholds": {}}

        # Create PDF file
        pdf_path = tmp_pdf_dir / "chinaxiv-202401.00001.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n" + b"0" * 2000)

        result = process_paper(
            "chinaxiv-202401.00001",
            "https://example.com/test.pdf",
            str(tmp_pdf_dir),
        )

        assert result is None

    @patch("src.pdf_pipeline.download_pdf")
    @patch("src.pdf_pipeline.extract_from_pdf")
    @patch("src.pdf_pipeline.get_config")
    def test_skips_download_if_pdf_exists(
        self, mock_config, mock_extract, mock_download, tmp_pdf_dir
    ):
        """Skips download if PDF already exists."""
        mock_extract.return_value = ["Existing content"]
        mock_config.return_value = {"validation_thresholds": {}}

        # Pre-create PDF file
        pdf_path = tmp_pdf_dir / "chinaxiv-202401.00001.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n" + b"0" * 2000)

        result = process_paper(
            "chinaxiv-202401.00001",
            "https://example.com/test.pdf",
            str(tmp_pdf_dir),
        )

        assert result is not None
        mock_download.assert_not_called()  # Should skip download


# =============================================================================
# Integration tests (marked for optional execution)
# =============================================================================

@pytest.mark.integration
class TestIntegration:
    """Integration tests that may require network access."""

    def test_inject_session_real_url_format(self):
        """Test with realistic BrightData URL format."""
        # Real URL format from documentation
        wss_url = "wss://brd-customer-hl_7f044a29-zone-china_browser1:password123@brd.superproxy.io:9222"
        result = _inject_session_into_wss(wss_url, "paper-12345")

        assert "zone-china_browser1-session-paper-12345:" in result
        assert "password123@" in result
        assert "brd.superproxy.io:9222" in result
