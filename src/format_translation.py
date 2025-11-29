#!/usr/bin/env python3
"""
Translation Formatter/Prettifier

Post-processes translated paragraphs to improve formatting and readability.
"""

from __future__ import annotations

import re
from typing import List, Dict, Any


def is_section_heading(text: str) -> bool:
    """
    Detect if a paragraph is likely a section heading.

    Examples:
        - "1. Introduction"
        - "2.1 Methods"
        - "Abstract"
        - "References"
        - "Acknowledgments"
    """
    # Numbered sections
    if re.match(r"^\d+\.?\s+[A-Z]", text.strip()):
        return True

    # Common heading words (short, capitalized, no period at end)
    heading_words = {
        "abstract",
        "introduction",
        "background",
        "methods",
        "methodology",
        "results",
        "discussion",
        "conclusion",
        "conclusions",
        "references",
        "acknowledgments",
        "acknowledgements",
        "appendix",
        "supplementary",
        "materials",
    }

    cleaned = text.strip().lower().rstrip(".")
    return bool(cleaned in heading_words and len(text) < 50)


def is_short_fragment(text: str, min_length: int = 50) -> bool:
    """Check if text is a short fragment that should be merged."""
    return len(text.strip()) < min_length


def is_mathematical_formula(text: str) -> bool:
    """
    Detect if text is primarily a mathematical formula.

    Looks for LaTeX commands or heavy use of mathematical symbols.
    """
    # Check for LaTeX commands
    if re.search(r"\\[a-zA-Z]+", text):
        return True

    # Check for high density of math symbols
    math_chars = sum(1 for c in text if c in "∑∏∫∂∇±×÷≤≥≠≈∞∈∉⊂⊃∩∪∀∃()[]{}")
    return bool(len(text) > 0 and math_chars / len(text) > 0.3)


def merge_short_fragments(paragraphs: List[str]) -> List[str]:
    """
    Merge short fragments with adjacent paragraphs.

    Short fragments are often:
    - Broken sentences from PDF extraction
    - Formula labels
    - Incomplete lines
    """
    if not paragraphs:
        return []

    merged = []
    buffer = ""

    for para in paragraphs:
        text = para.strip()

        if not text:
            continue

        # If this is a short fragment, add to buffer
        if is_short_fragment(text) and not is_section_heading(text):
            buffer += (" " if buffer else "") + text
        else:
            # Flush buffer with current paragraph
            if buffer:
                merged.append(buffer + " " + text)
                buffer = ""
            else:
                merged.append(text)

    # Flush remaining buffer
    if buffer:
        if merged:
            merged[-1] += " " + buffer
        else:
            merged.append(buffer)

    return merged


def format_as_markdown(paragraphs: List[str]) -> str:
    """
    Format paragraphs as structured markdown.

    - Section headings become ## headers
    - Math formulas get code fences
    - Regular paragraphs separated by blank lines
    """
    lines = []

    for para in paragraphs:
        text = para.strip()

        if not text:
            continue

        # Section headings
        if is_section_heading(text):
            # Remove trailing period from heading
            text = text.rstrip(".")
            lines.append(f"\n## {text}\n")

        # Mathematical formulas (put in code block)
        elif is_mathematical_formula(text) and len(text) < 200:
            lines.append(f"```\n{text}\n```\n")

        # Regular paragraphs
        else:
            lines.append(f"{text}\n")

    return "\n".join(lines)


def format_body_paragraphs(paragraphs: List[str]) -> List[str]:
    """
    Post-process translated body paragraphs for better formatting.

    Steps:
    1. Merge short fragments
    2. Clean up whitespace
    3. Structure with markdown

    Args:
        paragraphs: List of translated paragraphs

    Returns:
        Formatted paragraphs (or markdown string)
    """
    if not paragraphs:
        return []

    # Step 1: Merge short fragments
    merged = merge_short_fragments(paragraphs)

    # Step 2: Clean up each paragraph
    cleaned = []
    for para in merged:
        # Normalize whitespace
        text = re.sub(r"\s+", " ", para).strip()

        # Remove duplicate punctuation
        text = re.sub(r"\.\.+", ".", text)
        text = re.sub(r",\s*,", ",", text)

        if text:
            cleaned.append(text)

    return cleaned


def format_translation(translation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a complete translation record.

    Args:
        translation: Translation dict with title_en, abstract_en, body_en

    Returns:
        Formatted translation dict
    """
    formatted = translation.copy()

    # Format body paragraphs if present
    if translation.get("body_en"):
        formatted["body_en"] = format_body_paragraphs(translation["body_en"])

    # Format abstract (single paragraph, just clean whitespace)
    if translation.get("abstract_en"):
        abstract = translation["abstract_en"]
        formatted["abstract_en"] = re.sub(r"\s+", " ", abstract).strip()

    # Format title (single line, no extra whitespace)
    if translation.get("title_en"):
        title = translation["title_en"]
        formatted["title_en"] = re.sub(r"\s+", " ", title).strip()

    return formatted


def format_translation_to_markdown(translation: Dict[str, Any]) -> str:
    """
    Convert translation to a single markdown document.

    Useful for preview/display purposes.

    Args:
        translation: Translation dict

    Returns:
        Markdown string
    """
    parts = []

    # Title
    if translation.get("title_en"):
        parts.append(f"# {translation['title_en']}\n")

    # Metadata
    if translation.get("creators"):
        parts.append(f"**Authors:** {', '.join(translation['creators'])}\n")

    if translation.get("date"):
        parts.append(f"**Date:** {translation['date']}\n")

    if translation.get("subjects"):
        parts.append(f"**Subjects:** {', '.join(translation['subjects'])}\n")

    # Abstract
    if translation.get("abstract_en"):
        parts.append(f"\n## Abstract\n\n{translation['abstract_en']}\n")

    # Body
    if translation.get("body_en"):
        formatted_body = format_body_paragraphs(translation["body_en"])
        parts.append("\n## Full Text\n")
        parts.append(format_as_markdown(formatted_body))

    return "\n".join(parts)
