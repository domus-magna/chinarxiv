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


# =============================================================================
# FIGURE DETECTION: For logging and placeholder insertion
# =============================================================================

# Patterns to detect figure/table captions
# Each pattern: (compiled_regex, type, is_caption_pattern)
# is_caption_pattern=True means this is likely a caption definition, not just a reference
FIGURE_PATTERNS = [
    # ==========================================================================
    # SUPPLEMENTARY FIGURES (HIGH PRIORITY - catches ~15-20% more)
    # ==========================================================================
    # "Supplementary Figure S1", "Supp. Fig. A1", "SI Figure 2", "Extended Data Fig. 1"
    (re.compile(r'(?:Supplementary|Supp\.?|SI|Extended\s+Data)\s*(?:Figure|Fig\.?)\s*([A-Z]?\d+(?:\.\d+)?)', re.IGNORECASE), 'figure', False),
    # "Figure S1", "Fig. A1" (letter prefix without "Supplementary")
    (re.compile(r'(?:Figure|Fig\.?)\s+([A-Z]\d+(?:\.\d+)?)[:\.\s]', re.IGNORECASE), 'figure', False),
    # Supplementary tables: "Supplementary Table S1", "SI Table 2"
    (re.compile(r'(?:Supplementary|Supp\.?|SI|Extended\s+Data)\s*(?:Table)\s*([A-Z]?\d+(?:\.\d+)?)', re.IGNORECASE), 'table', False),
    # "Table S1", "Table A1"
    (re.compile(r'(?:Table)\s+([A-Z]\d+(?:\.\d+)?)[:\.\s]', re.IGNORECASE), 'table', False),

    # ==========================================================================
    # FIGURES WITH SUB-LETTERS (e.g., Figure 1a, Fig 2B, Figure 1A-C)
    # ==========================================================================
    # Caption-style: "Figure 1a:" or "Figure 1a." at start
    (re.compile(r'^(?:Figure|Fig\.?)\s+(\d+)([a-z](?:-[a-z])?)[:\.\s]', re.IGNORECASE), 'figure', True),
    # Reference-style: "in Figure 1a" or "see Fig. 2b"
    (re.compile(r'\b(?:Figure|Fig\.?)\s+(\d+)([a-z](?:-[a-z])?)\b', re.IGNORECASE), 'figure', False),
    # Range with letters: "Figure 1A-C", "Figures 1a-d"
    (re.compile(r'(?:Figure|Fig\.?)s?\s+(\d+)([A-Za-z])\s*[-–—]\s*([A-Za-z])', re.IGNORECASE), 'figure', False),

    # ==========================================================================
    # FIGURE RANGES (e.g., Figures 1-3, Figs. 2 to 5)
    # ==========================================================================
    # "Figures 1-3", "Figs 2–5", "Figures 1 to 3"
    (re.compile(r'(?:Figures?|Figs?\.?)\s+(\d+)\s*(?:to|[-–—])\s*(\d+)', re.IGNORECASE), 'figure', False),
    # "Tables 1-3", "Tables 2 and 3"
    (re.compile(r'(?:Tables?)\s+(\d+)\s*(?:to|[-–—]|and)\s*(\d+)', re.IGNORECASE), 'table', False),

    # ==========================================================================
    # STANDARD ENGLISH PATTERNS (existing, refined)
    # ==========================================================================
    # Caption-style (at start of line): "Figure 1:" or "Figure 1."
    (re.compile(r'^(?:Figure|Fig\.?)\s+(\d+(?:\.\d+)?)[:\.\s]', re.IGNORECASE), 'figure', True),
    # Reference-style: "in Figure 1" or "see Figure 1"
    (re.compile(r'\b(?:Figure|Fig\.?)\s+(\d+(?:\.\d+)?)[:\.\s\)]', re.IGNORECASE), 'figure', False),
    # Table caption-style
    (re.compile(r'^(?:Table)\s+(\d+(?:\.\d+)?)[:\.\s]', re.IGNORECASE), 'table', True),
    # Table reference-style
    (re.compile(r'\b(?:Table)\s+(\d+(?:\.\d+)?)[:\.\s\)]', re.IGNORECASE), 'table', False),
    # Scheme/Chart/Diagram/Plate/Panel
    (re.compile(r'^(?:Scheme|Chart|Diagram|Plate|Panel)\s+(\d+)[:\.\s]', re.IGNORECASE), 'figure', True),

    # ==========================================================================
    # CHINESE PATTERNS (expanded significantly)
    # ==========================================================================
    # Standard Chinese figures: "图 1:", "图1：", "图1."
    (re.compile(r'^图\s*(\d+(?:\.\d+)?)[：:．.\s]'), 'figure', True),
    (re.compile(r'图\s*(\d+(?:\.\d+)?)[：:．.\s]'), 'figure', False),
    # Chinese figures with letters: "图1a", "图 2b"
    (re.compile(r'图\s*(\d+)([a-z])', re.IGNORECASE), 'figure', False),
    # Chinese figure ranges: "图 1-3", "图1～5"
    (re.compile(r'图\s*(\d+)\s*[－\-–—～~]\s*(\d+)'), 'figure', False),
    # Chinese tables: "表 1:", "表1：", "表1."
    (re.compile(r'^表\s*(\d+(?:\.\d+)?)[：:．.\s]'), 'table', True),
    (re.compile(r'表\s*(\d+(?:\.\d+)?)[：:．.\s]'), 'table', False),
    # Chinese tables with letters: "表1a"
    (re.compile(r'表\s*(\d+)([a-z])', re.IGNORECASE), 'table', False),
    # Chinese table ranges: "表 1-3"
    (re.compile(r'表\s*(\d+)\s*[－\-–—～~]\s*(\d+)'), 'table', False),

    # ==========================================================================
    # CHINESE SUPPLEMENTARY PATTERNS
    # ==========================================================================
    # "补充图 1", "补充图S1", "附图1", "附表1"
    (re.compile(r'(?:补充|附加|附)\s*图\s*([A-Za-z]?\d+)'), 'figure', False),
    (re.compile(r'(?:补充|附加|附)\s*表\s*([A-Za-z]?\d+)'), 'table', False),
    # "扩展数据图 1" (Extended Data Figure)
    (re.compile(r'扩展数据\s*图\s*(\d+)'), 'figure', False),
    (re.compile(r'扩展数据\s*表\s*(\d+)'), 'table', False),
    # "SI图1", "SI表1" (Chinese docs using SI prefix)
    (re.compile(r'SI\s*图\s*(\d+)'), 'figure', False),
    (re.compile(r'SI\s*表\s*(\d+)'), 'table', False),

    # ==========================================================================
    # CHINESE SPECIALIZED FIGURE TYPES
    # ==========================================================================
    # "图表1" (combined figure-table)
    (re.compile(r'^图表\s*(\d+)[：:]'), 'figure', True),
    (re.compile(r'图表\s*(\d+)'), 'figure', False),
    # "插图1" (illustration), "示意图1" (schematic), "流程图1" (flowchart)
    (re.compile(r'(?:插图|示意图|流程图|框架图|原理图)\s*(\d+)'), 'figure', False),
    # "曲线图1" (curve), "柱状图1" (bar chart), "饼图1" (pie chart)
    (re.compile(r'(?:曲线图|柱状图|饼图|散点图|直方图)\s*(\d+)'), 'figure', False),
]

