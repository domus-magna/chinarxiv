from src.qa_filter import SynthesisQAFilter


def test_synthesis_qa_flags_para_wrapped_authors() -> None:
    qa = SynthesisQAFilter()
    translation = {
        "title_en": "A normal title",
        "abstract_en": "This is a sufficiently long abstract for QA.",
        "creators_en": ['<PARA id="1">Miao, Longxin</PARA>', "Yang, Dr. Zhen"],
        "body_md": "Body.\n\n" + ("Sentence. " * 200),
    }
    res = qa.check_synthesis_translation(translation)
    assert res.status.value != "pass"
    assert "creators_en" in res.flagged_fields

