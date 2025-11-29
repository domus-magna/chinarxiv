"""
Translation service for ChinaXiv English translation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ..config import get_config, get_proxies
from ..http_client import openrouter_headers, parse_openrouter_error
from ..monitoring import monitoring_service, alert_critical
from ..tex_guard import mask_math, unmask_math, verify_token_parity
from ..token_utils import estimate_tokens
from ..cost_tracker import compute_cost, append_cost_log
from ..logging_utils import log
from ..models import Paper, Translation
import re


SYSTEM_PROMPT = (
    "You are a professional scientific translator specializing in academic papers. "
    "Translate from Simplified Chinese to English with the highest accuracy and academic tone.\n\n"
    "CRITICAL REQUIREMENTS:\n"
    "1. Preserve ALL LaTeX commands and ⟪MATH_*⟫ placeholders exactly - do not modify, translate, or rewrite any mathematical formulas\n"
    "2. Preserve ALL citation commands (\\cite{}, \\ref{}, \\eqref{}, etc.) exactly as they appear\n"
    '3. **PRESERVE ALL <PARA id="N">...</PARA> paragraph wrapper tags EXACTLY** - these are structural markers that identify paragraphs and MUST remain untouched with their IDs\n'
    "4. Maintain academic tone and formal scientific writing style\n"
    "5. Use precise technical terminology - obey the glossary strictly\n"
    "6. Preserve section structure and paragraph organization\n"
    "7. Translate all content completely - do not omit any information\n\n"
    "OUTPUT RULES:\n"
    "- Return ONLY the translated text for the given input (no explanations, no quotes, no headings you invent).\n"
    "- Keep one output paragraph per input paragraph; do not merge or split paragraphs.\n"
    '- **Maintain exact count and IDs of <PARA id="N">...</PARA> tags** - if input has N paragraph tags, output must have exactly N paragraph tags with matching IDs\n'
    "- Do NOT add Markdown formatting unless it is present in the source.\n"
    "- Preserve original line breaks within the paragraph when meaningful; otherwise use standard English sentence spacing.\n\n"
    "FORMATTING GUIDELINES:\n"
    "- Keep mathematical expressions in their original LaTeX format\n"
    "- Preserve equation numbers and references\n"
    "- Maintain proper academic paragraph structure\n"
    "- Use formal scientific language appropriate for research papers\n"
    '- Never remove or modify <PARA id="N">...</PARA> tags - they are structural elements that must be preserved with their IDs intact\n\n'
    "Remember: Mathematical content, citations, and <PARA> wrapper tags must remain untouched - only translate the Chinese text inside the tags."
)


# =============================================================================
# SYNTHESIS MODE: New prompt for readable output
# =============================================================================

SYNTHESIS_SYSTEM_PROMPT = """You are an expert academic translator specializing in Chinese scientific papers.

Your task: Translate the Chinese academic text into fluent, readable English suitable for an academic audience.

CRITICAL REQUIREMENTS:
1. PRESERVE ALL MATH exactly as given - do not modify LaTeX, ⟪MATH_*⟫ placeholders, or equations
2. PRESERVE ALL CITATIONS exactly - keep \\cite{}, \\ref{}, \\eqref{} unchanged
3. Maintain academic tone, precision, and technical accuracy

TRANSLATION GUIDELINES:
1. Produce FLOWING, READABLE prose - prioritize natural English over literal translation
2. MERGE fragmented text into complete, well-formed sentences and paragraphs
3. SKIP garbage text (watermarks, artifacts, meaningless fragments) - do not translate nonsense
4. ORGANIZE by logical sections when they are provided
5. Each paragraph should express a complete thought (typically 3-8 sentences)
6. Use clear topic sentences and logical transitions

OUTPUT FORMAT:
Return your translation as clean Markdown:
- Use ## for major section headings (e.g., ## Introduction)
- Use ### for subsections (e.g., ### 2.1 Data Collection)
- Separate paragraphs with blank lines
- Preserve math inline ($...$) or in display blocks ($$...$$) as appropriate

DO NOT:
- Add commentary, notes, or explanations about the translation
- Include the original Chinese text
- Invent section numbers not in the original
- Convert prose to bullet lists (unless original was a list)
- Skip substantial content - only skip obvious garbage/watermarks

