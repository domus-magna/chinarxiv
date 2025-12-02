"""
Complete paper processor: fetch fresh metadata, download PDF, translate text,
translate figures, and optionally upload artifacts to Backblaze B2.

Designed as a thin orchestrator that reuses existing pipeline components and
avoids dependence on pre-harvested records. Per-paper runs are idempotent and
safe to re-run if interrupted.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .file_service import write_json
from .paper_metadata import PaperMetadata, fetch_metadata_for_id
from .pdf_pipeline import download_pdf
from .qa_filter import QAResult, QAStatus, SynthesisQAFilter
from .services.translation_service import TranslationService
from .utils import log


try:
    from .figure_pipeline import FigurePipeline  # type: ignore
    from .figure_pipeline.models import FigureProcessingResult, PipelineConfig  # type: ignore
except Exception:  # pragma: no cover - figure deps may not be installed in tests
    FigurePipeline = None
    FigureProcessingResult = None
    PipelineConfig = None


@dataclass
class PaperProcessResult:
    """Result of processing a single paper."""

    paper_id: str
    success: bool
    metadata: Optional[PaperMetadata] = None
    pdf_path: Optional[Path] = None
    translation_path: Optional[Path] = None
    figure_result: Optional[FigureProcessingResult] = None
    uploaded: bool = False
    qa_passed: Optional[bool] = None
    errors: List[str] = field(default_factory=list)


def _normalize_paper_id(paper_id: str) -> tuple[str, str]:
    """Return (full_id_with_prefix, raw_id)."""

    if paper_id.startswith("chinaxiv-"):
        return paper_id, paper_id.replace("chinaxiv-", "", 1)
    return f"chinaxiv-{paper_id}", paper_id


def _download_pdf_for_metadata(
    metadata: PaperMetadata,
    full_id: str,
    pdf_dir: Path,
) -> Path:
    """Download PDF for the paper and return the local path.

    Uses the default download_pdf() function. For testing, inject a custom
    pdf_downloader into process_paper_complete() instead.
    """
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{full_id}.pdf"

    ok = download_pdf(
        metadata.pdf_url,
        str(pdf_path),
        referer=metadata.referer,
        session_id=metadata.paper_id,
    )
    if not ok:
        raise RuntimeError(f"PDF download failed for {full_id}")
    return pdf_path


def _translate_text(
    record: dict,
    *,
    output_dir: Path,
    dry_run: bool = False,
    service_factory: Callable[[], TranslationService] = TranslationService,
) -> tuple[Path, QAResult]:
    """
    Translate the paper using synthesis mode and run QA.

    Returns (path to translated JSON, QA result).
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    service = service_factory()
    translation = service.translate_record_synthesis(record, dry_run=dry_run)

    qa_filter = SynthesisQAFilter()
    qa_result = qa_filter.check_synthesis_translation(
        translation, translation.get("_extraction_stats")
    )

    translation["_qa_status"] = qa_result.status.value
    translation["_qa_score"] = qa_result.score
    translation["_qa_issues"] = qa_result.issues
    translation["_qa_chinese_ratio"] = qa_result.chinese_ratio

    out_path = output_dir / f"{record['id']}.json"
    write_json(out_path, translation)
    return out_path, qa_result


def _process_figures(
    paper_id: str,
    pdf_dir: Path,
    *,
    figure_pipeline_factory: Optional[Callable[[], FigurePipeline]] = None,
) -> Optional[FigureProcessingResult]:
    """Run the figure pipeline if dependencies are available."""

    if FigurePipeline is None or PipelineConfig is None:
        log("Figure pipeline not available (dependencies missing); skipping figures")
        return None

    pipeline_factory = figure_pipeline_factory or (
        lambda: FigurePipeline(
            PipelineConfig(
                pdf_dir=str(pdf_dir),
                output_dir=str(pdf_dir.parent / "figures"),
            )
        )
    )

    pipeline = pipeline_factory()
    return pipeline.process_paper(paper_id)


def _get_b2_prefix() -> str:
    """Get normalized B2 prefix (no leading/trailing slashes)."""
    prefix = os.getenv("BACKBLAZE_PREFIX", "") or os.getenv("B2_PREFIX", "")
    return prefix.strip("/")


def _build_b2_key(path: str) -> str:
    """Build full B2 key with prefix."""
    prefix = _get_b2_prefix()
    if prefix:
        return f"{prefix}/{path}"
    return path