# Maximum caption length to extract (improved from 150)
MAX_CAPTION_LENGTH = 500


def _extract_caption_text(para: str, match_end: int) -> str:
    """
    Extract caption text after the figure/table reference.

    Looks for caption content after "Figure 1:" or similar patterns,
    extracting up to MAX_CAPTION_LENGTH chars until the next sentence
    or paragraph break.
    """
    # Start from match end position
    remainder = para[match_end:].strip()

    # If starts with : or . followed by space, skip that
    if remainder and remainder[0] in ':：.．':
        remainder = remainder[1:].strip()

    # Take up to MAX_CAPTION_LENGTH
    if len(remainder) > MAX_CAPTION_LENGTH:
        # Try to break at sentence boundary
        truncated = remainder[:MAX_CAPTION_LENGTH]
        # Find last sentence ending
        last_period = max(
            truncated.rfind('. '),
            truncated.rfind('。'),
            truncated.rfind('! '),
            truncated.rfind('? ')
        )
        if last_period > MAX_CAPTION_LENGTH // 2:
            return truncated[:last_period + 1].strip()
        return truncated.strip() + '...'

    return remainder


def _compute_confidence(para: str, match, is_caption_pattern: bool) -> float:
    """
    Compute confidence score (0.0-1.0) for a figure detection.

    Higher confidence for:
    - Caption patterns (start of paragraph with colon/period)
    - Longer surrounding context
    - Clear caption text following the reference

    Lower confidence for:
    - In-text references ("as shown in Figure 1")
    - Very short paragraphs
    - Ambiguous contexts
    """
    confidence = 0.5  # Base confidence

    # Caption at start of paragraph = high confidence
    if is_caption_pattern and match.start() < 5:
        confidence += 0.3
    elif match.start() < 20:
        confidence += 0.1

    # Check for caption indicators after match
    post_match = para[match.end():match.end() + 20] if match.end() < len(para) else ''
    if post_match and post_match.strip() and post_match.strip()[0] in ':：.．':
        confidence += 0.15  # Has caption delimiter

    # Check for reference indicators (lower confidence)
    pre_match = para[max(0, match.start() - 30):match.start()].lower()
    reference_phrases = ['see ', 'shown in ', 'as in ', 'refer to ', '参见', '如图', '见表']
    if any(phrase in pre_match for phrase in reference_phrases):
        confidence -= 0.15  # Just a reference, not definition

    # Paragraph length heuristic
    if len(para) > 200:
        confidence += 0.05  # Longer paragraphs more likely to be captions

    return min(1.0, max(0.0, confidence))


