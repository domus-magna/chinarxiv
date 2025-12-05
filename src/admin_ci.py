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
import logging
import tempfile
from werkzeug.security import check_password_hash

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from .gh_actions import GHClient, make_config
from .gha_workflow_config import get_dispatch_inputs, describe_workflow
import contextlib


logger = logging.getLogger(__name__)


def _get_passwords() -> Dict[str, Optional[str]]:
    """Return configured admin password hash/plain.

    Prefer ADMIN_PASSWORD_HASH. Fall back to ADMIN_PASSWORD (legacy).
    """
    return {
        "hash": os.getenv("ADMIN_PASSWORD_HASH"),
        "plain": os.getenv("ADMIN_PASSWORD"),
    }


def basic_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return Response(
                status=401, headers={"WWW-Authenticate": "Basic realm=admin"}
            )
        try:
            raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
            username, password = raw.split(":", 1)
        except Exception:
            return Response(
                status=401, headers={"WWW-Authenticate": "Basic realm=admin"}
            )
        creds = _get_passwords()
        ok = False
        if creds["hash"]:
            ok = check_password_hash(creds["hash"], password)
        elif creds["plain"]:
            ok = password == creds["plain"]
            if ok:
                logger.warning(
                    "ADMIN_PASSWORD used without hash; set ADMIN_PASSWORD_HASH for stronger security"
                )
        if not ok:
            logger.warning(
                "Admin auth failed for user=%s from %s", username, request.remote_addr
            )
            return Response(
                status=401, headers={"WWW-Authenticate": "Basic realm=admin"}
            )
        logger.info(
            "Admin access %s by user=%s from %s",
            request.path,
            username,
            request.remote_addr,
        )
        return f(*args, **kwargs)

    return wrapper


