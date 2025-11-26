from __future__ import annotations

import os
import re
import tarfile
import zipfile
from typing import Any, Dict, List, Optional

from .utils import log


# =============================================================================
# SYNTHESIS MODE: Watermark Detection
# =============================================================================

# Known watermark patterns to filter from PDF extraction
WATERMARK_PATTERNS = [
    # Spaced-out letters (common PDF artifact): "X a n i h C", "v i X a n i h C"
    re.compile(r"^[A-Za-z0-9]\s+[A-Za-z0-9]\s+[A-Za-z0-9]\s+[A-Za-z0-9]"),
    # ChinaXiv watermarks (reversed/spaced)
    re.compile(r"X\s*a\s*n\s*i\s*h\s*C", re.IGNORECASE),
    # "This version posted" spaced out
    re.compile(r"T\s+h\s+i\s+s\s+v\s+e\s+r\s+s\s+i\s+o\s+n", re.IGNORECASE),
    re.compile(r"p\s+o\s+s\s+t\s+e\s+d", re.IGNORECASE),
    # Version stamps with excessive spacing: "1 v 1 0 0 0 0 . 9 0 5 2 0 2"
    re.compile(r"\d\s+v\s+\d"),
    # arXiv-style identifiers with spacing
    re.compile(r":\s*v\s*i\s*X\s*a\s*n\s*i\s*h\s*C", re.IGNORECASE),
]


def is_watermark_or_garbage(text: str) -> bool:
    """
    Detect PDF artifacts that should be filtered out.

    Returns True if text appears to be a watermark, header artifact,
    or other garbage that shouldn't be translated.
    """
    text = text.strip()

    # Too short to be meaningful content
    if len(text) < 5:
        return True

    # Mostly whitespace with spaced-out characters (common watermark pattern)
    non_space = text.replace(" ", "")
    if len(non_space) > 0:
        space_ratio = text.count(" ") / len(text)
        # High space ratio + starts with letter = likely spaced-out watermark
        if space_ratio > 0.4 and re.match(r"^[A-Za-z0-9]", text):
            return True

    # Match known watermark patterns
    for pattern in WATERMARK_PATTERNS:
        if pattern.search(text):
            return True

    # Pure punctuation or numbers (page numbers, etc.)
    if re.match(r"^[\d\s\.\-–—]+$", text):
        return True

    return False


# =============================================================================
# SYNTHESIS MODE: Section Detection
# =============================================================================

# Section heading patterns (Chinese and English)
SECTION_PATTERNS = [
    # Numbered sections: "1 Introduction", "2.1 Methods", "1. 引言"
    re.compile(r"^(\d+\.?\d*\.?\d*)\s+([A-Z\u4e00-\u9fff])"),
    # Chinese chapter markers: "第一章", "第1节"
    re.compile(r"^第[一二三四五六七八九十\d]+[章节部分]\s*"),
    # Common Chinese section names (standalone or numbered)
    re.compile(
        r"^\d*\.?\s*(摘要|引言|介绍|背景|方法|方法论|结果|讨论|结论|"
        r"参考文献|致谢|附录|实验|分析|总结)",
        re.IGNORECASE,
    ),
    # English standalone headers
    re.compile(
        r"^(Abstract|Introduction|Background|Methods?|Methodology|"
        r"Results?|Discussion|Conclusions?|References|"
        r"Acknowledgm?ents?|Appendix|Experiment|Analysis|Summary)$",
        re.IGNORECASE,
    ),
]


def detect_section_boundary(text: str) -> Optional[str]:
    """
    Detect if text is a section heading.

    Returns:
        The section name if detected, None otherwise.
    """
    text = text.strip()

    # Section headings are typically short
    if len(text) > 100:
        return None

    for pattern in SECTION_PATTERNS:
        if pattern.match(text):
            return text

    return None


# =============================================================================
# SYNTHESIS MODE: Intelligent Paragraph Merging
# =============================================================================


