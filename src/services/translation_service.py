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
from ..body_extract import extract_body_paragraphs
from ..token_utils import chunk_paragraphs
from ..cost_tracker import compute_cost, append_cost_log
from ..logging_utils import log
from ..models import Paper, Translation


SYSTEM_PROMPT = (
    "You are a professional scientific translator specializing in academic papers. "
    "Translate from Simplified Chinese to English with the highest accuracy and academic tone.\n\n"
    "CRITICAL REQUIREMENTS:\n"
    "1. Preserve ALL LaTeX commands and ⟪MATH_*⟫ placeholders exactly - do not modify, translate, or rewrite any mathematical formulas\n"
    "2. Preserve ALL citation commands (\\cite{}, \\ref{}, \\eqref{}, etc.) exactly as they appear\n"
    "3. Maintain academic tone and formal scientific writing style\n"
    "4. Use precise technical terminology - obey the glossary strictly\n"
    "5. Preserve section structure and paragraph organization\n"
    "6. Translate all content completely - do not omit any information\n\n"
    "OUTPUT RULES:\n"
    "- Return ONLY the translated text for the given input (no explanations, no quotes, no headings you invent).\n"
    "- Keep one output paragraph per input paragraph; do not merge or split.\n"
    "- Do NOT add Markdown formatting unless it is present in the source.\n"
    "- Preserve original line breaks within the paragraph when meaningful; otherwise use standard English sentence spacing.\n\n"
    "FORMATTING GUIDELINES:\n"
    "- Keep mathematical expressions in their original LaTeX format\n"
    "- Preserve equation numbers and references\n"
    "- Maintain proper academic paragraph structure\n"
    "- Use formal scientific language appropriate for research papers\n\n"
    "Remember: Mathematical content and citations must remain untouched - only translate the Chinese text."
)


MAX_RESPONSE_PREVIEW = 2048


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


