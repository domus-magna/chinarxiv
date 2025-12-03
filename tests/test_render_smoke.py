import json
from datetime import datetime, timezone

from src.render import render_site, load_translated, parse_date_for_sort


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


class TestParseDateForSort:
    """Tests for parse_date_for_sort function."""

    def test_basic_ordering(self):
        """Papers should sort newest first."""
        cases = [
            ("2024-12-01T10:00:00Z", "paper-a"),
            ("2024-11-15T08:30:00Z", "paper-b"),
            ("2024-11-15", "paper-c"),
            ("", "paper-d"),
        ]
        sorted_cases = sorted(
            cases, key=lambda x: parse_date_for_sort(x[0], x[1]), reverse=True
        )
        assert [c[1] for c in sorted_cases] == ["paper-a", "paper-b", "paper-c", "paper-d"]

    def test_same_date_tiebreaker(self):
        """Same timestamp should use ID as tiebreaker (alphabetical)."""
        cases = [
            ("2024-11-15T08:30:00Z", "paper-b"),
            ("2024-11-15T08:30:00Z", "paper-a"),
        ]
        sorted_cases = sorted(
            cases, key=lambda x: parse_date_for_sort(x[0], x[1]), reverse=True
        )
        # Both have same datetime, so secondary key (id) determines order
        # reverse=True means "paper-b" > "paper-a" alphabetically reversed
        assert [c[1] for c in sorted_cases] == ["paper-b", "paper-a"]

    def test_timezone_handling(self):
        """Timezone offsets should be correctly converted for comparison."""
        cases = [
            ("2024-11-15T08:30:00+08:00", "tz-east"),  # UTC: 00:30
            ("2024-11-15T08:30:00Z", "tz-utc"),        # UTC: 08:30
        ]
        sorted_cases = sorted(
            cases, key=lambda x: parse_date_for_sort(x[0], x[1]), reverse=True
        )
        # tz-utc is 08:30 UTC, tz-east is 00:30 UTC, so tz-utc comes first
        assert [c[1] for c in sorted_cases] == ["tz-utc", "tz-east"]

    def test_non_string_date_input(self):
        """Non-string date should not crash, treated as missing."""
        # Should not raise, should return datetime.min
        result = parse_date_for_sort(None, "paper-1")
        assert result[0] == datetime.min.replace(tzinfo=timezone.utc)
        assert result[1] == "paper-1"

        result = parse_date_for_sort(12345, "paper-2")
        assert result[0] == datetime.min.replace(tzinfo=timezone.utc)

        result = parse_date_for_sort({"date": "2024-01-01"}, "paper-3")
        assert result[0] == datetime.min.replace(tzinfo=timezone.utc)

    def test_none_fallback_id(self):
        """None fallback_id should be converted to empty string for total ordering."""
        result = parse_date_for_sort("2024-11-15T08:30:00Z", None)
        assert result[1] == ""  # None becomes empty string for safe comparison

    def test_date_only_format(self):
        """Date-only strings should parse correctly."""
        result = parse_date_for_sort("2024-11-15", "paper-1")
        assert result[0].year == 2024
        assert result[0].month == 11
        assert result[0].day == 15

    def test_invalid_date_string(self):
        """Invalid date strings should return datetime.min."""
        result = parse_date_for_sort("not-a-date", "paper-1")
        assert result[0] == datetime.min.replace(tzinfo=timezone.utc)


def test_load_translated_sort_date_carryforward(tmp_path, monkeypatch):
    """Papers with invalid/missing dates get _sort_date carried forward, but original date preserved."""
    monkeypatch.chdir(tmp_path)
    translated_dir = tmp_path / "data" / "translated"
    translated_dir.mkdir(parents=True, exist_ok=True)

    # Create papers in ID order - paper-2 has invalid date, paper-3 has missing date
    paper1 = {
        "id": "chinaxiv-202401.00001",
        "title_en": "Paper 1",
        "_qa_status": "pass",
        "body_en": ["Content"],
        "_has_full_body": True,
        "date": "2024-01-15T10:00:00Z",  # Valid date
    }
    paper2 = {
        "id": "chinaxiv-202401.00002",
        "title_en": "Paper 2",
        "_qa_status": "pass",
        "body_en": ["Content"],
        "_has_full_body": True,
        "date": "not-a-valid-date",  # Invalid date
    }
    paper3 = {
        "id": "chinaxiv-202401.00003",
        "title_en": "Paper 3",
        "_qa_status": "pass",
        "body_en": ["Content"],
        "_has_full_body": True,
        # Missing date
    }

    with open(translated_dir / "paper1.json", "w", encoding="utf-8") as f:
        json.dump(paper1, f)
    with open(translated_dir / "paper2.json", "w", encoding="utf-8") as f:
        json.dump(paper2, f)
    with open(translated_dir / "paper3.json", "w", encoding="utf-8") as f:
        json.dump(paper3, f)

    items = load_translated()

    # Original dates should be PRESERVED (not mutated)
    dates = {it["id"]: it.get("date") for it in items}
    assert dates["chinaxiv-202401.00001"] == "2024-01-15T10:00:00Z"
    assert dates["chinaxiv-202401.00002"] == "not-a-valid-date"  # Original preserved
    assert dates["chinaxiv-202401.00003"] is None  # Original preserved (missing)

    # _sort_date should have carry-forward applied
    sort_dates = {it["id"]: it.get("_sort_date") for it in items}
    assert sort_dates["chinaxiv-202401.00001"] == "2024-01-15T10:00:00Z"
    assert sort_dates["chinaxiv-202401.00002"] == "2024-01-15T10:00:00Z"  # Carried forward
    assert sort_dates["chinaxiv-202401.00003"] == "2024-01-15T10:00:00Z"  # Carried forward
