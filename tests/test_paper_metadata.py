from pathlib import Path

from src.paper_metadata import PaperMetadata, parse_metadata_from_html


def test_parse_metadata_from_html_round_trip():
    html = Path("tests/fixtures/harvest/paper_metadata_sample.html").read_text(
        encoding="utf-8"
    )

    meta = parse_metadata_from_html(html, "202411.01234")

    assert isinstance(meta, PaperMetadata)
    assert meta.paper_id == "202411.01234"
    assert meta.title == "Sample Paper Title"
    assert meta.abstract.startswith("Here is the abstract")
    assert meta.creators == ["Alice", "Bob"]
    assert meta.subjects == ["Physics"]
    assert meta.pdf_url.endswith("filetype=pdf&id=202411.01234")

    record = meta.to_record()
    assert record["id"] == "chinaxiv-202411.01234"
    assert record["source_url"].endswith("/abs/202411.01234")
    assert record["date"] == "2024-12-31T12:00:00Z"


def test_parse_metadata_from_real_html():
    """Test parsing against realistic ChinaXiv HTML structure."""
    html = Path("tests/fixtures/harvest/paper_metadata_real.html").read_text(
        encoding="utf-8"
    )

    meta = parse_metadata_from_html(html, "202411.00001")

    assert isinstance(meta, PaperMetadata)
    assert meta.paper_id == "202411.00001"
    assert "钻石开放获取" in meta.title  # Chinese title
    assert "钻石开放获取" in meta.abstract  # Chinese abstract
    assert meta.creators == ["顾立平", "张三"]  # Authors from field=author links
    # Subjects from domain/subject links
    assert "图书馆学、情报学" in meta.subjects
    assert "图书馆学" in meta.subjects
    # PDF URL with UUID pattern
    assert "filetype=pdf" in meta.pdf_url
    assert "uuid=" in meta.pdf_url
    assert meta.pdf_url.startswith("https://chinaxiv.org")

    record = meta.to_record()
    assert record["id"] == "chinaxiv-202411.00001"
    assert record["date"] == "2024-10-26T22:53:17Z"

