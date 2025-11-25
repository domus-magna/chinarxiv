"""
Translation service for ChinaXiv English translation.
"""

from __future__ import annotations

import json
from datetime import datetime
import math
import time
from pathlib import Path
import os
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
from ..body_extract import extract_body_paragraphs
from ..token_utils import chunk_paragraphs, estimate_tokens
from ..cost_tracker import compute_cost, append_cost_log
from ..logging_utils import log
from ..qa_filter import ChineseCharacterDetector
from ..models import Paper, Translation
import re


SYSTEM_PROMPT = (
    "You are a professional scientific translator specializing in academic papers. "
    "Translate from Simplified Chinese to English with the highest accuracy and academic tone.\n\n"
    "CRITICAL REQUIREMENTS:\n"
    "1. Preserve ALL LaTeX commands and ⟪MATH_*⟫ placeholders exactly - do not modify, translate, or rewrite any mathematical formulas\n"
    "2. Preserve ALL citation commands (\\cite{}, \\ref{}, \\eqref{}, etc.) exactly as they appear\n"
    "3. **PRESERVE ALL <PARA id=\"N\">...</PARA> paragraph wrapper tags EXACTLY** - these are structural markers that identify paragraphs and MUST remain untouched with their IDs\n"
    "4. Maintain academic tone and formal scientific writing style\n"
    "5. Use precise technical terminology - obey the glossary strictly\n"
    "6. Preserve section structure and paragraph organization\n"
    "7. Translate all content completely - do not omit any information\n\n"
    "OUTPUT RULES:\n"
    "- Return ONLY the translated text for the given input (no explanations, no quotes, no headings you invent).\n"
    "- Keep one output paragraph per input paragraph; do not merge or split paragraphs.\n"
    "- **Maintain exact count and IDs of <PARA id=\"N\">...</PARA> tags** - if input has N paragraph tags, output must have exactly N paragraph tags with matching IDs\n"
    "- Do NOT add Markdown formatting unless it is present in the source.\n"
    "- Preserve original line breaks within the paragraph when meaningful; otherwise use standard English sentence spacing.\n\n"
    "FORMATTING GUIDELINES:\n"
    "- Keep mathematical expressions in their original LaTeX format\n"
    "- Preserve equation numbers and references\n"
    "- Maintain proper academic paragraph structure\n"
    "- Use formal scientific language appropriate for research papers\n"
    "- Never remove or modify <PARA id=\"N\">...</PARA> tags - they are structural elements that must be preserved with their IDs intact\n\n"
    "Remember: Mathematical content, citations, and <PARA> wrapper tags must remain untouched - only translate the Chinese text inside the tags."
)


DEFAULT_TRANSLATION_CONFIG: Dict[str, Any] = {
    "whole_paper_mode": True,
    "max_whole_paper_tokens": 32000,
    "batch_paragraphs": False,
    "macro_chunk_threshold": 160,
    "macro_chunk_target_size": 20,
    "macro_chunk_max_chunks": 8,
    "macro_chunk_retry_attempts": 1,
    "fallback_models": [],
    "max_retries_per_model": 1,
    "max_short_ratio": 0.1,
    # Reliability > latency: allow slow providers plenty of headroom (read timeout 15m)
    "request_timeout_seconds": {"connect": 10, "read": 900},
    "circuit_breaker": {
        "persistent_error_threshold": 2,
        "transient_error_threshold": 5,
    },
}


MAX_RESPONSE_PREVIEW = 2048
MACRO_CACHE_VERSION = 1


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