def detect_figures(paragraphs: List[str]) -> List[Dict[str, Any]]:
    """
    Detect figure and table references in a list of paragraphs.

    Args:
        paragraphs: List of paragraph strings to scan

    Returns:
        List of detected figures, each with:
        - type: 'figure' or 'table'
        - number: The figure/table number (as string, may include letters)
        - caption_preview: Up to 500 chars of caption text
        - paragraph_index: Index in the paragraphs list
        - confidence: 0.0-1.0 confidence score
        - is_caption: True if this appears to be a caption definition
    """
    if not paragraphs:
        return []

    figures: List[Dict[str, Any]] = []
    seen: set = set()  # Track (type, number) to avoid duplicates

    for para_idx, para in enumerate(paragraphs):
        if not para:
            continue

        for pattern_tuple in FIGURE_PATTERNS:
            # Handle both 2-tuple (old) and 3-tuple (new) formats
            if len(pattern_tuple) == 3:
                pattern, fig_type, is_caption_pattern = pattern_tuple
            else:
                pattern, fig_type = pattern_tuple
                is_caption_pattern = False

            match = pattern.search(para)
            if match:
                # Extract number - try group(1) first, then group(2) for patterns
                # with multiple groups (like "Figure 1a" where group(1)=1, group(2)=a)
                try:
                    number = match.group(1)
                    # For sub-letter patterns, append the letter
                    if match.lastindex and match.lastindex >= 2:
                        sub_letter = match.group(2)
                        if sub_letter and len(sub_letter) <= 2:
                            number = f"{number}{sub_letter}"
                except (IndexError, TypeError):
                    continue

                if not number:
                    continue

                key = (fig_type, number)

                if key not in seen:
                    seen.add(key)

                    # Compute confidence score
                    confidence = _compute_confidence(para, match, is_caption_pattern)

                    # Extract caption text
                    caption = _extract_caption_text(para, match.end())
                    if not caption:
                        # Fallback to paragraph truncation
                        caption = para[:MAX_CAPTION_LENGTH] if len(para) > MAX_CAPTION_LENGTH else para

                    figures.append({
                        'type': fig_type,
                        'number': number,
                        'caption_preview': caption,
                        'paragraph_index': para_idx,
                        'confidence': round(confidence, 2),
                        'is_caption': is_caption_pattern and match.start() < 5,
                    })

    # Sort by type then number (handle letters in number)
    def sort_key(f):
        num_str = f['number'] or '0'
        # Extract numeric part for sorting
        numeric_match = re.match(r'([A-Za-z]?)(\d+)', num_str)
        if numeric_match:
            prefix = numeric_match.group(1) or ''
            num = int(numeric_match.group(2))
            return (0 if f['type'] == 'figure' else 1, prefix, num)
        return (0 if f['type'] == 'figure' else 1, '', 0)

    figures.sort(key=sort_key)

    return figures


