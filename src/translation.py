"""
Simplified translation service for ChinaXiv English translation.
"""

import os
from typing import Dict
from tenacity import retry, stop_after_attempt, wait_exponential

from .tex_guard import mask_math, unmask_math


class TranslationService:
    """Simplified translation service."""

    def __init__(self):
        self.model = "deepseek/deepseek-v3.2-exp"
        self.glossary = [
            {"zh": "机器学习", "en": "machine learning"},
            {"zh": "深度学习", "en": "deep learning"},
            {"zh": "神经网络", "en": "neural network"},
            {"zh": "人工智能", "en": "artificial intelligence"},
            {"zh": "数据挖掘", "en": "data mining"},
            {"zh": "自然语言处理", "en": "natural language processing"},
            {"zh": "计算机视觉", "en": "computer vision"},
            {"zh": "算法", "en": "algorithm"},
            {"zh": "模型", "en": "model"},
            {"zh": "训练", "en": "training"},
        ]

    def translate_text(self, text: str) -> str:
        """Translate text with math preservation."""
        if not text:
            return ""

        # Mask math expressions
        masked, mappings = mask_math(text)

        # Translate
        translated = self._call_api(masked)

        # Unmask math expressions
        return unmask_math(translated, mappings)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _call_api(self, text: str) -> str:
        """Call OpenRouter API."""
        system_prompt = self._build_system_prompt()

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }

        from .http_client import get_session

        session = get_session()

        response = session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=self._get_headers(),
            json=payload,
            timeout=60,
        )

        if not response.ok:
            raise Exception(f"API error: {response.status_code}")

        return response.json()["choices"][0]["message"]["content"].strip()

    def _build_system_prompt(self) -> str:
        """Build system prompt with glossary."""
        base_prompt = "Translate from Chinese to English. Preserve math expressions and citations."
        glossary_str = "\n".join(f"{g['zh']} => {g['en']}" for g in self.glossary)
        return f"{base_prompt}\n\nGlossary:\n{glossary_str}"

    def _get_headers(self) -> Dict[str, str]:
        """Get API headers."""
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")

        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://chinarxiv.org",
            "X-Title": "ChinaRxiv",
        }

    def translate_paper(self, paper_id: str) -> str:
        """Translate a complete paper."""
        # Load paper data
        paper = self._load_paper(paper_id)

        # Translate fields
        translated = {
            "id": paper["id"],
            "title_en": self.translate_text(paper.get("title", "")),
            "abstract_en": self.translate_text(paper.get("abstract", "")),
            "body_en": [self.translate_text(p) for p in paper.get("body", [])],
        }

        # Save translation
        self._save_translation(paper_id, translated)
        return f"data/translated/{paper_id}.json"

    def _load_paper(self, paper_id: str) -> Dict:
        """Load paper data."""
        import json

        paper_file = f"data/selected/{paper_id}.json"
        if os.path.exists(paper_file):
            with open(paper_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"id": paper_id, "title": "", "abstract": "", "body": []}

    def _save_translation(self, paper_id: str, translation: Dict):
        """Save translation."""
        import json

        os.makedirs("data/translated", exist_ok=True)
        translation_file = f"data/translated/{paper_id}.json"
        with open(translation_file, "w", encoding="utf-8") as f:
            json.dump(translation, f, indent=2, ensure_ascii=False)


# Global translation service instance
translation_service = TranslationService()


# Convenience functions
def translate_text(text: str) -> str:
    """Convenience function to translate text."""
    return translation_service.translate_text(text)


def translate_paper(paper_id: str) -> str:
    """Convenience function to translate a paper."""
    return translation_service.translate_paper(paper_id)
