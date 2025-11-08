"""
Formatting service for consistent Markdown output.

Uses LLM-based formatting to convert translated fields into consistent Markdown 
while preserving math and citations. Fails loudly if LLM formatting is unavailable.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

from ..config import get_config, get_proxies
from ..http_client import openrouter_headers, parse_openrouter_error
from ..monitoring import monitoring_service, alert_critical
from ..tex_guard import mask_math, unmask_math, verify_token_parity

# Removed heuristic formatting imports - LLM formatting only


FORMATTER_SYSTEM_PROMPT = (
    "You are a professional scientific document formatter.\n"
    "Task: Convert the provided translated content into well-spaced, readable Markdown without changing meaning.\n\n"
    "STRICT REQUIREMENTS:\n"
    "1. Do NOT modify, translate, or rewrite any LaTeX math or citations (e.g., \\cite{}, \\ref{}).\n"
    "2. Preserve the original order and content; only adjust whitespace and structure.\n"
    "3. Output MUST be valid JSON with keys: 'abstract_md' and 'body_md'. No code fences.\n"
    "4. Use '# Title' only in 'body_md' if the title is included. If uncertain, keep title out.\n"
    "5. Use '## Abstract' heading for abstract in 'abstract_md' only if the abstract includes substructure; otherwise emit just the paragraph.\n"
    "6. Use '##' or '###' for section headings detected from numbered headings (e.g., '1. Introduction').\n"
    "7. Merge broken lines into full paragraphs; convert true lists to '- ' list items; avoid spurious lists.\n"
    "8. Do NOT wrap the entire document in triple backticks.\n\n"
    "CRITICAL FORMATTING RULES:\n"
    "- Add generous spacing between sections and paragraphs (use double newlines).\n"
    "- Separate different types of content (authors, affiliations, abstract, keywords) with clear spacing.\n"
    "- Break up dense text blocks into readable paragraphs.\n"
    "- Only preserve or normalize existing emphasis markup; do NOT invent new emphasis.\n"
    "- Ensure each section heading has space before and after it.\n"
    "- Format author lists, affiliations, and metadata with appropriate line breaks.\n"
    "- Make the document visually appealing and easy to scan.\n\n"
    "Formatting hints:\n"
    "- Keep paragraph breaks; remove isolated short fragments by merging with neighbors.\n"
    "- If a paragraph is primarily a short formula, you may wrap it with '```' code fences.\n"
    "- Prioritize readability over compactness - err on the side of more spacing.\n"
)


class FormattingService:
    """Service to format translations into consistent Markdown using LLM only."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or get_config()
        fmt = self.config.get("formatting") or {}
        self.model: str = fmt.get(
            "model",
            self.config.get("models", {}).get(
                "default_slug", "deepseek/deepseek-v3.2-exp"
            ),
        )
        self.temperature: float = float(fmt.get("temperature", 0.1))

    def _parse_formatter_json(self, content: str) -> Dict[str, Any]:
        """
        Parse formatter output into JSON, tolerating surrounding commentary.

        The formatter occasionally returns extra prose before/after the JSON
        payload. We try the straightforward load first, then fall back to
        scanning for the first JSON object in the response.
        """
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            stripped = content.strip()
            parsed = None

            for idx, ch in enumerate(stripped):
                if ch not in "{[":
                    continue
                try:
                    parsed_candidate, _ = decoder.raw_decode(stripped[idx:])
                except json.JSONDecodeError:
                    continue
                parsed = parsed_candidate
                break

            if parsed is None:
                raise
        if not isinstance(parsed, dict):
            raise ValueError("Formatter output was not a JSON object")
        return parsed

    def format_translation(
        self, translation: Dict[str, Any], *, dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Format a translation record into consistent Markdown using LLM.

        Returns the translation with additional fields:
          - 'abstract_md': Markdown string for abstract
          - 'body_md': Markdown string for full text body

        Raises RuntimeError if LLM formatting fails - no fallback.
        """
        if dry_run:
            # In dry run mode, return the translation without formatting
            return {**translation}

        return self._llm_format(translation)

    # Removed _heuristic method - LLM formatting only

    def _llm_format(self, translation: Dict[str, Any]) -> Dict[str, Any]:
        """LLM-based formatting with math preservation guard."""
        title = (translation.get("title_en") or "").strip()
        abstract = (translation.get("abstract_en") or "").strip()
        body_paras = translation.get("body_en") or []

        # Join body with sentinel so the model can see paragraph boundaries
        PARA = "\n\n⟪PARA_BREAK⟫\n\n"
        body_joined = PARA.join(
            (p or "").strip() for p in body_paras if p and p.strip()
        )

        # Mask math across combined strings to protect content
        abstract_masked, abstract_map = mask_math(abstract)
        body_masked, body_map = mask_math(body_joined)

        # Prepare user payload
        user_payload = {
            "title": title,
            "abstract": abstract_masked,
            "body_joined": body_masked,
            "paragraph_separator": PARA,
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": FORMATTER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Please format this translated paper as consistent Markdown.\n"
                        "Return ONLY strict JSON with keys 'abstract_md' and 'body_md'.\n\n"
                        f"Input JSON:\n{json.dumps(user_payload, ensure_ascii=False)}"
                    ),
                },
            ],
            "temperature": self.temperature,
        }

        proxies, source = get_proxies()
        try:
            kwargs = {
                "headers": openrouter_headers(),
                "data": json.dumps(payload),
                "timeout": (15, 90) if source != "none" else (10, 60),
            }
            if source == "config" and proxies:
                kwargs["proxies"] = proxies
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions", **kwargs
            )
            if not resp.ok:
                info = parse_openrouter_error(resp)
                status = info["status"]
                code = info["code"]
                message = info["message"] or f"OpenRouter error {status}"
                # Record error for budget tracking
                try:
                    monitoring_service.record_error(
                        service="openrouter",
                        message=message,
                        status=status,
                        code=code or None,
                        metadata={"component": "formatting", "model": self.model},
                    )
                except Exception:
                    pass
                # For formatting, we don't retry here; surface a clear message
                if status == 401 and not info["fallback_ok"]:
                    # Immediate critical alert for fatal
                    try:
                        alert_critical(
                            "OpenRouter Fatal Error (Formatting)",
                            message,
                            source="formatting_service",
                            metadata={
                                "status": status,
                                "code": code or "unknown",
                                "model": self.model,
                            },
                        )
                    except Exception:
                        pass
                    raise RuntimeError(
                        "OpenRouter API key is invalid or account has insufficient funds. Please verify OPENROUTER_API_KEY and credits."
                    )
                if status == 429:
                    raise RuntimeError(
                        "OpenRouter rate limit exceeded. Please try again later."
                    )
                raise RuntimeError(
                    f"OpenRouter error {status} ({code or 'unknown_code'}): {message}"
                )
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
        except requests.exceptions.RequestException as e:
            try:
                monitoring_service.record_error(
                    service="openrouter",
                    message=str(e),
                    status=None,
                    code="network_error",
                    metadata={"component": "formatting", "model": self.model},
                )
            except Exception:
                pass
            raise RuntimeError(f"Network error calling OpenRouter API: {e}")
        except Exception as e:
            raise RuntimeError(f"Formatter API failed: {e}")

        # Parse JSON response safely
        try:
            content_str = content
            if content_str.startswith("```") and content_str.endswith("```"):
                # Strip triple backticks and optional json hint
                inner = content_str.strip().strip("`")
                if inner.startswith("json\n"):
                    inner = inner[5:]
                content_str = inner
            parsed = self._parse_formatter_json(content_str)
        except Exception as e:
            raise RuntimeError(
                f"Failed to parse formatter JSON: {e}; content head: {content[:120]}"
            )

        abstract_md_masked = (parsed.get("abstract_md") or "").strip()
        body_md_masked = (parsed.get("body_md") or "").strip()

        # Verify math token parity for each part separately
        if not verify_token_parity(abstract_map, abstract_md_masked):
            raise RuntimeError(
                "Math placeholder parity check failed in abstract formatting output"
            )
        if not verify_token_parity(body_map, body_md_masked):
            raise RuntimeError(
                "Math placeholder parity check failed in body formatting output"
            )

        # Unmask with original maps
        abstract_md = unmask_math(abstract_md_masked, abstract_map)
        body_md = unmask_math(body_md_masked, body_map)

        out = {**translation}
        if abstract_md:
            out["abstract_md"] = abstract_md
        if body_md:
            out["body_md"] = body_md
        return out