def add_figure_metadata(translation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add figure metadata to a translation dict.

    Scans body_en, body_md, and body_zh for figures and tables,
    then adds _figures, _has_figures, _figure_count, _table_count fields.

    Args:
        translation: Translation dict with body content

    Returns:
        Updated translation dict with figure metadata
    """
    all_figures: List[Dict[str, Any]] = []
    seen: set = set()

    # Check body_en (list of paragraphs)
    body_en = translation.get('body_en')
    if body_en and isinstance(body_en, list):
        for fig in detect_figures(body_en):
            key = (fig['type'], fig['number'])
            if key not in seen:
                seen.add(key)
                all_figures.append(fig)

    # Check body_md (markdown string)
    body_md = translation.get('body_md')
    if body_md and isinstance(body_md, str):
        # Split into paragraphs for detection
        md_paragraphs = [p.strip() for p in body_md.split('\n') if p.strip()]
        for fig in detect_figures(md_paragraphs):
            key = (fig['type'], fig['number'])
            if key not in seen:
                seen.add(key)
                fig['source'] = 'md'
                all_figures.append(fig)

    # Check body_zh for Chinese-only figures
    body_zh = translation.get('body_zh')
    if body_zh and isinstance(body_zh, list):
        for fig in detect_figures(body_zh):
            key = (fig['type'], fig['number'])
            if key not in seen:
                seen.add(key)
                fig['source'] = 'zh'
                all_figures.append(fig)

    # Count by type
    figure_count = sum(1 for f in all_figures if f['type'] == 'figure')
    table_count = sum(1 for f in all_figures if f['type'] == 'table')

    # Add metadata to translation
    translation['_figures'] = all_figures
    translation['_has_figures'] = len(all_figures) > 0
    translation['_figure_count'] = figure_count
    translation['_table_count'] = table_count

    return translation


# =============================================================================
# FIGURE MARKER INJECTION: For position tracking across translation
# =============================================================================

# Simplified patterns for marker injection (subset of FIGURE_PATTERNS that are clear references)
MARKER_PATTERNS = [
    # English figures: "Figure 1", "Fig. 2", "Figure 1a"
    (re.compile(r'((?:Figure|Fig\.?)\s+(\d+)([a-z]?))', re.IGNORECASE), 'FIGURE'),
    # English tables: "Table 1", "Table 2a"
    (re.compile(r'((?:Table)\s+(\d+)([a-z]?))', re.IGNORECASE), 'TABLE'),
    # Chinese figures: "图1", "图 2", "图1a"
    (re.compile(r'(图\s*(\d+)([a-z]?))'), 'FIGURE'),
    # Chinese tables: "表1", "表 2"
    (re.compile(r'(表\s*(\d+)([a-z]?))'), 'TABLE'),
]


def inject_figure_markers(
    paragraphs: List[str],
    seen_markers: Optional[set] = None,
) -> tuple:
    """
    Inject [FIGURE:N] and [TABLE:N] markers into paragraphs.

    These markers are preserved during translation and allow the assembler
    to know exactly where to insert figure images in the final output.

    Args:
        paragraphs: List of paragraph strings (pre-translation)
        seen_markers: Optional set to track markers already injected

    Returns:
        Tuple of:
        - List of paragraphs with markers injected
        - Dict mapping marker (e.g., "FIGURE:1") to original paragraph index
        - Set of all markers injected

    Example:
        Input:  "如图1所示,数据分布呈正态分布"
        Output: "如图1 [FIGURE:1] 所示,数据分布呈正态分布"
    """
    if not paragraphs:
        return [], {}, set()

    marked_paragraphs: List[str] = []
    marker_map: Dict[str, int] = {}  # marker -> paragraph index
    injected: set = seen_markers.copy() if seen_markers else set()

    for para_idx, para in enumerate(paragraphs):
        if not para:
            marked_paragraphs.append(para)
            continue

        # Find all figure/table references and their positions
        insertions: List[tuple] = []  # (position, marker_text)

        for pattern, marker_type in MARKER_PATTERNS:
            for match in pattern.finditer(para):
                # Extract the full match and number
                full_match = match.group(1)
                number = match.group(2)
                sub_letter = match.group(3) if match.lastindex >= 3 else ''

                # Create unique marker
                fig_num = f"{number}{sub_letter}" if sub_letter else number
                marker_key = f"{marker_type}:{fig_num}"

                # Only inject once per unique figure
                if marker_key not in injected:
                    injected.add(marker_key)
                    marker_text = f" [{marker_key}]"
                    insertions.append((match.end(), marker_text, marker_key))
                    marker_map[marker_key] = para_idx

        # Sort insertions by position (reverse order to preserve indices)
        insertions.sort(key=lambda x: x[0], reverse=True)

        # Apply insertions
        result = para
        for pos, marker_text, _ in insertions:
            result = result[:pos] + marker_text + result[pos:]

        marked_paragraphs.append(result)

    return marked_paragraphs, marker_map, injected


def inject_markers_in_sections(
    sections: List[Dict[str, Any]],
) -> tuple:
    """
    Inject figure markers into section-structured content.

    Args:
        sections: List of section dicts with 'name' and 'paragraphs' keys

    Returns:
        Tuple of:
        - List of sections with markers injected in paragraphs
        - Dict mapping marker to (section_index, paragraph_index)
        - Set of all markers
    """
    marked_sections: List[Dict[str, Any]] = []
    marker_map: Dict[str, tuple] = {}
    all_markers: set = set()

    for section_idx, section in enumerate(sections):
        name = section.get('name', 'Untitled')
        paragraphs = section.get('paragraphs', [])

        # Inject markers in this section's paragraphs
        marked_paras, para_map, markers = inject_figure_markers(
            paragraphs, seen_markers=all_markers
        )

        # Update all_markers
        all_markers.update(markers)

        # Update marker_map with section context
        for marker, para_idx in para_map.items():
            marker_map[marker] = (section_idx, para_idx)

        marked_sections.append({
            'name': name,
            'paragraphs': marked_paras,
        })

    return marked_sections, marker_map, all_markers


def strip_figure_markers(text: str) -> str:
    """
    Remove all [FIGURE:N] and [TABLE:N] markers from text.

    Useful for displaying text without markers or cleaning up
    if markers weren't properly replaced.

    Args:
        text: Text potentially containing markers

    Returns:
        Text with markers removed
    """
    return re.sub(r'\s*\[(?:FIGURE|TABLE):[A-Za-z]?\d+[a-z]?\]', '', text)