Remember: The goal is a paper that English-speaking academics can READ, UNDERSTAND, and CITE. Readability and accuracy are both essential."""


DEFAULT_TRANSLATION_CONFIG: Dict[str, Any] = {
    "fallback_models": [],
    "max_retries_per_model": 1,
    # Reliability > latency: allow slow providers plenty of headroom (read timeout 15m)
    "request_timeout_seconds": {"connect": 10, "read": 900},
    "circuit_breaker": {
        "persistent_error_threshold": 2,
        "transient_error_threshold": 5,
    },
}


MAX_RESPONSE_PREVIEW = 2048

# Maximum tokens per synthesis chunk - leaves margin for prompt overhead + response
# in most models' 32K-128K context windows
SYNTHESIS_MAX_TOKENS_PER_CHUNK = 28000

# Rough heuristic for estimating input tokens when paragraph count is known
# Used only for cost logging - actual token count comes from API
ESTIMATED_TOKENS_PER_PARAGRAPH = 100


class OpenRouterError(Exception):
    """OpenRouter API error (non-retryable by default)."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        retryable: bool = False,
        fallback_ok: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.fallback_ok = fallback_ok


class OpenRouterRetryableError(OpenRouterError):
    """Retryable OpenRouter API error (e.g., 429, 5xx, transient network)."""

    def __init__(
        self, message: str, *, code: Optional[str] = None, fallback_ok: bool = True
    ) -> None:
        super().__init__(message, code=code, retryable=True, fallback_ok=fallback_ok)


class OpenRouterFatalError(OpenRouterError):
    """Fatal OpenRouter API error (e.g., invalid key, insufficient funds)."""

    def __init__(
        self, message: str, *, code: Optional[str] = None, fallback_ok: bool = False
    ) -> None:
        super().__init__(message, code=code, retryable=False, fallback_ok=fallback_ok)


class TranslationValidationError(Exception):
    """Translation validation error."""

    pass


class MathPreservationError(Exception):
    """Math preservation error."""

    pass


# Re-export for backward compatibility
from .circuit_breaker import CircuitBreaker, CircuitBreakerOpen