def make_app() -> Flask:
    # Ensure Flask finds templates at repo_root/templates
    tpl_dir = (Path(__file__).resolve().parent.parent / "templates").as_posix()
    app = Flask(__name__, template_folder=tpl_dir)

    # Inject current local time into all templates
    from datetime import datetime

    def _now_local_str() -> str:
        dt = datetime.now().astimezone()
        # Example: Mon Oct 20, 2025 02:45:07 PM PDT
        tz = dt.tzname() or "local"
        return dt.strftime(f"%a %b %d, %Y %I:%M:%S %p {tz}")

    @app.context_processor
    def _inject_now():
        return {"now_local": _now_local_str()}

    # Template filter: ISO8601 -> local time string
    from dateutil import parser as dtparser

    def dt_local(iso_str: Optional[str], fmt: str = "%Y-%m-%d %I:%M:%S %p %Z") -> str:
        try:
            if not iso_str:
                return ""
            dt = dtparser.isoparse(iso_str)
            dt_local = dt.astimezone()
            return dt_local.strftime(fmt)
        except Exception:
            return str(iso_str or "")

    app.jinja_env.filters["dt_local"] = dt_local

    # Duration helper: compute human-readable delta between two ISO times
    def duration(iso_start: Optional[str], iso_end: Optional[str] = None) -> str:
        try:
            if not iso_start:
                return ""
            start = dtparser.isoparse(iso_start)
            if iso_end:
                end = dtparser.isoparse(iso_end)
            else:
                from datetime import datetime as _dt, timezone as _tz

                end = _dt.now(_tz.utc)
            delta = end - start
            total = int(delta.total_seconds())
            if total < 0:
                total = 0
            days, rem = divmod(total, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, seconds = divmod(rem, 60)
            if days > 0:
                return f"{days}d {hours:02}:{minutes:02}:{seconds:02}"
            if hours > 0:
                return f"{hours}:{minutes:02}:{seconds:02}"
            return f"{minutes}:{seconds:02}"
        except Exception:
            return ""

    app.jinja_env.filters["duration"] = duration

    @app.route("/admin")
    @basic_auth
    def admin_home():
        cfg_error: Optional[str] = None
        latest_runs = []
        metrics = {}
        try:
            cfg = make_config()
            client = GHClient(cfg)
            latest_runs = client.list_runs(per_page=20)

            # Hide irrelevant automation runs (e.g., Claude Code)
            def _is_hidden_workflow(name: str, path: str | None = None) -> bool:
                s = (name or "").lower()
                p = (path or "").lower()
                return ("claude" in s) or ("claude" in p)

            latest_runs = [
                r
                for r in latest_runs
                if not _is_hidden_workflow(r.get("name", ""), r.get("path"))
            ]

            # Basic metrics from recent runs (simple counts)
            successes = sum(
                1
                for r in latest_runs
                if (r.get("conclusion") or "").lower() == "success"
            )
            failures = sum(
                1
                for r in latest_runs
                if (r.get("conclusion") or "").lower() == "failure"
            )
            cancelled = sum(
                1
                for r in latest_runs
                if (r.get("conclusion") or "").lower() == "cancelled"
            )
            in_progress = sum(
                1
                for r in latest_runs
                if (r.get("status") or "").lower() in ("in_progress", "queued")
            )
            metrics = {
                "window": len(latest_runs),
                "successes": successes,
                "failures": failures,
                "cancelled": cancelled,
                "in_progress": in_progress,
                "total": len(latest_runs),
            }
        except Exception as e:
            logger.error("admin_home error: %s", e, exc_info=True)
            cfg_error = "Configuration error. See logs."

        return render_template(
            "admin/home.html",
            cfg_error=cfg_error,
            latest_runs=latest_runs,
            repo=os.getenv("GH_REPO"),
            metrics=metrics,
        )

    # Convenience: redirect / to /admin
    @app.route("/")
    def root_redirect():
        return redirect(url_for("admin_home"))

    # Quiet favicon requests to avoid 404 noise
    @app.route("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.route("/admin/ci/workflows")
    @basic_auth
    def admin_workflows():
        try:
            client = GHClient(make_config())
            workflows = client.list_workflows()

            # Hide irrelevant automation workflows (e.g., Claude Code)
            def _is_hidden_workflow(w: Dict[str, Any]) -> bool:
                name = (w.get("name") or "").lower()
                path = (w.get("path") or "").lower()
                return ("claude" in name) or ("claude" in path)

            workflows = [w for w in workflows if not _is_hidden_workflow(w)]
            # Attach simple natural-language descriptions from the YAML
            for w in workflows:
                w["description"] = ""
                path = w.get("path")
                if path and Path(path).exists():
                    try:
                        w["description"] = describe_workflow(path)
                    except Exception:
                        w["description"] = ""
        except Exception as e:
            logger.error("admin_workflows error: %s", e, exc_info=True)
            return render_template("admin/error.html", message="An error occurred"), 500
        return render_template("admin/workflows.html", workflows=workflows)

    @app.route("/admin/ci/runs")
    @basic_auth
    def admin_runs():
        try:
            client = GHClient(make_config())
            runs = client.list_runs(per_page=50)
        except Exception as e:
            logger.error("admin_runs error: %s", e, exc_info=True)
            return render_template("admin/error.html", message="An error occurred"), 500
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
            MAX_PREVIEW_BYTES = int(
                os.getenv("ADMIN_MAX_PREVIEW_BYTES", str(100 * 1024 * 1024))
            )
            for art in artifacts:
                name = art.get("name", "")
                if name in ("harvest_report", "translation_report", "ocr_report"):
                    size = int(art.get("size_in_bytes") or 0)
                    if size and size > MAX_PREVIEW_BYTES:
                        previews[name] = {"error": "Artifact too large for preview"}
                        continue
                    # Download to memory and extract
                    zip_bytes = io.BytesIO()
                    tmp_zip = Path(tempfile.gettempdir()) / f"{art['id']}.zip"
                    path = client.download_artifact_zip(
                        art["id"], dest_path=str(tmp_zip)
                    )
                    with open(path, "rb") as fh:
                        zip_bytes.write(fh.read())
                    zip_bytes.seek(0)
                    with zipfile.ZipFile(zip_bytes) as zf:
                        for zname in zf.namelist():
                            if zname.endswith(".json"):
                                data = json.loads(zf.read(zname))
                                previews[name] = data.get("summary", data)
                                break
                    with contextlib.suppress(Exception):
                        tmp_zip.unlink(missing_ok=True)
            return render_template(
                "admin/run.html",
                run=run,
                jobs=jobs,
                artifacts=artifacts,
                previews=previews,
            )
        except Exception as e:
            logger.error("admin_run error: %s", e, exc_info=True)
            return render_template("admin/error.html", message="An error occurred"), 500

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
                        inputs[name] = request.form.get(name) == "on"
                    else:
                        val = request.form.get(name)
                        if val is not None and val != "":
                            if typ == "choice":
                                choices = (spec.get("options") or []) or (
                                    spec.get("choices") or []
                                )
                                if choices and val not in choices:
                                    return render_template(
                                        "admin/dispatch.html",
                                        workflow=wf,
                                        inputs_spec=inputs_spec,
                                        error=f"Invalid value for {name}",
                                    ), 400
                            inputs[name] = val
                        elif "default" in spec:
                            inputs[name] = spec["default"]
                logger.warning(
                    "Workflow %s dispatched ref=%s inputs=%s", wfid, ref, inputs
                )
                client.dispatch_workflow(wfid, ref=ref, inputs=inputs or None)
                return redirect(url_for("admin_runs"))

            return render_template(
                "admin/dispatch.html", workflow=wf, inputs_spec=inputs_spec
            )
        except Exception as e:
            logger.error("admin_dispatch error: %s", e, exc_info=True)
            return render_template("admin/error.html", message="An error occurred"), 500

    return app


if __name__ == "__main__":
    app = make_app()
    app.run(host="127.0.0.1", port=int(os.getenv("ADMIN_PORT", "8081")))
