from src.qa_filter import SynthesisQAFilter


def test_synthesis_qa_flags_para_wrapped_title() -> None:
    qa = SynthesisQAFilter()
    translation = {
        "title_en": '<PARA id="1">Not a real title</PARA>',
        "abstract_en": "This is a sufficiently long abstract for QA.",
        "body_md": "This is a sufficiently long body.\n\n" + ("Sentence. " * 200),
    }
    res = qa.check_synthesis_translation(translation)
    assert res.status.value != "pass"
    assert "title_en" in res.flagged_fields


def test_synthesis_qa_flags_body_sized_title() -> None:
    qa = SynthesisQAFilter()
    translation = {
        "title_en": "X" * 5000,
        "abstract_en": "This is a sufficiently long abstract for QA.",
        "body_md": "This is a sufficiently long body.\n\n" + ("Sentence. " * 200),
    }
    res = qa.check_synthesis_translation(translation)
    assert res.status.value != "pass"
    assert "title_en" in res.flagged_fields

