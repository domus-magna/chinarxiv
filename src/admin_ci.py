"""
Local-only Admin CI dashboard for GitHub Actions (Phase 1+2).

Endpoints:
  - /admin                : landing with quick status + config sanity
  - /admin/ci/workflows   : list workflows
  - /admin/ci/runs        : list recent runs
  - /admin/ci/run/<id>    : run details, jobs, and artifacts (preview JSON reports)
  - /admin/ci/workflow/<id>/dispatch : dispatch UI (Phase 2)

Auth: Basic password via ADMIN_PASSWORD (env). Bind to localhost only.
Config: GH_TOKEN and GH_REPO required for API access.
Run:   make admin
"""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from .gh_actions import GHClient, make_config
from .gha_workflow_config import get_dispatch_inputs


def require_password() -> str:
    pw = os.getenv("ADMIN_PASSWORD")
    if not pw:
        raise RuntimeError("ADMIN_PASSWORD not set; export it to protect the admin UI")
    return pw


def basic_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return Response(status=401, headers={"WWW-Authenticate": "Basic realm=admin"})
        try:
            raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
            _, password = raw.split(":", 1)
        except Exception:
            return Response(status=401, headers={"WWW-Authenticate": "Basic realm=admin"})
        if password != require_password():
            return Response(status=401, headers={"WWW-Authenticate": "Basic realm=admin"})
        return f(*args, **kwargs)

    return wrapper


def make_app() -> Flask:
    app = Flask(__name__)

    @app.route("/admin")
    @basic_auth
    def admin_home():
        cfg_error: Optional[str] = None
        workflows = []
        latest_runs = []
        try:
            cfg = make_config()
            client = GHClient(cfg)
            workflows = client.list_workflows()[:10]
            latest_runs = client.list_runs(per_page=20)
        except Exception as e:
            cfg_error = str(e)

        return render_template(
            "admin/home.html",
            cfg_error=cfg_error,
            workflows=workflows,
            latest_runs=latest_runs,
            repo=os.getenv("GH_REPO"),
        )

    # Convenience: redirect / to /admin
    @app.route("/")
    def root_redirect():
        return redirect(url_for("admin_home"))

    @app.route("/admin/ci/workflows")
    @basic_auth
    def admin_workflows():
        try:
            client = GHClient(make_config())
            workflows = client.list_workflows()
        except Exception as e:
            return render_template("admin/error.html", message=str(e)), 500
        return render_template("admin/workflows.html", workflows=workflows)

    @app.route("/admin/ci/runs")
    @basic_auth
    def admin_runs():
        try:
            client = GHClient(make_config())
            runs = client.list_runs(per_page=50)
        except Exception as e:
            return render_template("admin/error.html", message=str(e)), 500
        return render_template("admin/runs.html", runs=runs)

    @app.route("/admin/ci/run/<int:run_id>")
    @basic_auth
    def admin_run(run_id: int):
        try:
            client = GHClient(make_config())
            run = client.get_run(run_id)
            jobs = client.get_run_jobs(run_id)
            artifacts = client.list_artifacts(run_id)
            previews: Dict[str, Dict[str, Any]] = {}
            # Attempt to preview well-known JSON artifacts
            for art in artifacts:
                name = art.get("name", "")
                if name in ("harvest_report", "translation_report", "ocr_report"):
                    # Download to memory and extract
                    zip_bytes = io.BytesIO()
                    path = client.download_artifact_zip(art["id"], dest_path=str(Path("/tmp") / f"{art['id']}.zip"))
                    with open(path, "rb") as fh:
                        zip_bytes.write(fh.read())
                    zip_bytes.seek(0)
                    with zipfile.ZipFile(zip_bytes) as zf:
                        for zname in zf.namelist():
                            if zname.endswith(".json"):
                                data = json.loads(zf.read(zname))
                                previews[name] = data.get("summary", data)
                                break
            return render_template(
                "admin/run.html", run=run, jobs=jobs, artifacts=artifacts, previews=previews
            )
        except Exception as e:
            return render_template("admin/error.html", message=str(e)), 500

    @app.route("/admin/ci/workflow/<int:wfid>/dispatch", methods=["GET", "POST"])
    @basic_auth
    def admin_dispatch(wfid: int):
        try:
            client = GHClient(make_config())
            # Find the workflow by id to get its path and name
            workflows = client.list_workflows()
            wf = next((w for w in workflows if int(w.get("id")) == int(wfid)), None)
            if not wf:
                abort(404)
            inputs_spec = {}
            # Parse local workflow YAML to extract inputs
            wf_path = wf.get("path")
            if wf_path and Path(wf_path).exists():
                inputs_spec = get_dispatch_inputs(wf_path)

            if request.method == "POST":
                # Guard: require confirmation checkbox
                if request.form.get("confirm") != "on":
                    return render_template(
                        "admin/dispatch.html",
                        workflow=wf,
                        inputs_spec=inputs_spec,
                        error="Please confirm dispatch by checking the box.",
                    ), 400

                ref = request.form.get("ref", "main").strip() or "main"
                inputs: Dict[str, Any] = {}
                for name, spec in inputs_spec.items():
                    typ = (spec.get("type") or "string").lower()
                    if typ == "boolean":
                        inputs[name] = True if request.form.get(name) == "on" else False
                    else:
                        val = request.form.get(name)
                        if val is not None and val != "":
                            inputs[name] = val
                        elif "default" in spec:
                            inputs[name] = spec["default"]
                client.dispatch_workflow(wfid, ref=ref, inputs=inputs or None)
                return redirect(url_for("admin_runs"))

            return render_template(
                "admin/dispatch.html", workflow=wf, inputs_spec=inputs_spec
            )
        except Exception as e:
            return render_template("admin/error.html", message=str(e)), 500

    return app


if __name__ == "__main__":
    app = make_app()
    app.run(host="127.0.0.1", port=int(os.getenv("ADMIN_PORT", "8081")))
