from unittest.mock import patch

from src.services.translation_service import TranslationService


def test_translate_record_synthesis_clamps_or_falls_back_for_bad_title() -> None:
    """
    Regression: A model can occasionally return a whole-paper blob for title_en.

    We should not persist that into JSON/DB because it breaks UI and search.
    """
    service = TranslationService()

    record = {
        "id": "chinaxiv-202507.00007",
        "title": "DAYU3D: A modern code for HTGR thermal-hydraulic design and accident analysis",
        "abstract": "摘要",
        "creators": [],
        "subjects": [],
        "pdf_url": None,
        "source_url": None,
        "files": {},
    }

    bad_title = '<PARA id="1">' + ("This is clearly not a real title. " * 200) + "</PARA>"

    with patch.object(
        TranslationService,
        "translate_field",
        side_effect=[bad_title, "Abstract ok"],
    ):
        result = service.translate_record_synthesis(record, dry_run=False)

    assert result["title_en"] == record["title"]
    assert len(result["title_en"]) <= 300
