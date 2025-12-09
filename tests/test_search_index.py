from src.search_index import build_index


def test_build_index_fields():
    items = [
        {
            "id": "1",
            "title_en": "Title",
            "creators": ["A"],
            "abstract_en": "abc",
            "subjects": ["s"],
            "date": "2025-01-01",
            "_has_full_body": True,
        },
        {
            "id": "2",
            "title_en": "Missing",
            "creators": ["B"],
            "abstract_en": "def",
            "subjects": ["s"],
            "date": "2025-01-02",
            "_has_full_body": False,
        },
        {
            "id": "3",
            "title_en": "Legacy",
            "creators": ["C"],
            "abstract_en": "ghi",
            "subjects": ["s"],
            "date": "2025-01-03",
            # No explicit flag, but body_en contents prove full text
            "body_en": ["Para"],
        },
    ]
    idx = build_index(items)
    assert len(idx) == 2
    assert idx[0]["id"] == "1"
    assert idx[1]["id"] == "3"
    assert set(idx[0].keys()) == {
        "id",
        "title",
        "authors",
        "abstract",
        "subjects",
        "date",
        "has_figures",
        "pdf_url",
    }
