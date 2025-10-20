from __future__ import annotations

"""
Minimal GitHub Actions REST client for localhost admin.

Reads GH_TOKEN and GH_REPO (owner/repo) from environment or constructor args.
Only covers endpoints we need for read-only dashboard: list workflows, runs,
run details, jobs, and artifacts, plus artifact download.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


API_BASE = "https://api.github.com"


class GHError(RuntimeError):
    pass


@dataclass
class GHConfig:
    repo: str
    token: str


def _env_or(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name)
    return val if val else default


def detect_repo_from_git() -> Optional[str]:
    # Best-effort parse of 'origin' remote
    # Supports https remotes like github.com/<owner>/<repo>.git
    import subprocess
    try:
        url = (
            subprocess.check_output(["git", "remote", "get-url", "origin"], text=True)
            .strip()
        )
    except Exception:
        return None
    if "github.com" not in url:
        return None
    # strip suffix
    if url.endswith(".git"):
        url = url[:-4]
    # locate owner/repo
    try:
        owner_repo = url.split("github.com/")[-1]
        # handle URLs with credentials
        if "@github.com" in url:
            owner_repo = url.split("@github.com/")[-1]
        return owner_repo
    except Exception:
        return None


def make_config(repo: Optional[str] = None, token: Optional[str] = None) -> GHConfig:
    repo_eff = repo or _env_or("GH_REPO") or detect_repo_from_git()
    token_eff = token or _env_or("GH_TOKEN")
    if not repo_eff or not token_eff:
        raise GHError("Missing GH_REPO or GH_TOKEN; set env or provide explicitly.")
    return GHConfig(repo=repo_eff, token=token_eff)


class GHClient:
    def __init__(self, cfg: GHConfig) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {cfg.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "chinaxiv-admin-ci/1.0",
            }
        )

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{API_BASE}{path}"
        r = self.session.get(url, params=params, timeout=20)
        if not r.ok:
            raise GHError(f"GET {path} failed: {r.status_code} {r.text}")
        return r.json()

    def _post(self, path: str, json: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{API_BASE}{path}"
        r = self.session.post(url, json=json, timeout=20)
        if not r.ok:
            raise GHError(f"POST {path} failed: {r.status_code} {r.text}")
        if r.text:
            return r.json()
        return {}

    # Workflows
    def list_workflows(self) -> List[Dict[str, Any]]:
        data = self._get(f"/repos/{self.cfg.repo}/actions/workflows")
        return data.get("workflows", [])

    # Runs
    def list_runs(self, workflow_id: Optional[int] = None, per_page: int = 50) -> List[Dict[str, Any]]:
        if workflow_id:
            data = self._get(
                f"/repos/{self.cfg.repo}/actions/workflows/{workflow_id}/runs",
                params={"per_page": per_page},
            )
        else:
            data = self._get(
                f"/repos/{self.cfg.repo}/actions/runs", params={"per_page": per_page}
            )
        return data.get("workflow_runs", [])

    def get_run(self, run_id: int) -> Dict[str, Any]:
        return self._get(f"/repos/{self.cfg.repo}/actions/runs/{run_id}")

    def get_run_jobs(self, run_id: int) -> List[Dict[str, Any]]:
        data = self._get(f"/repos/{self.cfg.repo}/actions/runs/{run_id}/jobs")
        return data.get("jobs", [])

    # Artifacts
    def list_artifacts(self, run_id: int) -> List[Dict[str, Any]]:
        data = self._get(f"/repos/{self.cfg.repo}/actions/runs/{run_id}/artifacts")
        return data.get("artifacts", [])

    def download_artifact_zip(self, artifact_id: int, dest_path: str) -> str:
        # get the redirect URL, then stream to dest
        url = f"{API_BASE}/repos/{self.cfg.repo}/actions/artifacts/{artifact_id}/zip"
        r = self.session.get(url, allow_redirects=True, timeout=30)
        if not r.ok:
            raise GHError(f"Download artifact {artifact_id} failed: {r.status_code}")
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return dest_path

