from src.services.formatting_service import FormattingService


def test_parse_formatter_json_allows_prefix_suffix():
    svc = FormattingService(config={"formatting": {"model": "stub-model", "temperature": 0}})
    payload = "Here you go:\n{\"abstract_md\": \"A\", \"body_md\": \"B\"}\nThank you."
    parsed = svc._parse_formatter_json(payload)
    assert parsed == {"abstract_md": "A", "body_md": "B"}


def test_parse_formatter_json_raises_for_non_object():
    svc = FormattingService(config={"formatting": {"model": "stub-model", "temperature": 0}})
    bad_payload = "[1, 2, 3]"
    try:
        svc._parse_formatter_json(bad_payload)
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-object payload")