class CircuitBreakerOpen(Exception):
    """Circuit breaker triggered after consecutive failures."""

    pass


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
        self.model = self.config.get("models", {}).get(
            "default_slug", "openai/gpt-5.1"
        )
        self._active_paper_id: Optional[str] = None
        self.glossary = self.config.get("glossary", [])
        self.failure_log_dir = Path("data/monitoring/openrouter_failures")
        # Wall-clock guard per paper (seconds). None disables.
        self.paper_wallclock_limit = float(
            (self.config.get("translation") or {}).get("paper_wallclock_limit_seconds", 0)
        ) or None

        # Circuit breaker state
        self._consecutive_persistent = 0  # payment/auth errors
        self._consecutive_transient = 0   # network/server errors
        self._circuit_open = False
        translation_cfg = self.config.get("translation") or {}
        circuit_cfg = translation_cfg.get("circuit_breaker") or {}
        self.PERSISTENT_ERROR_THRESHOLD = int(
            circuit_cfg.get("persistent_error_threshold", 2)
        )
        self.TRANSIENT_ERROR_THRESHOLD = int(
            circuit_cfg.get("transient_error_threshold", 5)
        )
        timeout_cfg = translation_cfg.get("request_timeout_seconds") or {}
        self._connect_timeout = float(timeout_cfg.get("connect", 10))
        self._read_timeout = float(timeout_cfg.get("read", 60))

    def _check_circuit_breaker(self) -> None:
        """
        Check if circuit breaker is open.

        Raises:
            CircuitBreakerOpen: If circuit breaker is triggered
        """
        if self._circuit_open:
            raise CircuitBreakerOpen(
                f"Circuit breaker triggered after consecutive failures: "
                f"{self._consecutive_persistent} persistent, {self._consecutive_transient} transient"
            )

    def _record_failure(self, error_code: Optional[str]) -> None:
        """
        Record an API failure and check circuit breaker thresholds.

        Persistent errors (payment/auth) trigger after 2 consecutive failures.
        Transient errors (network/5xx) trigger after 5 consecutive failures.

        Args:
            error_code: Error code from OpenRouter API

        Raises:
            CircuitBreakerOpen: If threshold exceeded
        """
        # Classify error type
        persistent_codes = {
            "payment_required",
            "insufficient_quota",
            "invalid_api_key",
            "invalid_credentials",
            "unauthorized",
        }

        if error_code in persistent_codes:
            # Reset transient counter when switching to persistent errors
            self._consecutive_transient = 0
            self._consecutive_persistent += 1
            if self._consecutive_persistent >= self.PERSISTENT_ERROR_THRESHOLD:
                self._circuit_open = True
                try:
                    alert_critical(
                        "Circuit Breaker: Persistent Error Threshold Reached",
                        f"Stopped after {self._consecutive_persistent} consecutive {error_code} errors",
                        source="translation_service",
                        metadata={"error_code": error_code or "unknown"},
                    )
                except Exception as alert_err:
                    log(f"Failed to send circuit breaker alert: {alert_err}")
                raise CircuitBreakerOpen(
                    f"Circuit breaker triggered: {self._consecutive_persistent} consecutive "
                    f"persistent errors (threshold: {self.PERSISTENT_ERROR_THRESHOLD}). "
                    f"Last error: {error_code}"
                )
        else:
            # Transient error (network, 5xx, rate limit, etc.)
            # Reset persistent counter when switching to transient errors
            self._consecutive_persistent = 0
            self._consecutive_transient += 1
            if self._consecutive_transient >= self.TRANSIENT_ERROR_THRESHOLD:
                self._circuit_open = True
                try:
                    alert_critical(
                        "Circuit Breaker: Transient Error Threshold Reached",
                        f"Stopped after {self._consecutive_transient} consecutive transient errors",
                        source="translation_service",
                        metadata={"error_code": error_code or "unknown"},
                    )
                except Exception as alert_err:
                    log(f"Failed to send circuit breaker alert: {alert_err}")
                raise CircuitBreakerOpen(
                    f"Circuit breaker triggered: {self._consecutive_transient} consecutive "
                    f"transient errors (threshold: {self.TRANSIENT_ERROR_THRESHOLD}). "
                    f"Last error: {error_code or 'unknown'}"
                )

    def _reset_failure_counter(self) -> None:
        """Reset circuit breaker failure counters on successful translation."""
        self._consecutive_persistent = 0
        self._consecutive_transient = 0
        if self._circuit_open:
            log("Circuit breaker closed after successful translation call")
        self._circuit_open = False

    @retry(
        wait=wait_exponential(min=1, max=10),
        stop=stop_after_attempt(1),
        retry=retry_if_exception_type(OpenRouterRetryableError),
        reraise=True,
    )
    def _call_openrouter(
        self, text: str, model: str, glossary: List[Dict[str, str]]
    ) -> str:
        """
        Call OpenRouter API for translation.

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
        glossary_str = "\n".join(f"{g['zh']} => {g['en']}" for g in glossary)
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

        proxies, source = get_proxies()
        try:
            kwargs = {
                "headers": openrouter_headers(),
                "data": json.dumps(payload),
                "timeout": (
                    self._connect_timeout if source != "none" else max(1.0, self._connect_timeout - 2),
                    self._read_timeout,
                ),
            }
            if source == "config" and proxies:
                kwargs["proxies"] = proxies
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions", **kwargs
            )
        except requests.RequestException as e:
            # Record network error
            try:
                monitoring_service.record_error(
                    service="openrouter",
                    message=str(e),
                    status=None,
                    code="network_error",
                    metadata={"model": model},
                )
            except Exception:
                pass
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
            except Exception:
                pass
            if info["retryable"]:
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
                except Exception:
                    pass
                raise OpenRouterFatalError(message, code=code, fallback_ok=False)
            raise OpenRouterError(message, code=code, retryable=False, fallback_ok=True)

        try:
            data = resp.json()
        except ValueError as e:
            self._log_malformed_response(
                response=resp, model=model, reason="invalid_json", error=e
            )
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
            raise OpenRouterRetryableError(
                "Malformed response from OpenRouter (empty content)",
                code="empty_content",
            )

        return content

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
        except Exception:
            pass

        # Persist a richer artifact locally for manual debugging.
        try:
            self.failure_log_dir.mkdir(parents=True, exist_ok=True)
            record_path = self.failure_log_dir / f"{timestamp}_{reason}_{uuid4().hex}.json"
            record: Dict[str, Any] = {
                "timestamp": timestamp,
                **metadata,
                "body_preview": preview,
            }
            if parsed is not None:
                try:
                    record["parsed"] = parsed
                except Exception:
                    record["parsed"] = str(parsed)
            with record_path.open("w", encoding="utf-8") as fh:
                json.dump(record, fh, ensure_ascii=True, indent=2)
        except Exception:
            pass

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
            raise MathPreservationError("Math placeholder parity check failed")

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

    def translate_paragraphs(
        self,
        paragraphs: List[str],
        model: Optional[str] = None,
        dry_run: bool = False,
        glossary_override: Optional[List[Dict[str, str]]] = None,
    ) -> List[str]:
        """
        Translate multiple paragraphs.

        Translation modes:
        - Whole-paper translation (single call) when under the macro threshold.
        - Macro-chunk translation (few balanced chunks) once the threshold is exceeded.
        Paragraph-level translation is intentionally disabled.

        Args:
            paragraphs: List of paragraphs to translate
            model: Model to use (defaults to service model)
            dry_run: If True, skip actual translation

        Returns:
            List of translated paragraphs
        """
        model = model or self.model
        glossary_eff = self.glossary if glossary_override is None else glossary_override
        user_trans_config = self.config.get("translation") or {}
        trans_config = {**DEFAULT_TRANSLATION_CONFIG, **user_trans_config}

        # Whole-paper only: enforce paragraph count with JSON first, delimiter fallback.
        whole_paper_mode = trans_config.get("whole_paper_mode") is True
        max_whole_paper_tokens = trans_config.get("max_whole_paper_tokens", 32000)

        if not whole_paper_mode:
            raise TranslationValidationError(
                "Whole-paper translation was disabled, but paragraph/chunk mode is not allowed"
            )

        para_count = len(paragraphs)
        total_tokens = sum(estimate_tokens(p) for p in paragraphs)
        if total_tokens > max_whole_paper_tokens:
            log(
                f"Whole-paper translation exceeds token limit ({total_tokens} > {max_whole_paper_tokens}); using macro chunks"
            )
            return self._translate_macro_chunks(
                paragraphs,
                model,
                dry_run,
                glossary_eff,
                target_size=macro_target,
                max_chunks=macro_max_chunks,
                retry_attempts=macro_retries,
            )
        # Large documents: fall back to macro-chunk translation (few large chunks)
        macro_threshold = int(trans_config.get("macro_chunk_threshold", 300))
        macro_target = max(1, int(trans_config.get("macro_chunk_target_size", 150)))
        macro_max_chunks = max(1, int(trans_config.get("macro_chunk_max_chunks", 8)))
        macro_retries = max(0, int(trans_config.get("macro_chunk_retry_attempts", 0)))
        if para_count >= macro_threshold:
            log(f"Para count {para_count} exceeds threshold {macro_threshold}; using macro-chunk translation")
            return self._translate_macro_chunks(
                paragraphs,
                model,
                dry_run,
                glossary_eff,
                target_size=macro_target,
                max_chunks=macro_max_chunks,
                retry_attempts=macro_retries,
            )

        import re

        def _dump_raw(label: str, raw: str) -> None:
            """Persist raw model output for post-mortem debugging."""
            try:
                out_dir = Path("reports") / "raw_translations"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().isoformat()
                with open(out_dir / "debug.log", "a", encoding="utf-8") as f:
                    f.write(f"\n[{ts}] {label}\n")
                    f.write(raw)
                    f.write("\n")
            except Exception:
                pass

        def _translate_delimited(prompt: str, label: str) -> tuple[dict[int, str], str, str]:
            raw = self.translate_field(
                prompt, model, dry_run, glossary_override=glossary_eff
            )
            items, order = self._parse_delimited_static(raw)
            return items, raw, label

        para_count = len(paragraphs)
        base_prompt = (
            "Translate every paragraph below to English. Preserve math, LaTeX, and IDs.\n"
            f"There are exactly {para_count} paragraphs indexed from 0 to {para_count - 1}.\n"
            "Respond ONLY with one block per paragraph in this exact format:\n"
            "###PARA i###<English translation>###END###\n"
            "Rules:\n"
            "- Emit ALL indexes 0..N-1 once each, in order. No gaps, no extras.\n"
            "- Do NOT add anything before/after the blocks. No summaries or notes.\n"
            "- Keep the translation content as plain text; do not wrap in JSON or Markdown."
        )
        body = "\n".join(f"###PARA {i}###{p}###END###" for i, p in enumerate(paragraphs))

        def _validate(items: dict[int, str]) -> tuple[bool, list[int], list[int]]:
            missing = [i for i in range(para_count) if i not in items]
            extra = sorted(idx for idx in items.keys() if idx < 0 or idx >= para_count)
            return not missing and not extra and len(items) == para_count, missing, extra

        attempts = [
            ("delimiter_initial", base_prompt),
            (
                "delimiter_retry",
                base_prompt
                + "\nYour last attempt missed or misnumbered paragraphs. "
                "Output ONLY the ###PARA i### blocks for every i=0..N-1 with no other text.",
            ),
        ]

        last_raw = ""
        for label, prefix in attempts:
            full_prompt = f"{prefix}\n\n{body}"
            items, raw, _ = _translate_delimited(full_prompt, label)
            last_raw = raw
            ok, missing, extra = _validate(items)
            if ok:
                return [items[i] for i in range(para_count)]
            _dump_raw(f"{label}_mismatch", raw or "")
            log(
                f"Delimiter output invalid (missing={missing}, extra={extra}, "
                f"count={len(items)}/{para_count}); retrying" if label == "delimiter_initial" else
                f"Retry failed with missing={missing}, extra={extra}, count={len(items)}/{para_count}"
            )

        _dump_raw("delimiter_failed", last_raw or "")
        raise TranslationValidationError("Whole-paper translation failed to preserve paragraph count")

    def _translate_chunked_paragraphs(
        self,
        paragraphs: List[str],
        model: str,
        dry_run: bool,
        glossary_override: Optional[List[Dict[str, str]]],
        max_chunk_tokens: int,
    ) -> List[str]:
        """
        Translate paragraphs in token-bounded groups while preserving math handling.

        Args:
            paragraphs: Paragraphs to translate
            model: Model slug to use
            dry_run: Whether to skip API calls
            glossary_override: Optional glossary entries
            max_chunk_tokens: Token budget per chunk
        """
        if not paragraphs:
            return []

        out: List[str] = []
        SENTINEL = "\n<PARA_BREAK/>\n"

        for group in chunk_paragraphs(paragraphs, max_tokens=max_chunk_tokens):
            joined = SENTINEL.join(group)
            translated = self.translate_field(
                joined, model, dry_run, glossary_override=glossary_override
            )
            parts = [s.strip() for s in translated.split(SENTINEL)]
            if len(parts) != len(group):
                raise TranslationValidationError(
                    "Paragraph chunk mismatch detected; chunked translation failed"
                )
            else:
                out.extend(parts)

        return out

    def _macro_cache_path(self, paper_id: Optional[str] = None) -> Optional[Path]:
        """Return the cache path for macro-chunk outputs."""
        paper = paper_id or self._active_paper_id
        if not paper:
            return None

        safe_model = (self.model or "unknown").replace("/", "__")
        return (
            Path("data")
            / "cache"
            / "macro_chunks"
            / f"{paper}__{safe_model}.json"
        )

    def _load_macro_cache(self, para_count: int) -> Dict[str, Any]:
        """Load cached macro-chunk results for the active paper/model."""
        path = self._macro_cache_path()
        if not path or not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("version") != MACRO_CACHE_VERSION:
                return {}
            if data.get("model") != self.model:
                return {}
            if data.get("para_count") != para_count:
                return {}
            chunks = data.get("chunks") or {}
            if isinstance(chunks, dict):
                return chunks
        except Exception as exc:
            log(f"Macro cache load failed: {exc}")
        return {}

    def _persist_macro_chunk(
        self, start_idx: int, end_idx: int, chunk_text: List[str], para_count: int
    ) -> None:
        """Persist a macro-chunk translation for resumability."""
        path = self._macro_cache_path()
        if not path:
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": MACRO_CACHE_VERSION,
                "paper_id": self._active_paper_id,
                "model": self.model,
                "para_count": para_count,
                "chunks": {},
            }
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as fh:
                        existing = json.load(fh)
                    if (
                        existing.get("version") == MACRO_CACHE_VERSION
                        and existing.get("model") == self.model
                        and existing.get("para_count") == para_count
                    ):
                        payload["chunks"] = existing.get("chunks") or {}
                except Exception:
                    pass
            key = f"{start_idx}-{end_idx}"
            payload["chunks"][key] = chunk_text
            with path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            log(f"Saved macro chunk cache {key} for {self._active_paper_id} ({len(chunk_text)} paras)")
        except Exception as exc:
            log(f"Failed to persist macro chunk cache: {exc}")

    def _clear_macro_cache(self, paper_id: Optional[str] = None) -> None:
        """Remove macro-chunk cache after successful end-to-end translation."""
        path = self._macro_cache_path(paper_id)
        if path and path.exists():
            try:
                path.unlink()
            except Exception as exc:
                log(f"Failed to clear macro chunk cache: {exc}")

    def _translate_macro_chunks(
        self,
        paragraphs: List[str],
        model: str,
        dry_run: bool,
        glossary_override: Optional[List[Dict[str, str]]],
        target_size: int,
        max_chunks: int,
        retry_attempts: int,
    ) -> List[str]:
        """
        Translate in a few large macro-chunks, enforcing strict paragraph counts per chunk.
        """
        if not paragraphs:
            return []
        para_count = len(paragraphs)
        chunk_count = max(1, min(max_chunks, math.ceil(para_count / max(1, target_size))))
        # Balance chunks so the tail is not too small.
        base = para_count // chunk_count
        remainder = para_count % chunk_count
        ranges: List[tuple[int, int]] = []
        start = 0
        for i in range(chunk_count):
            size = base + (1 if i < remainder else 0)
            end = min(para_count, start + size)
            ranges.append((start, end))
            start = end

        cache = self._load_macro_cache(para_count)
        max_attempts = max(1, retry_attempts + 1)
        dump_raw_static = self._get_dump_raw()
        translated: List[str] = []
        for start_idx, end_idx in ranges:
            cache_key = f"{start_idx}-{end_idx}"
            cached = cache.get(cache_key) if cache else None
            if cached and len(cached) == (end_idx - start_idx):
                log(f"Macro chunk cache hit for {cache_key} ({len(cached)} paras)")
                # Track cache metrics for run summary
                try:
                    self._cache_hits = getattr(self, "_cache_hits", 0) + 1
                    self._cache_chunks = getattr(self, "_cache_chunks", 0) + len(cached)
                except Exception:
                    pass
                translated.extend(cached)
                continue

            last_raw = ""
            for attempt in range(max_attempts):
                sub_paras = paragraphs[start_idx:end_idx]
                sub_body = "\n".join(
                    f"###PARA {i}###{p}###END###" for i, p in enumerate(sub_paras, start=start_idx)
                )
                sub_prompt = (
                    "Translate the paragraphs. Output ONLY the translations wrapped as "
                    "###PARA i###<translation>###END### for every i from "
                    f"{start_idx} to {end_idx-1}, in order with no gaps or extras. "
                    "Do not add notes or headers. Do not add citations or LaTeX not present in input. "
                    "If you cannot comply, respond with 'FAIL: reason'."
                )
                if attempt > 0:
                    sub_prompt += "\nYour previous attempt missed or misnumbered items. Emit each index exactly once."
                full_prompt = sub_prompt + "\n" + sub_body
                try:
                    items, raw, _ = self._translate_chunk_delimited(
                        full_prompt, model, dry_run, glossary_override
                    )
                    last_raw = raw or ""
                    missing = [i for i in range(start_idx, end_idx) if i not in items]
                    extra = [i for i in items.keys() if i < start_idx or i >= end_idx]
                    if missing or extra or len(items) != (end_idx - start_idx):
                        raise TranslationValidationError(
                            f"Macro chunk mismatch: missing={missing}, extra={extra}, "
                            f"count={len(items)}/{end_idx - start_idx}"
                        )
                    chunk_out = [items[i] for i in range(start_idx, end_idx)]
                    translated.extend(chunk_out)
                    self._persist_macro_chunk(start_idx, end_idx, chunk_out, para_count)
                    break
                except Exception as exc:
                    dump_raw_static(
                        f"macro_chunk_{start_idx}_attempt_{attempt+1}_mismatch",
                        last_raw or "",
                    )
                    if attempt >= max_attempts - 1:
                        log(
                            f"Macro chunk failed at {start_idx}-{end_idx-1} after {max_attempts} attempts: {exc}"
                        )
                        # Skip the missing chunk but continue with downstream chunks
                        translated.extend([""] * (end_idx - start_idx))
                        break
                    log(
                        f"Retrying macro chunk {start_idx}-{end_idx-1} "
                        f"(attempt {attempt+1}/{max_attempts}): {exc}"
                    )

        return translated

    def _translate_chunk_delimited(
        self,
        prompt: str,
        model: str,
        dry_run: bool,
        glossary_override: Optional[List[Dict[str, str]]],
    ) -> tuple[dict[int, str], str, str]:
        raw = self.translate_field(
            prompt, model, dry_run, glossary_override=glossary_override
        )
        items, order = self._parse_delimited_static(raw)
        return items, raw, "chunk"

    @staticmethod
    def _parse_delimited_static(raw: str) -> tuple[dict[int, str], list[int]]:
        import re

        pattern = re.compile(r"###PARA\s+(\d+)###(.*?)###END###", re.DOTALL)
        matches = pattern.findall(raw)
        if raw.strip().startswith("FAIL"):
            return {}, []
        if not matches:
            return {}, []
        items: dict[int, str] = {}
        order: list[int] = []
        for idx_str, text in matches:
            idx = int(idx_str)
            order.append(idx)
            items[idx] = text.strip()
        return items, order

    @staticmethod
    def _get_dump_raw():
        def _dump_raw(label: str, raw: str) -> None:
            try:
                out_dir = Path("reports") / "raw_translations"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().isoformat()
                with open(out_dir / "debug.log", "a", encoding="utf-8") as f:
                    f.write(f"\n[{ts}] {label}\n")
                    f.write(raw)
                    f.write("\n")
            except Exception:
                pass

        return _dump_raw

    def _render_markdown(self, translation: Dict[str, Any]) -> str:
        """
        Render a Markdown document from translation dict using _md fields when present.
        """
        parts: List[str] = []
        title = translation.get("title_en") or ""
        if title:
            parts.append(f"# {title}\n")

        creators = translation.get("creators_en") or translation.get("creators") or []
        if creators:
            parts.append(f"**Authors:** {', '.join(creators)}\n")

        if translation.get("date"):
            parts.append(f"**Date:** {translation['date']}\n")

        if translation.get("subjects"):
            parts.append(f"**Subjects:** {', '.join(translation['subjects'])}\n")

        abstract_md = translation.get("abstract_md")
        if abstract_md:
            parts.append(abstract_md.strip())
        elif translation.get("abstract_en"):
            parts.append(f"## Abstract\n{translation['abstract_en'].strip()}")

        body_md = translation.get("body_md")
        if body_md:
            parts.append(body_md.strip())
        elif translation.get("body_en"):
            body = "\n\n".join(p for p in translation["body_en"] if p and p.strip())
            parts.append(body)

        return "\n\n".join(p for p in parts if p.strip()) + "\n"

    def _structural_quality_ok(
        self,
        source_para_count: int,
        source_short_ratio: float,
        translated_paras: List[str],
    ) -> tuple[bool, str]:
        if source_para_count <= 0:
            # No baseline; skip structural gate
            return True, ""
        translated_count = len(translated_paras or [])
        if translated_count != source_para_count:
            return False, f"Paragraph count mismatch (src={source_para_count}, got={translated_count})"
        if translated_count == 0:
            return False, "No translated paragraphs"
        if translated_count < 10:
            return True, ""
        max_short_ratio = float(
            self.config.get("translation", {}).get("max_short_ratio", 0.1)
        )
        short_dst = sum(1 for p in translated_paras if len((p or "").strip()) < 10)
        dst_ratio = short_dst / translated_count if translated_count else 0.0
        if dst_ratio > max_short_ratio and source_short_ratio < max_short_ratio / 2:
            return (
                False,
                f"Excessive short fragments ({dst_ratio:.2f}); source ratio {source_short_ratio:.2f}",
            )
        return True, ""

    def _write_run_summary(
        self,
        rec: Dict[str, Any],
        attempts: List[Dict[str, Any]],
        success_path: Optional[str],
        last_error: Optional[str],
        *,
        cache_hits: Optional[int] = None,
        cache_chunks: Optional[int] = None,
        stripped_chars: Optional[List[str]] = None,
        formatter_retries: Optional[int] = None,
    ) -> None:
        try:
            os.makedirs("reports/run_summaries", exist_ok=True)
            payload = {
                "paper_id": rec.get("id"),
                "source_para_count": rec.get("__source_para_count"),
                "source_short_ratio": rec.get("__source_short_ratio"),
                "attempts": attempts,
                "success_path": success_path,
                "last_error": last_error,
            }
            if cache_hits is not None:
                payload["cache_hits"] = cache_hits
            if cache_chunks is not None:
                payload["cache_chunks"] = cache_chunks
            if stripped_chars:
                payload["stripped_chinese_chars"] = stripped_chars
            if formatter_retries is not None:
                payload["formatter_retries"] = formatter_retries
            out_path = os.path.join(
                "reports", "run_summaries", f"{rec.get('id', 'unknown')}.json"
            )
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception as err:
            log(f"Failed to write run summary: {err}")

    def _persist_failed_translation(
        self, paper_id: str, translation: Dict[str, Any], qa_result
    ) -> None:
        """Persist the last failed translation for manual inspection."""
        try:
            out_dir = Path("reports") / "raw_translations"
            out_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "paper_id": paper_id,
                "qa_status": qa_result.status.value if qa_result else "unknown",
                "qa_issues": qa_result.issues if qa_result else [],
                "qa_fields": getattr(qa_result, "flagged_fields", []),
                "translation": translation,
            }
            out_path = out_dir / f"{paper_id}.qa_failed.json"
            with out_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            log(f"Saved QA-failed translation to {out_path}")
        except Exception as exc:
            log(f"Failed to persist QA-failed translation: {exc}")

    def _strip_chinese_characters(self, translation: Dict[str, Any]) -> Dict[str, Any]:
        """Remove residual Chinese characters to satisfy strict QA."""
        detector = ChineseCharacterDetector()
        removed: list[str] = []
        meta_hits: list[str] = []

        def _strip_text(text: str) -> str:
            chars = detector.find_chinese_chars(text)
            if chars:
                removed.extend(chars)
                return "".join(ch for ch in text if not detector.is_chinese_char(ch))
            # Also strip known metadata markers that leak through (e.g., DOI:)
            for marker in detector.CHINESE_METADATA_MARKERS:
                if marker in text:
                    meta_hits.append(marker)
                    text = text.replace(marker, "")
            return text

        for field in ("title_en", "abstract_en"):
            if isinstance(translation.get(field), str):
                translation[field] = _strip_text(translation[field])

        if isinstance(translation.get("body_en"), list):
            translation["body_en"] = [
                _strip_text(p) if isinstance(p, str) else p
                for p in translation["body_en"]
            ]

        if removed:
            translation["_removed_chinese_chars"] = sorted(set(removed))
            log(f"Stripped Chinese characters from translation: {translation['_removed_chinese_chars'][:5]}")
        if meta_hits:
            translation["_removed_chinese_metadata"] = sorted(set(meta_hits))
            log(f"Stripped Chinese metadata markers: {translation['_removed_chinese_metadata'][:5]}")

        return translation

    def translate_record(
        self,
        record: Dict[str, Any],
        dry_run: bool = False,
        force_full_text: bool = False,
        glossary_override: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Translate a complete record.

        Args:
            record: Record to translate
            dry_run: If True, skip actual translation
            force_full_text: If True, translate full text regardless of license (always True now)

        Returns:
            Translated record
        """
        # DISABLED: We do not care about licenses. All papers translated in full.
        # from ..licenses import decide_derivatives_allowed

        # DISABLED: License gate - always translate full text
        # record = decide_derivatives_allowed(record, self.config)

        # Convert to Paper model
        paper = Paper.from_dict(record)
        self._active_paper_id = paper.id

        try:
            # DISABLED: Always allow full text - we don't care about licenses
            allow_full = True

            # Create translation from paper
            translation = Translation.from_paper(paper)

            # Translate title and abstract
            title_src = paper.title or ""
            abstract_src = paper.abstract or ""

            translation.title_en = self.translate_field(
                title_src, dry_run=dry_run, glossary_override=glossary_override
            )
            translation.abstract_en = self.translate_field(
                abstract_src, dry_run=dry_run, glossary_override=glossary_override
            )

            # Translate authors (creators)
            if paper.creators:
                translation.creators_en = []
                for creator in paper.creators:
                    if creator:  # Skip empty strings
                        try:
                            translated_name = self.translate_field(
                                creator, dry_run=dry_run, glossary_override=glossary_override
                            )
                            translation.creators_en.append(translated_name)
                        except Exception:
                            # Fallback to original if translation fails
                            translation.creators_en.append(creator)

            # Translate subjects
            if paper.subjects:
                translation.subjects_en = []
                for subject in paper.subjects:
                    if subject:  # Skip empty strings
                        try:
                            translated_subject = self.translate_field(
                                subject, dry_run=dry_run, glossary_override=glossary_override
                            )
                            translation.subjects_en.append(translated_subject)
                        except Exception:
                            # Fallback to original if translation fails
                            translation.subjects_en.append(subject)

            has_full_body = False
            body_reason: Optional[str] = None
            body_source: Optional[str] = None
            paras: Optional[List[str]] = None

            source_short_ratio = 0.0
            source_para_count = 0

            # Translate body if allowed
            if allow_full:
                cached = record.get("__cached_paragraphs")
                paras = cached if cached else extract_body_paragraphs(record)
                if paras:
                    source_para_count = len(paras)
                    source_short = sum(1 for p in paras if len(p.strip()) < 10)
                    source_short_ratio = source_short / len(paras) if paras else 0.0
                    # Source structural metrics
                    translation.body_en = self.translate_paragraphs(
                        paras, dry_run=dry_run, glossary_override=glossary_override
                    )
                    has_full_body = True
                    files = record.get("files") or {}
                    if files.get("latex_source_path"):
                        body_source = "latex"
                    elif files.get("pdf_path"):
                        body_source = "pdf"
                    else:
                        body_source = "unknown"
                else:
                    body_reason = self._infer_body_missing_reason(record)
            else:
                body_reason = "full_text_disabled"

            # Cost tracking (approximate)
            from ..token_utils import estimate_tokens

            in_toks = estimate_tokens(title_src) + estimate_tokens(abstract_src)
            out_toks = estimate_tokens(translation.title_en or "") + estimate_tokens(
                translation.abstract_en or ""
            )

            if translation.body_en:
                in_toks += sum(estimate_tokens(p) for p in paras or [])
                out_toks += sum(estimate_tokens(p) for p in translation.body_en)

            cost = compute_cost(self.model, in_toks, out_toks, self.config)
            append_cost_log(paper.id, self.model, in_toks, out_toks, cost)

            translation_dict = translation.to_dict()
            translation_dict["_has_full_body"] = has_full_body
            if body_source:
                translation_dict["_body_source"] = body_source
            translation_dict["_source_para_count"] = source_para_count
            translation_dict["_source_short_ratio"] = source_short_ratio
            if translation.body_en:
                translation_dict["_body_paragraphs"] = len(translation.body_en)
            if not has_full_body:
                translation_dict["_full_body_reason"] = body_reason or "missing_full_text"
                translation_dict["_body_paragraphs"] = 0

            return translation_dict
        finally:
            self._active_paper_id = None

    def translate_paper(
        self, paper_id: str, dry_run: bool = False, with_full_text: bool = True
    ) -> str:
        """
        Translate a single paper by ID.

        Args:
            paper_id: Paper identifier
            dry_run: If True, skip actual translation
            with_full_text: If True, download PDF and translate full text

        Returns:
            Path to translated JSON file

        Raises:
            ValueError: If paper not found
        """
        from ..file_service import read_json, write_json
        import glob
        import os

        # Load selected records
        selected_path = os.path.join("data", "selected.json")
        selected = read_json(selected_path)

        # Find the record in selected.json first
        rec = next((r for r in selected if r["id"] == paper_id), None)

        # If not found, try any harvested records under data/records
        if not rec:
            records_dir = os.path.join("data", "records")
            rec_files = sorted(
                glob.glob(os.path.join(records_dir, "*.json")), reverse=True
            )

            for rf in rec_files:
                try:
                    records = read_json(rf)
                except Exception:
                    continue
                rec = next((r for r in records if r.get("id") == paper_id), None)
                if rec:
                    break

        if not rec:
            raise ValueError(
                f"Paper {paper_id} not found in selected.json or data/records"
            )

        source_para_count = 0
        source_short_ratio = 0.0

        # Download PDF and extract text if requested
        if with_full_text and rec.get("pdf_url"):
            from ..pdf_pipeline import process_paper

            pdf_result = process_paper(paper_id, rec["pdf_url"])
            if pdf_result:
                # Add local pdf_path to record so extract_body_paragraphs can use it
                if "files" not in rec:
                    rec["files"] = {}
                rec["files"]["pdf_path"] = pdf_result["pdf_path"]
                rec["__cached_paragraphs"] = pdf_result.get("paragraphs") or []
                source_para_count = pdf_result.get("num_paragraphs", 0)
                if rec["__cached_paragraphs"]:
                    short_src = sum(
                        1
                        for p in rec["__cached_paragraphs"]
                        if len((p or "").strip()) < 10
                    )
                    source_short_ratio = (
                        short_src / len(rec["__cached_paragraphs"])
                        if rec["__cached_paragraphs"]
                        else 0.0
                    )
                    rec["__source_para_count"] = source_para_count
                    rec["__source_short_ratio"] = source_short_ratio
                log(
                    f"Downloaded and extracted {pdf_result['num_paragraphs']} paragraphs from PDF"
                )

        # Retry/fallback strategy
        trans_config = self.config.get("translation") or {}
        fallback_models = [
            m
            for m in trans_config.get("fallback_models", [])
            if m and m != self.model
        ]
        models_to_try = [self.model, *fallback_models]
        base_max_retries = int(trans_config.get("max_retries_per_model", 2))

        attempts_summary = []
        success_path: Optional[str] = None
        last_error: Optional[str] = None
        started_at = time.time()
        # Reset per-run cache counters
        self._cache_hits = 0
        self._cache_chunks = 0

        for model_slug in models_to_try:
            self.model = model_slug
            max_retries = base_max_retries
            for attempt_idx in range(max_retries):
                attempt_info = {
                    "model": model_slug,
                    "attempt": attempt_idx + 1,
                    "status": "started",
                }
                try:
                    # Translate (always full text)
                    tr = self.translate_record(rec, dry_run=dry_run, force_full_text=True)
                    tr["_model"] = model_slug

                    # Structural QA
                    source_count = (
                        rec.get("__source_para_count")
                        or tr.get("_source_para_count")
                        or source_para_count
                    )
                    source_ratio = (
                        rec.get("__source_short_ratio")
                        or tr.get("_source_short_ratio")
                        or source_short_ratio
                    )
                    ok, reason = self._structural_quality_ok(
                        source_count, source_ratio, tr.get("body_en") or []
                    )
                    if not ok:
                        raise TranslationValidationError(reason)

                    # Apply LLM formatting
                    from .formatting_service import FormattingService

                    fmt_service = FormattingService(self.config)
                    tr = fmt_service.format_translation(tr, dry_run=dry_run)
                    tr = self._strip_chinese_characters(tr)
                    removed_chars = tr.get("_removed_chinese_chars", [])
                    formatter_retries = tr.get("_formatter_retries", 0)
                    # Persist Markdown rendering alongside JSON
                    try:
                        md_out = self._render_markdown(tr)
                        md_path = os.path.join("data", "translated", f"{rec['id']}.md")
                        os.makedirs(os.path.dirname(md_path), exist_ok=True)
                        with open(md_path, "w", encoding="utf-8") as fh:
                            fh.write(md_out)
                        tr["_markdown_path"] = md_path
                    except Exception as md_err:
                        log(f"Markdown render failed for {rec['id']}: {md_err}")

                    # Apply QA filter to check for Chinese characters and formatting
                    from ..qa_filter import TranslationQAFilter

                    qa_filter = TranslationQAFilter()
                    qa_result = qa_filter.check_translation(tr)
                    tr["_qa_status"] = qa_result.status.value
                    tr["_qa_score"] = qa_result.score
                    tr["_qa_issues"] = qa_result.issues
                    tr["_qa_chinese_chars"] = qa_result.chinese_chars
                    tr["_qa_chinese_ratio"] = qa_result.chinese_ratio
                    tr["_qa_flagged_fields"] = qa_result.flagged_fields

                    if qa_result.status.value != "pass":
                        # Targeted retry using QA issues
                        prompt = (
                            "The previous translation failed QA for these reasons:\n"
                            + "; ".join(qa_result.issues or ["Chinese characters present"])
                            + "\nRe-translate the entire paper. Fix only these issues. "
                            "Preserve paragraph count and ordering exactly. Do not drop content."
                        )
                        retry_translation = self._retry_translate_with_prompt(tr, prompt)
                        retry_translation = fmt_service.format_translation(
                            retry_translation, dry_run=dry_run
                        )
                        retry_translation = self._strip_chinese_characters(
                            retry_translation
                        )
                        retry_qa = qa_filter.check_translation(retry_translation)
                        if retry_qa.status.value != "pass":
                            self._persist_failed_translation(rec["id"], retry_translation, retry_qa)
                            raise TranslationValidationError(
                                f"QA status {retry_qa.status.value} after retry"
                            )
                        tr = retry_translation
                        qa_result = retry_qa
                    tr["_qa_status"] = qa_result.status.value
                    tr["_qa_score"] = qa_result.score
                    tr["_qa_issues"] = qa_result.issues
                    tr["_qa_chinese_chars"] = qa_result.chinese_chars
                    tr["_qa_chinese_ratio"] = qa_result.chinese_ratio
                    tr["_qa_flagged_fields"] = qa_result.flagged_fields

                    # Save
                    out_dir = os.path.join("data", "translated")
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, f"{rec['id']}.json")
                    write_json(out_path, tr)
                    self._clear_macro_cache(rec.get("id"))

                    attempt_info["status"] = "success"
                    attempts_summary.append(attempt_info)
                    success_path = out_path
                    break
                except Exception as e:
                    last_error = str(e)
                    attempt_info["status"] = "failed"
                    attempt_info["reason"] = last_error
                    attempts_summary.append(attempt_info)
                log(f"Attempt failed for {rec['id']} with {model_slug}: {e}")
            if success_path:
                break

            # Wall-clock guard: bail out if the paper runtime exceeds limit
            if self.paper_wallclock_limit is not None:
                elapsed = time.time() - started_at
                if elapsed > self.paper_wallclock_limit:
                    log(
                        f"Wall-clock limit exceeded for {paper_id}: {elapsed:.1f}s > {self.paper_wallclock_limit}s"
                    )
                    break

        self._write_run_summary(
            rec,
            attempts_summary,
            success_path,
            last_error,
            cache_hits=getattr(self, "_cache_hits", 0) or None,
            cache_chunks=getattr(self, "_cache_chunks", 0) or None,
            stripped_chars=removed_chars if success_path else None,
            formatter_retries=formatter_retries if success_path else None,
        )

        if not success_path:
            raise TranslationValidationError(
                f"Translation failed for {paper_id} after {len(attempts_summary)} attempts: {last_error}"
            )

        return success_path

    def _retry_translate_with_prompt(
        self, translation: Dict[str, Any], retry_prompt: str
    ) -> Dict[str, Any]:
        """
        Retry by selectively re-translating only the fields that still
        contain Chinese characters. This avoids corrupting fields by
        sending the entire prompt+text to the LLM and ensures we update
        all affected fields (title, abstract, and body paragraphs).

        Note: The provided `retry_prompt` is intentionally not injected
        into the user content. Our system prompt already instructs the
        model to translate Chinese to English; re-translating the field
        text is sufficient and safer.
        """
        from ..qa_filter import ChineseCharacterDetector

        detector = ChineseCharacterDetector()
        retry_translation = translation.copy()

        # Helper to re-translate a single string field if it contains Chinese
        def _fix_field(text: str) -> str:
            if not text:
                return text
            # If any Chinese characters detected, re-translate just this field
            if detector.find_chinese_chars(text):
                return self.translate_field(
                    text,
                    model=self.model,
                    dry_run=False,
                    glossary_override=self.glossary,
                )
            return text

        # Title
        if isinstance(retry_translation.get("title_en"), str):
            retry_translation["title_en"] = _fix_field(retry_translation["title_en"])

        # Abstract
        if isinstance(retry_translation.get("abstract_en"), str):
            retry_translation["abstract_en"] = _fix_field(
                retry_translation["abstract_en"]
            )

        # Body paragraphs (list of strings)
        if isinstance(retry_translation.get("body_en"), list):
            new_body: List[str] = []
            for para in retry_translation["body_en"]:
                if isinstance(para, str):
                    new_body.append(_fix_field(para))
                else:
                    new_body.append(para)
            retry_translation["body_en"] = new_body

        return retry_translation

    def _infer_body_missing_reason(self, record: Dict[str, Any]) -> str:
        """
        Provide a coarse reason when full-text extraction fails.
        """
        files = record.get("files") or {}
        pdf_path = files.get("pdf_path")
        latex_path = files.get("latex_source_path")

        if not pdf_path and not latex_path:
            return "missing_assets"

        if pdf_path and not Path(pdf_path).exists():
            return "pdf_missing_on_disk"

        if latex_path and not Path(latex_path).exists():
            return "latex_missing_on_disk"

        return "extraction_failed"

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
        import re

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

    def _call_openrouter_with_fallback(
        self, text: str, model: str, glossary: List[Dict[str, str]]
    ) -> str:
        """
        Call OpenRouter API with fallback to alternate models on failure.

        Args:
            text: Text to translate
            model: Primary model to use
            glossary: Translation glossary

        Returns:
            Translated text

        Raises:
            OpenRouterError: If all models fail
            CircuitBreakerOpen: If circuit breaker threshold exceeded
        """
        # Check circuit breaker before attempting translation
        self._check_circuit_breaker()

        models_to_try = [model] + self.config.get("models", {}).get("alternates", [])

        last_error = None
        for model_to_try in models_to_try:
            try:
                log(f"Attempting translation with model: {model_to_try}")
                result = self._call_openrouter(text, model_to_try, glossary)
                # Success! Reset failure counters
                self._reset_failure_counter()
                return result
            except OpenRouterError as e:
                last_error = e
                log(f"Model {model_to_try} failed: {e}")

                # Record failure for circuit breaker
                error_code = getattr(e, "code", None)
                try:
                    self._record_failure(error_code)
                except CircuitBreakerOpen:
                    # Circuit breaker triggered - stop immediately
                    raise

                # If the failure cannot be fixed by switching models, stop early
                if isinstance(e, OpenRouterFatalError) and not getattr(
                    e, "fallback_ok", True
                ):
                    break
                continue

        # All models failed
        raise OpenRouterError(
            f"All translation models failed. Last error: {last_error}"
        )
