import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from pdfminer.high_level import extract_text

from src.make_pdf import md_to_pdf, run_cli


def test_make_pdf_no_pandoc(monkeypatch):
    # Force has_binary to return False to skip
    monkeypatch.setattr("src.make_pdf.has_binary", lambda name: False)
    # Should not raise
    old_argv = sys.argv
    try:
        sys.argv = [old_argv[0]]
        run_cli()
    finally:
        sys.argv = old_argv


def _has_xelatex_and_cjk_fonts() -> bool:
    """Check if xelatex and CJK fonts are available for testing."""
    if not shutil.which("xelatex"):
        return False
    # Check for Noto Sans CJK SC specifically (Simplified Chinese variant)
    # fonts-noto-cjk includes SC, but we check the exact variant to avoid flakiness
    try:
        result = subprocess.run(
            ["fc-list", ":family", "Noto Sans CJK SC"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "Noto Sans CJK SC" in result.stdout
    except Exception:
        # fc-list not available (e.g., macOS without fontconfig)
        return False


@pytest.fixture
def require_cjk_fonts():
    """Skip test if xelatex + CJK fonts are not available."""
    if not _has_xelatex_and_cjk_fonts():
        pytest.skip("Requires xelatex + Noto Sans CJK SC font (install: fonts-noto-cjk)")


def test_cjk_author_rendering(require_cjk_fonts):
    """Verify Chinese author names render correctly in PDFs.

    This test ensures the fontspec + xeCJK packages are working and
    that CJK characters (like 周蕾, 李立统) are not rendered as blanks.
    """
    md_content = """\
---
header-includes:
  - \\usepackage{fontspec}
  - \\usepackage{xeCJK}
  - \\setCJKmainfont{Noto Sans CJK SC}
---

# Test Paper

**Authors:** 周蕾, 李立统, 王旭

This is a test document for CJK author name rendering.
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        md_path = Path(tmpdir) / "test_cjk.md"
        pdf_path = Path(tmpdir) / "test_cjk.pdf"

        md_path.write_text(md_content, encoding="utf-8")

        # Generate PDF
        success = md_to_pdf(str(md_path), str(pdf_path), pdf_engine="xelatex")
        assert success, "PDF generation failed"
        assert pdf_path.exists(), "PDF file not created"

        # Extract text and verify ALL Chinese author names are present
        # Require all 3 names to catch partial rendering regressions
        text = extract_text(str(pdf_path))
        missing = [name for name in ["周蕾", "李立统", "王旭"] if name not in text]
        assert not missing, (
            f"Chinese author names missing from PDF: {missing}. Extracted text:\n{text[:500]}"
        )
