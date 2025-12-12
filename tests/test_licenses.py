from src.licenses import parse_license_string, decide_derivatives_allowed
from src.utils import load_yaml


def test_parse_license_mapping():
    assert parse_license_string("CC-BY 4.0") == "CC BY"
    assert parse_license_string("Creative Commons Attribution-ShareAlike") == "CC BY-SA"
    assert (
        parse_license_string("https://creativecommons.org/licenses/by-nd/4.0/")
        == "CC BY-ND"
    )
    assert parse_license_string("") is None


def test_decide_derivatives_from_config(tmp_path):
    cfg = load_yaml("src/config.yaml")
    rec = {"id": "x", "license": {"raw": "CC BY"}}
    out = decide_derivatives_allowed(rec, cfg)
    assert out["license"]["derivatives_allowed"] is True
    rec2 = {"id": "y", "license": {"raw": "CC-BY-ND"}}
    out2 = decide_derivatives_allowed(rec2, cfg)
    assert out2["license"]["derivatives_allowed"] is False
