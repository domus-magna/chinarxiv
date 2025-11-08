import json

from src.render import render_site, load_translated


def test_render_smoke(tmp_path, monkeypatch):
    items = [
        {
            "id": "2025-12345",
            "title_en": "Example Title",
            "abstract_en": "An abstract.",
            "creators": ["Li, Hua"],
            "subjects": ["cs.AI"],
            "date": "2025-10-02",
            "license": {"badge": "CC BY"},
            "source_url": "https://example.org/abs/2025-12345",
            "pdf_url": "https://example.org/pdf/2025-12345.pdf",
        }
    ]
    # Render into tmp site folder by monkeypatching output location
    monkeypatch.chdir(tmp_path)
    # need templates and assets from project root; create minimal mirrors
    from pathlib import Path
    import shutil

    # Resolve project root by test file position
    root = Path(__file__).resolve().parents[1]
    # Copy templates and assets from repo
    shutil.copytree(str(root / "src" / "templates"), "src/templates")
    shutil.copytree(str(root / "assets"), "assets")
    render_site(items)
    assert (tmp_path / "site" / "index.html").exists()
    assert (tmp_path / "site" / "items" / "2025-12345" / "index.html").exists()


def test_load_translated_skips_missing_body(tmp_path, monkeypatch):
    """Only translations with a full body should be returned for rendering."""
    monkeypatch.chdir(tmp_path)
    translated_dir = tmp_path / "data" / "translated"
    translated_dir.mkdir(parents=True, exist_ok=True)

    good = {
        "id": "with-body",
        "title_en": "Title",
        "_qa_status": "pass",
        "body_en": ["Paragraph"],
        "_has_full_body": True,
    }
    missing = {
        "id": "no-body",
        "title_en": "Title",
        "_qa_status": "pass",
        "body_en": [],
        "_has_full_body": False,
        "_full_body_reason": "missing_assets",
    }
    with open(translated_dir / "good.json", "w", encoding="utf-8") as f:
        json.dump(good, f)
    with open(translated_dir / "missing.json", "w", encoding="utf-8") as f:
        json.dump(missing, f)

    items = load_translated()
    assert [it["id"] for it in items] == ["with-body"]
    report_path = tmp_path / "reports" / "missing_full_body.json"
    assert report_path.exists()
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
    assert report[0]["id"] == "no-body"