def _upload_to_b2(
    paper_id: str,
    translation_path: Optional[Path],
    pdf_path: Optional[Path],
    qa_passed: bool = True,
) -> bool:
    """Upload translation and PDF to B2 via b2sdk.

    Args:
        qa_passed: If True, upload to validated/. If False, upload to flagged/.
    """
    try:
        import b2sdk.v2 as b2
    except ImportError:
        log("b2sdk not installed; skipping B2 upload")
        return False

    if not translation_path or not translation_path.exists():
        log(f"No translation to upload for {paper_id}; skipping B2 upload")
        return False

    key_id = (
        os.getenv("B2_KEY_ID")
        or os.getenv("BACKBLAZE_KEY_ID")
    )
    app_key = (
        os.getenv("B2_APP_KEY")
        or os.getenv("BACKBLAZE_APPLICATION_KEY")
    )
    bucket_name = os.getenv("B2_BUCKET") or os.getenv("BACKBLAZE_BUCKET")

    if not all([key_id, app_key, bucket_name]):
        log(f"Skipping B2 upload for {paper_id}: missing B2 credentials or bucket")
        return False

    try:
        info = b2.InMemoryAccountInfo()
        client = b2.B2Api(info)
        client.authorize_account("production", key_id, app_key)
        bucket = client.get_bucket_by_name(bucket_name)
    except Exception as e:
        log(f"B2 authorization failed: {e}")
        return False

    ok = True

    # Upload translation to validated/ or flagged/ based on QA status
    translation_dir = "validated/translations" if qa_passed else "flagged/translations"
    try:
        bucket.upload_local_file(
            local_file=str(translation_path),
            file_name=_build_b2_key(f"{translation_dir}/{translation_path.name}"),
        )
        log(f"Uploaded translation to {translation_dir}: {translation_path.name}")
    except Exception as e:
        log(f"Upload failed for {paper_id} translation: {e}")
        ok = False

    # Upload PDF if present (always to pdfs/)
    if pdf_path and pdf_path.exists():
        try:
            bucket.upload_local_file(
                local_file=str(pdf_path),
                file_name=_build_b2_key(f"pdfs/{pdf_path.name}"),
            )
            log(f"Uploaded PDF: {pdf_path.name}")
        except Exception as e:
            log(f"Upload failed for {paper_id} PDF: {e}")
            ok = False

    return ok


def _check_exists_in_b2(paper_id: str) -> bool:
    """Check if translation already exists in B2 (in validated/ directory)."""
    try:
        import b2sdk.v2 as b2
    except ImportError:
        return False  # Can't check, assume doesn't exist

    key_id = os.getenv("B2_KEY_ID") or os.getenv("BACKBLAZE_KEY_ID")
    app_key = os.getenv("B2_APP_KEY") or os.getenv("BACKBLAZE_APPLICATION_KEY")
    bucket_name = os.getenv("B2_BUCKET") or os.getenv("BACKBLAZE_BUCKET")

    if not all([key_id, app_key, bucket_name]):
        return False  # Can't check without credentials

    try:
        info = b2.InMemoryAccountInfo()
        client = b2.B2Api(info)
        client.authorize_account("production", key_id, app_key)
        bucket = client.get_bucket_by_name(bucket_name)
        # Use prefix-aware key
        bucket.get_file_info_by_name(
            _build_b2_key(f"validated/translations/{paper_id}.json")
        )
        return True
    except Exception:
        return False


