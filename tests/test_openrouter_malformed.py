"""Tests for handling malformed OpenRouter responses."""

from __future__ import annotations

import json
from typing import List

import pytest

import src.services.translation_service as translation_service


class _FakeResponse:
    """Simple response stub used to simulate OpenRouter payloads."""

    def __init__(self, *, text: str, payload: dict | None = None) -> None:
        self._text = text
        self._payload = payload
        self.ok = True
        self.status_code = 200
        self.headers = {"Content-Type": "application/json"}

    @property
    def text(self) -> str:  # pragma: no cover - trivial property
        return self._text

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("Invalid JSON")
        return self._payload


@pytest.fixture(autouse=True)
def _patch_external_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch network helpers so tests do not rely on real credentials."""

    monkeypatch.setattr(
        translation_service, "openrouter_headers", lambda: {"Authorization": "test"}
    )
    monkeypatch.setattr(
        translation_service, "get_proxies", lambda: ({}, "none")
    )


@pytest.fixture
def _collector(monkeypatch: pytest.MonkeyPatch) -> List[dict]:
    """Collect monitoring error calls for assertions."""

    calls: List[dict] = []

    def _record_error(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(translation_service.monitoring_service, "record_error", _record_error)
    return calls


def _stub_post(response: _FakeResponse):
    """Return a callable suitable for patching requests.post."""

    def _post(*_args, **_kwargs):  # pragma: no cover - trivial wrapper
        return response

    return _post


def test_invalid_json_response_logs_and_raises(tmp_path, monkeypatch, _collector):
    service = translation_service.TranslationService()
    service.failure_log_dir = tmp_path / "failures"

    monkeypatch.setattr(translation_service.requests, "post", _stub_post(_FakeResponse(text="{not valid")))

    call = translation_service.TranslationService._call_openrouter.__wrapped__

    with pytest.raises(translation_service.OpenRouterRetryableError) as exc:
        call(service, "text", service.model, [])

    assert "Malformed response" in str(exc.value)
    assert any(call.get("code") == "invalid_json" for call in _collector)

    artifacts = list(service.failure_log_dir.glob("*.json"))
    assert len(artifacts) == 1
    data = json.loads(artifacts[0].read_text())
    assert data["reason"] == "invalid_json"
    assert "body_preview" in data


def test_missing_content_response_is_retryable(tmp_path, monkeypatch, _collector):
    service = translation_service.TranslationService()
    service.failure_log_dir = tmp_path / "failures"

    payload = {"choices": [{"message": {}}]}
    monkeypatch.setattr(
        translation_service.requests,
        "post",
        _stub_post(_FakeResponse(text=json.dumps(payload), payload=payload)),
    )

    call = translation_service.TranslationService._call_openrouter.__wrapped__

    with pytest.raises(translation_service.OpenRouterRetryableError) as exc:
        call(service, "text", service.model, [])

    assert "Malformed response" in str(exc.value)
    assert any(call.get("code") == "invalid_payload" for call in _collector)

    artifacts = list(service.failure_log_dir.glob("*.json"))
    assert len(artifacts) == 1
    data = json.loads(artifacts[0].read_text())
    assert data["reason"] == "invalid_payload"
