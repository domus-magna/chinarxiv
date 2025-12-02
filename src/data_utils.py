"""
Data processing utilities for ChinaXiv English translation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# CS/AI Paper Detection Keywords
# ============================================================================

# English keywords (lowercase for matching)
CS_AI_ENGLISH_KEYWORDS = [
    'machine learning', 'deep learning', 'neural network', 'neural net',
    'artificial intelligence', 'computer vision', 'nlp',
    'natural language processing', 'transformer', 'attention mechanism',
    'large language model', 'llm', 'gpt', 'bert', 'diffusion model',
    'generative model', 'generative ai', 'reinforcement learning',
    'knowledge graph', 'embedding', 'classification', 'segmentation',
    'pretrained', 'pre-trained', 'fine-tuning', 'finetuning',
    'convolutional', 'recurrent', 'lstm', 'rnn', 'cnn',
    'object detection', 'image recognition', 'speech recognition',
    'text generation', 'language model', 'chatbot', 'recommendation system',
    'graph neural', 'autoencoder', 'variational', 'adversarial',
    'bayesian neural', 'physics-informed neural',
]

# Chinese keywords
CS_AI_CHINESE_KEYWORDS = [
    '机器学习', '深度学习', '神经网络', '人工智能',
    '大语言模型', '大模型', '预训练', '计算机视觉',
    '自然语言处理', '知识图谱', '强化学习', '卷积',
    '生成对抗', '图神经网络', '注意力机制', '目标检测',
    '图像识别', '语义分割', '文本生成', '语音识别',
    '推荐系统', '循环神经', '长短期记忆',
]

# Subject patterns (partial match)
CS_AI_SUBJECT_PATTERNS = [
    '计算机', '信息科学', '信息技术',
    'computer', 'computing', 'informatics',
]


def utc_date_range_str(days_back: int = 1) -> Tuple[str, str]:
    """
    Get UTC date range string.

    Args:
        days_back: Number of days back from today

    Returns:
        Tuple of (start_date, end_date) in ISO format
    """
    # Yesterday UTC by default
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days_back)).date()
    end = start
    return start.isoformat(), end.isoformat()


def stable_id_from_oai(oai_identifier: str) -> str:
    """
    Extract stable ID from OAI identifier.

    Args:
        oai_identifier: OAI identifier (e.g., oai:chinaxiv.org:YYYY-XXXXX)

    Returns:
        Stable ID (e.g., YYYY-XXXXX)
    """
    # e.g., oai:chinaxiv.org:YYYY-XXXXX -> YYYY-XXXXX
    return oai_identifier.split(":")[-1]


def has_full_body_content(data: Dict[str, Any]) -> bool:
    """
    Determine whether a translation dict contains usable full-text content.

    Prefers explicit _has_full_body metadata when present, but gracefully
    falls back to inspecting body_en for legacy translations.
    """
    if "_has_full_body" in data:
        return bool(data.get("_has_full_body"))

    body_en = data.get("body_en")
    if isinstance(body_en, list):
        return any((para or "").strip() for para in body_en)
    return False


def filter_by_timestamp(
    items: List[Dict[str, Any]],
    cutoff: datetime,
    timestamp_key: str = "timestamp",
    keep_invalid: bool = False,
) -> List[Dict[str, Any]]:
    """
    Filter list of dicts, keeping items newer than cutoff.

    Args:
        items: List of dictionaries with timestamp fields
        cutoff: Datetime cutoff - items older than this are filtered out
        timestamp_key: Key name for the timestamp field (default: "timestamp")
        keep_invalid: If True, keep items with invalid/missing timestamps (default: False)

    Returns:
        Filtered list of items with timestamps after the cutoff

    Note:
        Handles timezone-aware and naive datetime comparison by normalizing
        both to UTC. Timezone-aware timestamps are converted to UTC before
        comparison. This ensures correct filtering regardless of timezone
        offsets in the stored data.
    """
    result = []
    for item in items:
        try:
            item_time = datetime.fromisoformat(item.get(timestamp_key, ""))
            # Normalize both to UTC for correct comparison
            if cutoff.tzinfo is None:
                # Naive cutoff - assume UTC, convert item_time to UTC then strip
                if item_time.tzinfo is not None:
                    # Convert to UTC before stripping timezone
                    item_time = item_time.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                # Aware cutoff - convert both to UTC for comparison
                cutoff_utc = cutoff.astimezone(timezone.utc)
                if item_time.tzinfo is None:
                    # Assume naive item_time is UTC
                    item_time = item_time.replace(tzinfo=timezone.utc)
                else:
                    item_time = item_time.astimezone(timezone.utc)
                # Compare in UTC
                if item_time > cutoff_utc:
                    result.append(item)
                continue
            if item_time > cutoff:
                result.append(item)
        except (ValueError, TypeError, AttributeError):
            # ValueError: invalid ISO format
            # TypeError: comparison with None
            # AttributeError: item is None (no .get() method)
            if keep_invalid:
                result.append(item)
    return result


def is_cs_ai_paper(paper: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Check if a paper is CS/AI related based on keywords in title, abstract, subjects.

    Args:
        paper: Paper dict with title, abstract, subjects fields

    Returns:
        Tuple of (is_cs_ai, matched_keyword) where matched_keyword shows
        what triggered the match (e.g., "en:machine learning", "zh:深度学习")
    """
    # Get text fields (handle both _en and raw field names)
    title = (paper.get('title') or paper.get('title_en') or '').lower()
    abstract = (paper.get('abstract') or paper.get('abstract_en') or '').lower()
    subjects = paper.get('subjects') or paper.get('subjects_en') or []
    if isinstance(subjects, str):
        subjects = [subjects]
    subjects_text = ' '.join(str(s).lower() for s in subjects)

    # Also check original Chinese fields if present
    title_zh = paper.get('title_zh') or paper.get('title') or ''
    abstract_zh = paper.get('abstract_zh') or paper.get('abstract') or ''

    # Combined text for English keyword search
    text_en = f"{title} {abstract} {subjects_text}"

    # Combined text for Chinese keyword search
    text_zh = f"{title_zh} {abstract_zh}"

    # Check subject patterns first (fastest)
    for pattern in CS_AI_SUBJECT_PATTERNS:
        if pattern.lower() in subjects_text:
            return True, f"subject:{pattern}"

    # Check English keywords
    for kw in CS_AI_ENGLISH_KEYWORDS:
        if kw in text_en:
            return True, f"en:{kw}"

    # Check Chinese keywords
    for kw in CS_AI_CHINESE_KEYWORDS:
        if kw in text_zh:
            return True, f"zh:{kw}"

    return False, None
