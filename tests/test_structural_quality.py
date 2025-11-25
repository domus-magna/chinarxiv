
from src.services.translation_service import TranslationService


def make_service(max_short_ratio: float = 0.1) -> TranslationService:
    cfg = {
        "models": {"default_slug": "dummy"},
        "translation": {"max_short_ratio": max_short_ratio},
    }
    return TranslationService(config=cfg)


def test_structural_quality_passes_on_match():
    svc = make_service()
    ok, reason = svc._structural_quality_ok(3, 0.0, ["para1", "para2", "para3"])
    assert ok
    assert reason == ""


def test_structural_quality_rejects_count_mismatch():
    svc = make_service()
    ok, reason = svc._structural_quality_ok(3, 0.0, ["only one"])
    assert not ok
    assert "mismatch" in reason


def test_structural_quality_rejects_excessive_shorts():
    svc = make_service(max_short_ratio=0.2)
    translated = ["a", "b", "tiny", "short", "ok text"] * 5  # 25 paras, mostly short
    ok, reason = svc._structural_quality_ok(25, 0.0, translated)
    assert not ok
    assert "short fragments" in reason


def test_structural_quality_skips_when_no_source():
    svc = make_service()
    ok, reason = svc._structural_quality_ok(0, 0.0, ["para"])
    assert ok
    assert reason == ""
