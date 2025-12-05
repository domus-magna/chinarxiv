from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess

from .utils import log


def has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def md_to_pdf(md_path: str, pdf_path: str, pdf_engine: str | None = None) -> bool:
    """Convert markdown to PDF using pandoc.

    Returns True on success, False on failure. Logs error details for debugging.
    Uses XeLaTeX by default for full Unicode/CJK support.
    """
    try:
        # Default to xelatex for Unicode support (Chinese chars in author names, math symbols)
        engine = pdf_engine or "xelatex"
        cmd = ["pandoc", md_path, "-o", pdf_path, "--pdf-engine", engine, "--resource-path=."]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        stderr_snippet = (e.stderr[:500] + "...") if e.stderr and len(e.stderr) > 500 else (e.stderr or "no stderr")
        log(f"PDF generation failed for {md_path}: {stderr_snippet}")
        return False
    except FileNotFoundError as e:
        log(f"PDF tool not found: {e}")
        return False
    except Exception as e:
        log(f"Unexpected error generating PDF for {md_path}: {e}")
        return False


def run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate PDFs from rendered Markdown using pandoc if available."
    )
    parser.parse_args()

    if not has_binary("pandoc"):
        log("pandoc not found; skipping PDF generation")
        return

    pdf_engine = None  # Will default to xelatex in md_to_pdf
    if not has_binary("xelatex"):
        if has_binary("tectonic"):
            pdf_engine = "tectonic"
            log("xelatex missing; using tectonic via pandoc --pdf-engine")
        elif has_binary("pdflatex"):
            pdf_engine = "pdflatex"
            log("xelatex missing; falling back to pdflatex (Unicode chars may fail)")
        else:
            log("No LaTeX engine found (xelatex, tectonic, pdflatex); PDFs will fail")

    count = 0
    for md in glob.glob(os.path.join("site", "items", "*", "*.md")):
        base = os.path.splitext(md)[0]
        pdf_path = base + ".pdf"
        ok = md_to_pdf(md, pdf_path, pdf_engine=pdf_engine)
        if ok:
            count += 1
    log(f"Generated {count} PDFs")


if __name__ == "__main__":
    run_cli()