def process_paper_complete(
    paper_id: str,
    *,
    with_text: bool = True,
    with_figures: bool = True,
    upload: bool = True,
    force: bool = False,
    workdir: Path | str = Path("."),
    continue_on_error: bool = False,
    dry_run: bool = False,
    metadata_fetcher: Callable[[str], PaperMetadata] = fetch_metadata_for_id,
    pdf_downloader: Callable[[PaperMetadata, str, Path], Path] | None = None,
    text_translator: Callable[[dict, Path, bool], tuple[Path, QAResult]] | None = None,
    figure_runner: Optional[Callable[[str, Path], Optional[FigureProcessingResult]]] = None,
    uploader: Callable[[str, Optional[Path], Optional[Path], bool], bool] = _upload_to_b2,
) -> PaperProcessResult:
    """
    Process a paper end-to-end. Raises unless continue_on_error is True.

    Args:
        force: If False, skip papers that already exist in B2 (default).
               If True, re-process even if translation exists.
    """

    full_id, raw_id = _normalize_paper_id(paper_id)

    # Idempotency check: skip if already in B2 (unless --force)
    if not force and _check_exists_in_b2(full_id):
        log(f"Skipping {full_id}: already exists in B2 (use --force to override)")
        return PaperProcessResult(
            paper_id=full_id,
            success=True,
            errors=["skipped: already exists in B2"],
        )

    base_dir = Path(workdir)
    pdf_dir = base_dir / "data" / "pdfs"
    translated_dir = base_dir / "data" / "translated"

    errors: List[str] = []
    metadata: Optional[PaperMetadata] = None
    pdf_path: Optional[Path] = None
    translation_path: Optional[Path] = None
    figure_result: Optional[FigureProcessingResult] = None
    uploaded = False
    qa_passed: Optional[bool] = None

    try:
        metadata = metadata_fetcher(raw_id)
        log(f"Fetched fresh metadata for {full_id}: {metadata.title[:80]}")

        downloader_fn = pdf_downloader or _download_pdf_for_metadata
        pdf_path = downloader_fn(metadata, full_id, pdf_dir)
        log(f"PDF ready for {full_id}: {pdf_path}")

        record = metadata.to_record()
        record["files"] = {"pdf_path": str(pdf_path)}

        if with_text:
            translator_fn: Callable[[dict, Path, bool], tuple[Path, QAResult]]
            if text_translator:
                translator_fn = text_translator
            else:
                translator_fn = lambda rec, out_dir, is_dry: _translate_text(
                    rec, output_dir=out_dir, dry_run=is_dry
                )
            translation_path, qa_result = translator_fn(record, translated_dir, dry_run)
            qa_passed = qa_result.status == QAStatus.PASS
            log(
                f"Translation complete for {full_id}: {translation_path} "
                f"(QA: {qa_result.status.value}, score: {qa_result.score:.2f})"
            )

        if with_figures:
            runner = figure_runner or (lambda pid, pdir: _process_figures(pid, pdir))
            figure_result = runner(full_id, pdf_dir)

        if upload:
            # Pass qa_passed to route to validated/ or flagged/
            uploaded = uploader(full_id, translation_path, pdf_path, qa_passed if qa_passed is not None else True)
            if not uploaded:
                errors.append("B2 upload failed")

        return PaperProcessResult(
            paper_id=full_id,
            success=len(errors) == 0,
            metadata=metadata,
            pdf_path=pdf_path,
            translation_path=translation_path,
            figure_result=figure_result,
            uploaded=uploaded,
            qa_passed=qa_passed,
            errors=errors,
        )

    except Exception as exc:  # pragma: no cover - exercised in integration usage
        errors.append(str(exc))
        log(f"Error processing {full_id}: {exc}")
        if not continue_on_error:
            raise
        return PaperProcessResult(
            paper_id=full_id,
            success=False,
            metadata=metadata,
            pdf_path=pdf_path,
            translation_path=translation_path,
            figure_result=figure_result,
            uploaded=uploaded,
            qa_passed=qa_passed,
            errors=errors,
        )


def _load_ids_from_file(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if clean:
            ids.append(clean)
    return ids


def run_cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Process papers end-to-end")
    parser.add_argument("--paper-id", help="Single paper ID")
    parser.add_argument("--paper-ids-file", help="File with paper IDs (one per line)")
    parser.add_argument("--limit", type=int, default=0, help="Max papers to process")
    parser.add_argument("--no-text", action="store_true", help="Skip text translation")
    parser.add_argument("--no-figures", action="store_true", help="Skip figure pipeline")
    parser.add_argument("--no-upload", action="store_true", help="Skip B2 upload")
    parser.add_argument("--force", action="store_true", help="Re-process even if already in B2")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--workdir", default=".", help="Base working directory")
    parser.add_argument("--dry-run", action="store_true", help="Skip API calls where possible")

    args = parser.parse_args(argv)

    ids: list[str] = []
    if args.paper_id:
        ids.append(args.paper_id)
    if args.paper_ids_file:
        ids.extend(_load_ids_from_file(Path(args.paper_ids_file)))

    if not ids:
        parser.error("Provide --paper-id or --paper-ids-file")

    if args.limit > 0:
        ids = ids[: args.limit]

    successes = 0
    failures = 0

    for idx, pid in enumerate(ids, start=1):
        log(f"[{idx}/{len(ids)}] Processing {pid}…")
        try:
            result = process_paper_complete(
                pid,
                with_text=not args.no_text,
                with_figures=not args.no_figures,
                upload=not args.no_upload,
                force=args.force,
                workdir=Path(args.workdir),
                continue_on_error=args.continue_on_error,
                dry_run=args.dry_run,
            )
            if result.success:
                successes += 1
            else:
                failures += 1
        except Exception as exc:
            failures += 1
            log(f"Failed {pid}: {exc}")
            if not args.continue_on_error:
                break

    log(f"Complete: {successes} succeeded, {failures} failed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_cli())
