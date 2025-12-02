from pathlib import Path

from src.complete_paper_processor import process_paper_complete
from src.paper_metadata import PaperMetadata


def test_process_paper_complete_uses_injected_components(tmp_path):
    calls = []

    def fake_fetch(pid: str) -> PaperMetadata:
        calls.append(("fetch", pid))
        return PaperMetadata(
            paper_id=pid,
            title="Injected Title",
            abstract="Injected Abstract",
            creators=["Author"],
            subjects=["Math"],
            date_iso="2025-01-01T00:00:00Z",
            pdf_url="https://example.com/paper.pdf",
            source_url=f"https://example.com/abs/{pid}",
        )

    def fake_download(meta: PaperMetadata, full_id: str, pdf_dir: Path) -> Path:
        calls.append(("download", meta.paper_id, full_id))
        pdf_path = pdf_dir / f"{full_id}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(b"%PDF-1.4 test")
        return pdf_path

    def fake_translate(record: dict, out_dir: Path, dry_run: bool) -> Path:
        calls.append(("translate", record["id"], dry_run))
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{record['id']}.json"
        out_path.write_text("{}", encoding="utf-8")
        return out_path

    def fake_figures(pid: str, pdf_dir: Path):
        calls.append(("figures", pid, pdf_dir))
        return "figures-ok"

    uploaded = {}

    def fake_upload(pid: str, translation_path: Path | None, pdf_path: Path | None) -> bool:
        calls.append(("upload", pid, translation_path, pdf_path))
        uploaded[pid] = True
        return True

    result = process_paper_complete(
        "chinaxiv-202411.00001",
        with_text=True,
        with_figures=True,
        upload=True,
        force=True,  # Bypass idempotency check for test
        workdir=tmp_path,
        metadata_fetcher=fake_fetch,
        pdf_downloader=fake_download,
        text_translator=fake_translate,
        figure_runner=fake_figures,
        uploader=fake_upload,
    )

    assert result.success
    assert result.pdf_path == tmp_path / "data" / "pdfs" / "chinaxiv-202411.00001.pdf"
    assert result.translation_path == tmp_path / "data" / "translated" / "chinaxiv-202411.00001.json"
    assert uploaded.get("chinaxiv-202411.00001") is True
    # Order matters for fetch -> download -> translate
    assert calls[0][0] == "fetch"
    assert any(call[0] == "figures" for call in calls)
    assert any(call[0] == "upload" for call in calls)


def test_pdf_path_matches_figure_pipeline_expectations():
    """Verify PDF naming matches what figure pipeline expects.

    The complete processor saves PDFs as {full_id}.pdf (e.g., chinaxiv-202411.00001.pdf).
    The figure pipeline's _find_pdf() searches for the same pattern.
    """
    from src.complete_paper_processor import _normalize_paper_id

    # Test normalization produces consistent IDs
    full_id_from_short, _ = _normalize_paper_id("202411.00001")
    full_id_from_long, _ = _normalize_paper_id("chinaxiv-202411.00001")

    assert full_id_from_short == "chinaxiv-202411.00001"
    assert full_id_from_long == "chinaxiv-202411.00001"

    # Both should produce the same PDF path
    pdf_path_short = Path("data/pdfs") / f"{full_id_from_short}.pdf"
    pdf_path_long = Path("data/pdfs") / f"{full_id_from_long}.pdf"

    assert pdf_path_short == pdf_path_long
    assert pdf_path_short.name == "chinaxiv-202411.00001.pdf"