class TranslationService:
    """Service for handling translation operations."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize translation service.

        Args:
            config: Configuration dictionary (optional)
        """
        self.config = config or get_config()
        self.model = self.config.get("models", {}).get(
            "default_slug", "deepseek/deepseek-v3.2-exp"
        )
        self.glossary = self.config.get("glossary", [])
        self.failure_log_dir = Path("data/monitoring/openrouter_failures")

    @retry(
        wait=wait_exponential(min=1, max=20),
        stop=stop_after_attempt(5),
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
                "timeout": (15, 90) if source != "none" else (10, 60),
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
    ) -> str:
        """
        Translate a single field with math preservation.

        Args:
            text: Text to translate
            model: Model to use (defaults to service model)
            dry_run: If True, skip actual translation

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
            translated = self._call_openrouter_with_fallback(
                masked, model, glossary_eff
            )

        if not verify_token_parity(mappings, translated):
            raise MathPreservationError("Math placeholder parity check failed")

        unmasked = unmask_math(translated, mappings)

        # Additional validation checks
        self._validate_translation(text, unmasked)

        return unmasked

    def translate_paragraphs(
        self,
        paragraphs: List[str],
        model: Optional[str] = None,
        dry_run: bool = False,
        glossary_override: Optional[List[Dict[str, str]]] = None,
    ) -> List[str]:
        """
        Translate multiple paragraphs.

        Args:
            paragraphs: List of paragraphs to translate
            model: Model to use (defaults to service model)
            dry_run: If True, skip actual translation

        Returns:
            List of translated paragraphs
        """
        model = model or self.model
        glossary_eff = self.glossary if glossary_override is None else glossary_override
        # Optional batching to reduce API calls; disabled by default
        batch_enabled = (self.config.get("translation") or {}).get(
            "batch_paragraphs"
        ) is True
        if not batch_enabled:
            out: List[str] = []
            for p in paragraphs:
                out.append(
                    self.translate_field(
                        p, model, dry_run, glossary_override=glossary_eff
                    )
                )
            return out

        # Batch mode: chunk paragraphs into token-limited groups and join/split
        out: List[str] = []
        SENTINEL = "\n\n⟪PARA_BREAK⟫\n\n"
        for group in chunk_paragraphs(paragraphs):
            joined = SENTINEL.join(group)
            translated = self.translate_field(
                joined, model, dry_run, glossary_override=glossary_eff
            )
            parts = [s.strip() for s in translated.split(SENTINEL)]
            # Ensure we preserve count; if mismatch, fall back to per-paragraph
            if len(parts) != len(group):
                for p in group:
                    out.append(
                        self.translate_field(
                            p, model, dry_run, glossary_override=glossary_eff
                        )
                    )
            else:
                out.extend(parts)
        return out

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
                    except Exception as e:
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
                    except Exception as e:
                        # Fallback to original if translation fails
                        translation.subjects_en.append(subject)

        # Translate body if allowed
        if allow_full:
            paras = extract_body_paragraphs(record)
            if paras:
                translation.body_en = self.translate_paragraphs(
                    paras, dry_run=dry_run, glossary_override=glossary_override
                )

        # Cost tracking (approximate)
        from ..token_utils import estimate_tokens

        in_toks = estimate_tokens(title_src) + estimate_tokens(abstract_src)
        out_toks = estimate_tokens(translation.title_en or "") + estimate_tokens(
            translation.abstract_en or ""
        )

        if translation.body_en:
            in_toks += sum(estimate_tokens(p) for p in paras)
            out_toks += sum(estimate_tokens(p) for p in translation.body_en)

        cost = compute_cost(self.model, in_toks, out_toks, self.config)
        append_cost_log(paper.id, self.model, in_toks, out_toks, cost)

        return translation.to_dict()

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

        # Download PDF and extract text if requested
        if with_full_text and rec.get("pdf_url"):
            from ..pdf_pipeline import process_paper

            pdf_result = process_paper(paper_id, rec["pdf_url"])
            if pdf_result:
                # Add local pdf_path to record so extract_body_paragraphs can use it
                if "files" not in rec:
                    rec["files"] = {}
                rec["files"]["pdf_path"] = pdf_result["pdf_path"]
                log(
                    f"Downloaded and extracted {pdf_result['num_paragraphs']} paragraphs from PDF"
                )

        # Translate (always translate full text - we don't care about licenses)
        tr = self.translate_record(rec, dry_run=dry_run, force_full_text=True)

        # Apply LLM formatting (mandatory)
        from .formatting_service import FormattingService

        fmt_service = FormattingService(self.config)
        tr = fmt_service.format_translation(tr, dry_run=dry_run)

        # Apply QA filter to check for Chinese characters
        from ..qa_filter import TranslationQAFilter

        qa_filter = TranslationQAFilter()
        qa_result = qa_filter.check_translation(tr)

        # Simple retry for Chinese characters or Chinese punctuation issues
        # We retry when QA flagged explicit Chinese content (ideographs)
        # or when issues mention Chinese punctuation/metadata, to cover cases
        # where status may be FLAG_FORMATTING but Chinese is still present.
        issues_text = " ".join(qa_result.issues or [])
        should_retry = (
            qa_result.status.value == "flag_chinese"
            or (qa_result.status.value == "flag_formatting" and "Chinese" in issues_text)
        )
        if (
            not dry_run
            and self.config.get("translation", {}).get("retry_chinese_chars", True)
            and should_retry
            and not tr.get("_retry_attempted")
            and len(qa_result.chinese_chars) <= 5
        ):  # Only retry for reasonable number of chars

            try:
                retry_prompt = f"""
                This paper was translated from Chinese to English, but Chinese characters were detected: {qa_result.chinese_chars}
                
                Please re-translate the entire paper ensuring all Chinese characters are translated to English.
                Maintain exact formatting and structure. Return the corrected translation in the same format.
                """

                retry_translation = self._retry_translate_with_prompt(tr, retry_prompt)

                # Simple quality check: fewer Chinese characters
                retry_qa = qa_filter.check_translation(retry_translation)
                if len(retry_qa.chinese_chars) < len(qa_result.chinese_chars):
                    # Accept retry and re-apply formatting so _md fields match updated text
                    tr = fmt_service.format_translation(retry_translation, dry_run=dry_run)
                    qa_result = retry_qa
                    log(
                        f"Retry successful for {rec['id']}: {len(qa_result.chinese_chars)} Chinese chars remaining"
                    )
                else:
                    log(f"Retry failed for {rec['id']}: no improvement")

            except Exception as e:
                log(f"Retry failed for {rec['id']}: {e}")

            tr["_retry_attempted"] = True

        # Add QA metadata to translation
        tr["_qa_status"] = qa_result.status.value
        tr["_qa_score"] = qa_result.score
        tr["_qa_issues"] = qa_result.issues
        tr["_qa_chinese_chars"] = qa_result.chinese_chars
        tr["_qa_chinese_ratio"] = qa_result.chinese_ratio
        tr["_qa_flagged_fields"] = qa_result.flagged_fields

        # Log QA results
        if qa_result.status.value != "pass":
            log(
                f"QA Flagged {rec['id']}: {qa_result.status.value} (score: {qa_result.score:.2f})"
            )
            for issue in qa_result.issues:
                log(f"  - {issue}")

        # Save
        out_dir = os.path.join("data", "translated")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{rec['id']}.json")
        write_json(out_path, tr)

        return out_path

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
        """
        models_to_try = [model] + self.config.get("models", {}).get("alternates", [])

        last_error = None
        for model_to_try in models_to_try:
            try:
                log(f"Attempting translation with model: {model_to_try}")
                return self._call_openrouter(text, model_to_try, glossary)
            except OpenRouterError as e:
                last_error = e
                log(f"Model {model_to_try} failed: {e}")
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