def merge_pdf_lines_to_paragraphs(
    lines: List[str], min_para_length: int = 50
) -> List[str]:
    """
    Merge fragmented PDF lines into logical paragraphs.

    PDF extraction often creates one "paragraph" per visual line. This function
    merges them back into logical paragraphs based on:
    - Section headings (start new paragraphs)
    - Sentence endings followed by uppercase (natural paragraph breaks)
    - Very short lines that don't end sentences (continuation)

    Args:
        lines: Raw lines from PDF extraction
        min_para_length: Minimum characters to consider a line a complete paragraph

    Returns:
        List of merged paragraphs
    """
    if not lines:
        return []

    paragraphs = []
    current_buffer: List[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Skip garbage/watermarks
        if is_watermark_or_garbage(line):
            continue

        # Section heading starts new paragraph
        section = detect_section_boundary(line)
        if section:
            # Flush current buffer
            if current_buffer:
                paragraphs.append(" ".join(current_buffer))
                current_buffer = []
            # Section heading as its own paragraph
            paragraphs.append(line)
            continue

        # Check if this continues previous content or starts new
        if current_buffer:
            last_text = " ".join(current_buffer)
            # Does previous buffer end a sentence?
            ends_sentence = bool(re.search(r"[.?!。！？:：]\s*$", last_text))
            # Does this line start a new sentence (uppercase or Chinese)?
            starts_new = bool(re.match(r"^[A-Z\u4e00-\u9fff]", line))

            if ends_sentence and starts_new and len(last_text) >= min_para_length:
                # Complete paragraph break
                paragraphs.append(last_text)
                current_buffer = [line]
            else:
                # Continue building current paragraph
                current_buffer.append(line)
        else:
            current_buffer = [line]

    # Flush remaining buffer
    if current_buffer:
        paragraphs.append(" ".join(current_buffer))

    return paragraphs


# =============================================================================
# SYNTHESIS MODE: Main Extraction Function
# =============================================================================


def extract_from_pdf_synthesis(pdf_path: str) -> Optional[Dict[str, Any]]:
    """
    Extract text with intelligent merging for synthesis translation mode.

    Unlike extract_from_pdf() which preserves PDF line structure, this function:
    1. Filters watermarks and garbage
    2. Merges fragmented lines into logical paragraphs
    3. Detects section boundaries

    Args:
        pdf_path: Path to PDF file

    Returns:
        Dict with:
        - 'sections': List of {name: str, paragraphs: List[str]}
        - 'raw_paragraphs': Flat list of cleaned paragraphs
        - 'stats': Extraction statistics
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return None

    try:
        from pdfminer.high_level import extract_text

        raw_text = extract_text(pdf_path) or ""
    except Exception as e:
        log(f"PDF synthesis extract failed: {e}")
        return None

    # Split into lines
    raw_lines = raw_text.split("\n")
    original_count = len(raw_lines)

    # Merge into paragraphs (filtering happens inside)
    paragraphs = merge_pdf_lines_to_paragraphs(raw_lines)

    if not paragraphs:
        log(f"No paragraphs extracted from {pdf_path}")
        return None

    # Group into sections
    sections: List[Dict[str, Any]] = []
    current_section: Dict[str, Any] = {"name": "Preamble", "paragraphs": []}

    for para in paragraphs:
        section_name = detect_section_boundary(para)
        if section_name:
            # Save current section if it has content
            if current_section["paragraphs"]:
                sections.append(current_section)
            # Start new section
            current_section = {"name": section_name, "paragraphs": []}
        else:
            current_section["paragraphs"].append(para)

    # Don't forget the last section
    if current_section["paragraphs"]:
        sections.append(current_section)

    stats = {
        "original_lines": original_count,
        "merged_paragraphs": len(paragraphs),
        "detected_sections": len(sections),
        "section_names": [s["name"] for s in sections],
    }

    log(
        f"Synthesis extraction: {original_count} lines -> {len(paragraphs)} paragraphs "
        f"in {len(sections)} sections"
    )

    return {
        "sections": sections,
        "raw_paragraphs": paragraphs,
        "stats": stats,
    }


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _find_main_tex(files: List[str], read_file) -> Optional[str]:
    # Prefer files with \documentclass and \begin{document}
    candidates = []
    for name in files:
        if not name.lower().endswith(".tex"):
            continue
        try:
            txt = read_file(name)
        except Exception:
            continue
        if "\\documentclass" in txt and "\\begin{document}" in txt:
            candidates.append((name, len(txt)))
    if candidates:
        candidates.sort(key=lambda x: -x[1])
        return candidates[0][0]
    # Fallback: largest .tex
    texes = [n for n in files if n.lower().endswith(".tex")]
    if texes:
        # Cannot measure size here reliably; pick first
        return texes[0]
    return None


def _extract_tex_content(tex: str) -> str:
    # Keep content strictly inside document body
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", tex, flags=re.DOTALL)
    body = m.group(1) if m else tex
    # Strip full-line comments
    lines = []
    for line in body.splitlines():
        if line.strip().startswith("%"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _split_paragraphs(text: str) -> List[str]:
    # Split on blank lines; normalize whitespace
    paras = []
    for block in re.split(r"\n\s*\n", text):
        t = re.sub(r"\s+", " ", block).strip()
        if len(t) >= 2:
            paras.append(t)
    return paras


def extract_from_latex(archive_path: str) -> Optional[List[str]]:
    if not archive_path or not os.path.exists(archive_path):
        return None
    try:
        if archive_path.lower().endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                names = zf.namelist()

                def read_file(n):
                    with zf.open(n) as f:
                        return f.read().decode("utf-8", errors="ignore")

                main = _find_main_tex(names, read_file)
                if not main:
                    return None
                tex = read_file(main)
        else:
            with tarfile.open(archive_path, "r:gz") as tf:
                names = [m.name for m in tf.getmembers() if m.isfile()]

                def read_file(n):
                    member = tf.getmember(n)
                    with tf.extractfile(member) as f:
                        return f.read().decode("utf-8", errors="ignore")

                main = _find_main_tex(names, read_file)
                if not main:
                    return None
                tex = read_file(main)
    except Exception as e:
        log(f"latex extract failed: {e}")
        return None
    content = _extract_tex_content(tex)
    return _split_paragraphs(content)


def extract_from_pdf(pdf_path: str) -> Optional[List[str]]:
    if not pdf_path or not os.path.exists(pdf_path):
        return None
    try:
        from pdfminer.high_level import extract_text

        txt = extract_text(pdf_path) or ""
    except Exception as e:
        log(f"pdf extract failed: {e}")
        return None
    # Coalesce into paragraphs using blank lines
    # pdfminer might insert many newlines; compact multiple newlines
    txt = re.sub(r"\n{2,}", "\n\n", txt)
    return _split_paragraphs(txt)


def extract_body_paragraphs(rec: dict) -> List[str]:
    files = rec.get("files") or {}
    # Prefer LaTeX
    paras = extract_from_latex(files.get("latex_source_path"))
    if paras:
        return paras
    paras = extract_from_pdf(files.get("pdf_path"))
    if paras:
        return paras
    return []