class TranslationService:
    """Service for handling translation operations."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize translation service.

        Args:
            config: Configuration dictionary (optional)

        Thread Safety:
            This class is NOT thread-safe. Circuit breaker state
            (_consecutive_persistent, _consecutive_transient, _circuit_open)
            is not protected by locks. Each instance should only be used
            from a single thread.
        """
        self.config = config or get_config()
        self.model = self.config.get("models", {}).get("default_slug", "openai/gpt-5.1")
        self._active_paper_id: Optional[str] = None
        self.glossary = self.config.get("glossary", [])
        self.failure_log_dir = Path("data/monitoring/openrouter_failures")
        # Wall-clock guard per paper (seconds). None disables.
        self.paper_wallclock_limit = (
            float(
                (self.config.get("translation") or {}).get(
                    "paper_wallclock_limit_seconds", 0
                )
            )
            or None
        )

        # Circuit breaker
        translation_cfg = self.config.get("translation") or {}
        circuit_cfg = translation_cfg.get("circuit_breaker") or {}
        self._circuit_breaker = CircuitBreaker(
            persistent_threshold=int(circuit_cfg.get("persistent_error_threshold", 2)),
            transient_threshold=int(circuit_cfg.get("transient_error_threshold", 5)),
            source_name="translation_service",
        )
        timeout_cfg = translation_cfg.get("request_timeout_seconds") or {}
        self._connect_timeout = float(timeout_cfg.get("connect", 10))
        self._read_timeout = float(timeout_cfg.get("read", 60))

    def _check_circuit_breaker(self) -> None:
        """Check if circuit breaker is open. Raises CircuitBreakerOpen if tripped."""
        self._circuit_breaker.check()

    def _record_failure(self, error_code: Optional[str]) -> None:
        """Record an API failure. Raises CircuitBreakerOpen if threshold exceeded."""
        self._circuit_breaker.record_failure(error_code)

    def _on_api_success(self) -> None:
        """Record successful API call, resetting circuit breaker counters."""
        self._circuit_breaker.record_success()

    def _build_glossary_string(self, glossary: List[Dict[str, str]]) -> str:
        """
        Build glossary string from list of term dicts.

        Gracefully skips malformed entries (missing 'zh' or 'en' keys).

        Args:
            glossary: List of dicts with 'zh' and 'en' keys

        Returns:
            Formatted glossary string for prompt injection
        """
        parts = []
        for g in glossary:
            try:
                parts.append(f"{g['zh']} => {g['en']}")
            except (KeyError, TypeError):
                log(f"Warning: Skipping malformed glossary entry: {g}")
        return "\n".join(parts)

    @retry(
        wait=wait_exponential(min=1, max=10),
        stop=stop_after_attempt(4),  # 3 retries with exponential backoff
        retry=retry_if_exception_type(OpenRouterRetryableError),
        reraise=True,
    )
    def _call_openrouter(
        self, text: str, model: str, glossary: List[Dict[str, str]]
    ) -> str:
        """
        Call OpenRouter API for translation with automatic retry.

        Args:
            text: Text to translate
            model: Model to use
            glossary: Translation glossary

        Returns:
            Translated text

        Raises:
            OpenRouterError: On API failure
        """
        # prepend glossary as instructions
        glossary_str = self._build_glossary_string(glossary)
        system = SYSTEM_PROMPT + (
            "\nGlossary (zh => en):\n" + glossary_str if glossary_str else ""
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }

        # Reuse shared HTTP and error handling logic
        return self._execute_openrouter_request(payload, model)

    def _log_malformed_response(
        self,
        *,
        response: requests.Response,
        model: str,
        reason: str,
        error: Optional[Exception] = None,
        parsed: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist malformed OpenRouter payload details for debugging."""

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        preview = response.text[:MAX_RESPONSE_PREVIEW] if response.text else ""
        metadata = {
            "model": model,
            "reason": reason,
            "status_code": response.status_code,
            "content_type": response.headers.get("Content-Type"),
            "error": str(error) if error else None,
        }

        # Record the failure for dashboards/alerts without leaking full body content.
        try:
            monitoring_service.record_error(
                service="openrouter",
                message=f"Malformed response: {reason}",
                status=response.status_code,
                code=reason,
                metadata={**metadata, "body_preview": preview[:256]},
            )
        except Exception as e:
            log(f"Debug: Failed to record malformed response to monitoring: {e}")

        # Persist a richer artifact locally for manual debugging.
        try:
            self.failure_log_dir.mkdir(parents=True, exist_ok=True)
            record_path = (
                self.failure_log_dir / f"{timestamp}_{reason}_{uuid4().hex}.json"
            )
            record: Dict[str, Any] = {
                "timestamp": timestamp,
                **metadata,
                "body_preview": preview,
            }
            if parsed is not None:
                try:
                    record["parsed"] = parsed
                except Exception as e:
                    log(f"Debug: Failed to serialize parsed response: {e}")
                    record["parsed"] = str(parsed)
            with record_path.open("w", encoding="utf-8") as fh:
                json.dump(record, fh, ensure_ascii=True, indent=2)
        except Exception as e:
            log(f"Debug: Failed to persist malformed response artifact: {e}")

    def translate_field(
        self,
        text: str,
        model: Optional[str] = None,
        dry_run: bool = False,
        glossary_override: Optional[List[Dict[str, str]]] = None,
        validation_source: Optional[str] = None,
    ) -> str:
        """
        Translate a single field with math preservation.

        Args:
            text: Text to translate
            model: Model to use (defaults to service model)
            dry_run: If True, skip actual translation
            validation_source: Optional original text for validation when input text
                includes additional instructions or wrappers

        Returns:
            Translated text

        Raises:
            RuntimeError: On math parity check failure
        """
        if not text:
            return ""

        model = model or self.model
        glossary_eff = (
            glossary_override if glossary_override is not None else self.glossary
        )
        masked, mappings = mask_math(text)

        if dry_run:
            translated = masked  # identity to preserve placeholders
        else:
            # Call single model; higher-level retry/fallback handles alternates
            translated = self._call_openrouter(masked, model, glossary_eff)

        if not verify_token_parity(mappings, translated):
            expected = len(mappings)
            raise MathPreservationError(
                f"Math placeholder parity check failed: expected {expected} placeholders"
            )

        unmasked = unmask_math(translated, mappings)
        unmasked = self._strip_hallucinated_math(unmasked)

        # Additional validation checks
        original_for_validation = validation_source if validation_source else text
        self._validate_translation(original_for_validation, unmasked)

        return unmasked

    @staticmethod
    def _strip_hallucinated_math(text: str) -> str:
        """Remove any stray math placeholders hallucinated by the model."""
        if "⟪MATH_" not in text:
            return text
        cleaned = re.sub(r"⟪MATH_[0-9A-Z]+⟫", "", text)
        if cleaned != text:
            log("Stripped hallucinated math placeholders from translation")
        return cleaned

    def _validate_translation(self, original: str, translated: str) -> None:
        """
        Perform additional validation checks on translation quality.

        Args:
            original: Original Chinese text
            translated: Translated English text

        Raises:
            TranslationValidationError: If validation fails
        """
        # Check for empty translation
        if not translated or translated.strip() == "":
            raise TranslationValidationError("Translation is empty")

        # Check for reasonable length (translated should be roughly 1.2-2x original length)
        orig_len = len(original.strip())
        trans_len = len(translated.strip())

        if orig_len > 0:
            ratio = trans_len / orig_len
            if ratio < 0.5 or ratio > 3.0:
                log(
                    f"Warning: Unusual translation length ratio: {ratio:.2f} (original: {orig_len}, translated: {trans_len})"
                )

        # Check for common translation issues
        if "⟪MATH_" in translated:
            raise TranslationValidationError(
                "Math placeholders found in final translation"
            )

        # Check for citation preservation
        orig_citations = re.findall(r"\\cite\{[^}]*\}", original)
        trans_citations = re.findall(r"\\cite\{[^}]*\}", translated)

        if len(orig_citations) != len(trans_citations):
            log(
                f"Warning: Citation count mismatch (original: {len(orig_citations)}, translated: {len(trans_citations)})"
            )

        # Check for LaTeX command preservation
        orig_latex = re.findall(r"\\[a-zA-Z]+\{[^}]*\}", original)
        trans_latex = re.findall(r"\\[a-zA-Z]+\{[^}]*\}", translated)

        if len(orig_latex) != len(trans_latex):
            log(
                f"Warning: LaTeX command count mismatch (original: {len(orig_latex)}, translated: {len(trans_latex)})"
            )

    # =========================================================================
    # SYNTHESIS MODE: Methods for readable output translation
    # =========================================================================

    def _call_openrouter_synthesis(
        self,
        text: str,
        model: str,
        glossary: List[Dict[str, str]],
    ) -> str:
        """
        Call OpenRouter API with synthesis prompt for readable output.

        Uses slightly higher temperature for more natural prose.
        Shares error handling logic with _call_openrouter for consistency.
        """
        glossary_str = self._build_glossary_string(glossary)
        system = SYNTHESIS_SYSTEM_PROMPT + (
            "\n\nTerminology Glossary:\n" + glossary_str if glossary_str else ""
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,  # Slightly higher for more natural flow
        }

        # Reuse shared HTTP and error handling logic
        return self._execute_openrouter_request(payload, model)

    def _execute_openrouter_request(self, payload: Dict[str, Any], model: str) -> str:
        """
        Execute OpenRouter HTTP request with full error handling.

        This is the shared implementation for both _call_openrouter and
        _call_openrouter_synthesis, ensuring consistent error handling,
        monitoring, circuit breaker protection, and response parsing.

        Args:
            payload: Complete OpenRouter API payload (model, messages, temperature)
            model: Model name (for error reporting)

        Returns:
            Extracted content string from the API response

        Raises:
            OpenRouterRetryableError: For network errors, rate limits, or malformed responses
            OpenRouterFatalError: For auth/payment errors (no retry, no fallback)
            OpenRouterError: For other non-retryable errors
            CircuitBreakerOpen: If circuit breaker is tripped
        """
        # Check circuit breaker before making request
        self._check_circuit_breaker()

        proxies, source = get_proxies()
        try:
            kwargs = {
                "headers": openrouter_headers(),
                "data": json.dumps(payload),
                "timeout": (
                    self._connect_timeout
                    if source != "none"
                    else max(1.0, self._connect_timeout - 2),
                    self._read_timeout,
                ),
            }
            if source == "config" and proxies:
                kwargs["proxies"] = proxies
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions", **kwargs
            )
        except requests.RequestException as e:
            # Record network error for monitoring
            try:
                monitoring_service.record_error(
                    service="openrouter",
                    message=str(e),
                    status=None,
                    code="network_error",
                    metadata={"model": model},
                )
            except Exception as monitor_err:
                log(
                    f"Debug: Failed to record network error in monitoring: {monitor_err}"
                )
            self._record_failure("network_error")
            raise OpenRouterRetryableError(f"Network error: {e}", code="network_error")

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
                    metadata={"model": model},
                )
            except Exception as monitor_err:
                log(f"Debug: Failed to record API error in monitoring: {monitor_err}")
            if info["retryable"]:
                self._record_failure(code)
                raise OpenRouterRetryableError(
                    f"{message}", code=code, fallback_ok=info["fallback_ok"]
                )
            # fatal or non-retryable – decide if fallback to alternate models makes sense
            if not info["fallback_ok"]:
                # Immediate critical alert for fatal auth/payment
                try:
                    alert_critical(
                        "OpenRouter Fatal Error",
                        message,
                        source="translation_service",
                        metadata={
                            "status": status,
                            "code": code or "unknown",
                            "model": model,
                        },
                    )
                except Exception as alert_err:
                    log(f"Debug: Failed to send critical alert: {alert_err}")
                self._record_failure(code)
                raise OpenRouterFatalError(message, code=code, fallback_ok=False)
            self._record_failure(code)
            raise OpenRouterError(message, code=code, retryable=False, fallback_ok=True)

        try:
            data = resp.json()
        except ValueError as e:
            self._log_malformed_response(
                response=resp, model=model, reason="invalid_json", error=e
            )
            self._record_failure("invalid_json")
            raise OpenRouterRetryableError(
                "Malformed response from OpenRouter (invalid JSON)",
                code="invalid_json",
            ) from e

        try:
            content = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            self._log_malformed_response(
                response=resp,
                model=model,
                reason="invalid_payload",
                error=e,
                parsed=data,
            )
            self._record_failure("invalid_payload")
            raise OpenRouterRetryableError(
                "Malformed response from OpenRouter (missing content)",
                code="invalid_payload",
            ) from e

        if not content:
            self._log_malformed_response(
                response=resp,
                model=model,
                reason="empty_content",
                parsed=data,
            )
            self._record_failure("empty_content")
            raise OpenRouterRetryableError(
                "Malformed response from OpenRouter (empty content)",
                code="empty_content",
            )

        # Record success for circuit breaker
        self._on_api_success()
        return content

    def _chunk_by_sections(
        self,
        extraction_result: Dict[str, Any],
        max_tokens: int = SYNTHESIS_MAX_TOKENS_PER_CHUNK,
    ) -> List[Dict[str, Any]]:
        """
        Chunk paper by logical sections, keeping each section together when possible.

        Args:
            extraction_result: Result from extract_from_pdf_synthesis()
            max_tokens: Maximum tokens per chunk

        Returns:
            List of chunks, each with:
            - 'sections': List of section dicts included in this chunk
            - 'token_estimate': Estimated token count
            - 'chunk_index': Position in sequence
        """
        sections = extraction_result.get("sections", [])
        if not sections:
            # Fallback: treat raw_paragraphs as single section
            sections = [
                {
                    "name": "Content",
                    "paragraphs": extraction_result.get("raw_paragraphs", []),
                }
            ]

        chunks: List[Dict[str, Any]] = []
        current_chunk: Dict[str, Any] = {"sections": [], "token_estimate": 0}

        for section in sections:
            # Calculate section size
            section_text = section["name"] + "\n\n" + "\n\n".join(section["paragraphs"])
            section_tokens = estimate_tokens(section_text)

            # Would adding this section exceed the limit?
            if current_chunk["token_estimate"] + section_tokens > max_tokens:
                # Save current chunk if it has content
                if current_chunk["sections"]:
                    current_chunk["chunk_index"] = len(chunks)
                    chunks.append(current_chunk)

                # Handle oversized sections by splitting
                if section_tokens > max_tokens:
                    split_chunks = self._split_large_section(section, max_tokens)
                    for sc in split_chunks:
                        sc["chunk_index"] = len(chunks)
                        chunks.append(sc)
                    current_chunk = {"sections": [], "token_estimate": 0}
                else:
                    # Start new chunk with this section
                    current_chunk = {
                        "sections": [section],
                        "token_estimate": section_tokens,
                    }
            else:
                # Add to current chunk
                current_chunk["sections"].append(section)
                current_chunk["token_estimate"] += section_tokens

        # Don't forget the last chunk
        if current_chunk["sections"]:
            current_chunk["chunk_index"] = len(chunks)
            chunks.append(current_chunk)

        return chunks

    def _split_large_section(
        self, section: Dict[str, Any], max_tokens: int
    ) -> List[Dict[str, Any]]:
        """Split an oversized section into multiple chunks."""
        paragraphs = section["paragraphs"]
        section_name = section["name"]

        chunks: List[Dict[str, Any]] = []
        current_paras: List[str] = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = estimate_tokens(para)

            if current_tokens + para_tokens > max_tokens and current_paras:
                # Create chunk from accumulated paragraphs
                chunks.append(
                    {
                        "sections": [
                            {
                                "name": f"{section_name} (continued)"
                                if chunks
                                else section_name,
                                "paragraphs": current_paras,
                            }
                        ],
                        "token_estimate": current_tokens,
                    }
                )
                current_paras = [para]
                current_tokens = para_tokens
            else:
                current_paras.append(para)
                current_tokens += para_tokens

        if current_paras:
            chunks.append(
                {
                    "sections": [
                        {
                            "name": f"{section_name} (continued)"
                            if chunks
                            else section_name,
                            "paragraphs": current_paras,
                        }
                    ],
                    "token_estimate": current_tokens,
                }
            )

        return chunks

    def translate_synthesis_mode(
        self,
        extraction_result: Dict[str, Any],
        dry_run: bool = False,
        glossary_override: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Translate using synthesis mode - prioritizes readability over structure.

        Args:
            extraction_result: Output from extract_from_pdf_synthesis()
            dry_run: Skip actual API calls if True
            glossary_override: Custom glossary entries

        Returns:
            Dict with:
            - 'body_md': Full Markdown translation
            - 'sections_translated': Number of sections processed
            - 'chunks_used': Number of API chunks
        """
        model = self.model
        glossary = glossary_override if glossary_override is not None else self.glossary

        chunks = self._chunk_by_sections(extraction_result)
        total_chunks = len(chunks)

        log(f"Synthesis mode: processing {total_chunks} chunks")

        translated_parts: List[str] = []

        for chunk in chunks:
            # Build chunk content
            chunk_content = ""
            for section in chunk["sections"]:
                chunk_content += f"\n\n## {section['name']}\n\n"
                chunk_content += "\n\n".join(section["paragraphs"])

            # Mask math and citations
            masked_content, mappings = mask_math(chunk_content)

            # Build prompt
            chunk_idx = chunk.get("chunk_index", 0)
            position_hint = (
                f"(Part {chunk_idx + 1} of {total_chunks})" if total_chunks > 1 else ""
            )

            user_prompt = f"""Translate this section of a Chinese academic paper into fluent English. {position_hint}

---
{masked_content}
---

Remember: Produce flowing, readable academic English. Merge fragments into complete paragraphs. Skip obvious garbage/watermarks."""

            if dry_run:
                translated = masked_content  # Return masked as-is for dry run
            else:
                # Circuit breaker check and success/failure tracking now in
                # _execute_openrouter_request (called by _call_openrouter_synthesis)
                translated = self._call_openrouter_synthesis(
                    user_prompt, model, glossary
                )

            # Verify math preservation
            if not verify_token_parity(mappings, translated):
                log(
                    f"Warning: Math placeholder mismatch in chunk {chunk_idx + 1}: "
                    f"expected {len(mappings)} placeholders"
                )
                # Don't fail - just log warning for synthesis mode

            # Unmask
            unmasked = unmask_math(translated, mappings)

            translated_parts.append(unmasked)

        # Combine all parts
        full_body_md = "\n\n".join(translated_parts)

        return {
            "body_md": full_body_md,
            "sections_translated": sum(len(c["sections"]) for c in chunks),
            "chunks_used": total_chunks,
        }

    def translate_record_synthesis(
        self,
        record: Dict[str, Any],
        dry_run: bool = False,
        glossary_override: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Translate a record using synthesis mode for readable output.

        This is the synthesis-mode equivalent of translate_record().

        Args:
            record: Record to translate
            dry_run: If True, skip actual translation
            glossary_override: Custom glossary entries

        Returns:
            Translated record dict
        """
        from ..body_extract import extract_from_pdf_synthesis

        paper = Paper.from_dict(record)
        self._active_paper_id = paper.id

        try:
            # Create translation from paper
            translation = Translation.from_paper(paper)

            # Translate title and abstract (use regular method - these are short)
            title_src = paper.title or ""
            abstract_src = paper.abstract or ""

            translation.title_en = self.translate_field(
                title_src, dry_run=dry_run, glossary_override=glossary_override
            )
            translation.abstract_en = self.translate_field(
                abstract_src, dry_run=dry_run, glossary_override=glossary_override
            )

            # Translate authors
            if paper.creators:
                translation.creators_en = []
                for creator in paper.creators:
                    if creator:
                        try:
                            translation.creators_en.append(
                                self.translate_field(
                                    creator,
                                    dry_run=dry_run,
                                    glossary_override=glossary_override,
                                )
                            )
                        except Exception as e:
                            log(
                                f"Warning: Failed to translate creator '{creator}': {e}"
                            )
                            translation.creators_en.append(creator)

            # Translate subjects
            if paper.subjects:
                translation.subjects_en = []
                for subject in paper.subjects:
                    if subject:
                        try:
                            translation.subjects_en.append(
                                self.translate_field(
                                    subject,
                                    dry_run=dry_run,
                                    glossary_override=glossary_override,
                                )
                            )
                        except Exception as e:
                            log(
                                f"Warning: Failed to translate subject '{subject}': {e}"
                            )
                            translation.subjects_en.append(subject)

            # Get PDF path for synthesis extraction
            files = record.get("files") or {}
            pdf_path = files.get("pdf_path")

            has_full_body = False
            body_md = ""
            extraction_stats: Dict[str, Any] = {}

            if pdf_path:
                # Use synthesis extraction
                try:
                    extraction = extract_from_pdf_synthesis(pdf_path)
                except Exception as e:
                    log(f"Error extracting from PDF {pdf_path}: {e}")
                    extraction = None

                if extraction:
                    extraction_stats = extraction.get("stats", {})
                    log(
                        f"Synthesis extraction for {paper.id}: "
                        f"{extraction_stats.get('original_lines', 0)} lines -> "
                        f"{extraction_stats.get('merged_paragraphs', 0)} paragraphs"
                    )

                    # Translate with synthesis mode
                    result = self.translate_synthesis_mode(
                        extraction,
                        dry_run=dry_run,
                        glossary_override=glossary_override,
                    )
                    body_md = result["body_md"]
                    has_full_body = bool(body_md)

            # Build translation dict
            translation_dict = translation.to_dict()
            translation_dict["body_md"] = body_md
            translation_dict["_has_full_body"] = has_full_body
            translation_dict["_synthesis_mode"] = True
            translation_dict["_extraction_stats"] = extraction_stats
            translation_dict["_body_source"] = "pdf" if pdf_path else "none"

            # Cost tracking
            in_toks = estimate_tokens(title_src) + estimate_tokens(abstract_src)
            out_toks = estimate_tokens(translation.title_en or "") + estimate_tokens(
                translation.abstract_en or ""
            )
            if body_md:
                # Estimate input from raw paragraphs
                raw_paras = (
                    extraction_stats.get("merged_paragraphs", 0)
                    if extraction_stats
                    else 0
                )
                in_toks += raw_paras * ESTIMATED_TOKENS_PER_PARAGRAPH
                out_toks += estimate_tokens(body_md)

            cost = compute_cost(self.model, in_toks, out_toks, self.config)
            append_cost_log(paper.id, self.model, in_toks, out_toks, cost)

            return translation_dict

        finally:
            self._active_paper_id = None
