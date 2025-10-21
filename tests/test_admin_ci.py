import base64
import os
from werkzeug.security import generate_password_hash

import pytest


def _auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class _FakeGHClient:
    def __init__(self, cfg):
        pass

    def list_runs(self, per_page: int = 20):
        return [
            {
                "id": 1,
                "name": "build-and-deploy",
                "status": "completed",
                "conclusion": "success",
                "run_started_at": "2025-10-20T12:00:00Z",
                "updated_at": "2025-10-20T12:05:00Z",
                "path": ".github/workflows/build.yml",
            }
        ]

    def list_workflows(self):
        return [
            {"id": 123, "name": "rebuild-from-b2", "path": ".github/workflows/rebuild-from-b2.yml"}
        ]

    def dispatch_workflow(self, workflow_id, ref: str, inputs=None):
        return None


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    # Configure hashed password
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    # Provide GH envs but we will mock GH client
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_REPO", "owner/repo")
    yield


def test_auth_required():
    from src.admin_ci import make_app

    app = make_app()
    client = app.test_client()
    # No auth
    r = client.get("/admin")
    assert r.status_code == 401
    # Wrong password
    r = client.get("/admin", headers=_auth("u", "wrong"))
    assert r.status_code == 401


def test_admin_home_success(monkeypatch):
    from src import admin_ci

    # Patch GH client and config to avoid network
    monkeypatch.setattr(admin_ci, "GHClient", _FakeGHClient)
    monkeypatch.setattr(admin_ci, "make_config", lambda: object())

    app = admin_ci.make_app()
    client = app.test_client()
    r = client.get("/admin", headers=_auth("u", "pw"))
    assert r.status_code == 200
    assert b"Basic Metrics" in r.data


def test_dispatch_requires_confirmation(monkeypatch):
    from src import admin_ci

    monkeypatch.setattr(admin_ci, "GHClient", _FakeGHClient)
    monkeypatch.setattr(admin_ci, "make_config", lambda: object())

    app = admin_ci.make_app()
    client = app.test_client()
    # GET form
    r = client.get("/admin/ci/workflow/123/dispatch", headers=_auth("u", "pw"))
    assert r.status_code == 200
    # POST without confirm
    r = client.post(
        "/admin/ci/workflow/123/dispatch",
        data={"ref": "main"},
        headers=_auth("u", "pw"),
    )
    assert r.status_code == 400
    assert b"Please confirm dispatch" in r.data

