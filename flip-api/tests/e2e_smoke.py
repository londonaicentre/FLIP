# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""End-to-end smoke that drives a fresh project from creation to results-downloaded.

Replaces the manual UI loop a developer goes through when sanity-checking a PR:
create project, approve, upload model files, wait for image pull, initiate
training, wait for training to finish, download the FL results. Hits the same
flip-api endpoints the UI does, so it covers the API + trust + FL integration
paths without the fragility of UI selectors.

Backend-agnostic: the script uploads whatever files are in `--model-files-dir`,
and the FL framework (Flower vs NVFLARE) is decided server-side by `FL_BACKEND`
in flip-api's bundling code. `make e2e_smoke` picks the chest-xray tutorial
that matches FL_BACKEND — the Flower tutorial lives in flip-fl-base-flower,
the NVFLARE one in flip-fl-base. Both reuse the same `query.sql` against the
trust mock OMOP data. Override either path with `--model-files-dir` and
`--query-file`.

Usage (preferred):
    make e2e_smoke                    # picks Flower tutorial (default)
    FL_BACKEND=nvflare make e2e_smoke # picks NVFLARE tutorial

Direct invocation:
    cd flip-api
    uv run python -m tests.e2e_smoke \\
        --model-files-dir ../../flip-fl-base-flower/tutorials/xray_classification/app \\
        --query-file ../../flip-fl-base-flower/tutorials/xray_classification/query.sql

Run on a stack that already has trusts approved (`make up` plus the usual
seeding) and non-empty XNAT data so image pull has something to do.

Pass --abort-midway to verify the FL "stop training" path (GitHub issue #490)
instead of running to completion: once training is live, the smoke POSTs
/fl/stop/{model_id} twice and asserts both return HTTP 204 — the first aborts
the running job, the second is an idempotent no-op.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.domain.schemas.status import ModelStatus
from flip_api.utils import constants
from tests.integration.utils import admin_authentication

# Resolve next to this file so the default works regardless of CWD (direct
# `uv run` from any directory, not just `flip-api/`). `make e2e_smoke` passes
# --query-file explicitly, so this default only kicks in for direct invocation.
DEFAULT_QUERY_FILE = Path(__file__).parent / "example_query.sql"
DEFAULT_PROJECT_NAME_PREFIX = "Xrays E2E Smoke"
DEFAULT_MODEL_NAME = "Xrays E2E Smoke Model"
ABORT_MIDWAY_NAME_SUFFIX = " (abort-midway)"

# Anything strictly past INITIATED. RESULTS_UPLOADED is included so a fast
# finish short-circuits wait_for_training_finished cleanly on the first poll.
TRAINING_PROGRESS_STATUSES = {
    ModelStatus.PREPARED.value,
    ModelStatus.TRAINING_STARTED.value,
    ModelStatus.RESULTS_UPLOADED.value,
}


class SmokeFailure(RuntimeError):
    pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def resolve_model_name(base_name: str, abort_midway: bool) -> str:
    """Return ``base_name`` plus the abort-midway suffix when the flag is set.

    The suffix lets the UI distinguish an abort-midway model from a full-train
    model when the same project hosts both (the typical --project-id reuse flow).
    Idempotent: a name that already ends with the suffix is returned unchanged.

    Args:
        base_name (str): The base model name (CLI default or --model-name override).
        abort_midway (bool): Whether the smoke run will exercise the abort path.

    Returns:
        str: ``base_name`` with the abort suffix when ``abort_midway`` is true and
        the suffix is not already present, otherwise ``base_name`` unchanged.
    """
    if not abort_midway or base_name.endswith(ABORT_MIDWAY_NAME_SUFFIX):
        return base_name
    return f"{base_name}{ABORT_MIDWAY_NAME_SUFFIX}"


def _post(
    client: requests.Session, path: str, json: dict[str, Any], headers: dict[str, str], timeout: int = 30
) -> requests.Response:
    return client.post(f"{constants.BASE_URL}{path}", json=json, headers=headers, timeout=timeout)


def _get(client: requests.Session, path: str, headers: dict[str, str], timeout: int = 30) -> requests.Response:
    return client.get(f"{constants.BASE_URL}{path}", headers=headers, timeout=timeout)


def _try_request(fn: Any, *args: Any, **kwargs: Any) -> requests.Response | None:
    """Run a request and swallow transient connection errors.

    Long polls (image pull, training) routinely outlast brief flip-api
    restarts or container reschedules. Treat ConnectionError / Timeout as
    "try again on the next tick" instead of crashing the whole smoke.
    """
    try:
        return fn(*args, **kwargs)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        _log(f"  ⚠️  transient request error ({type(exc).__name__}); will retry")
        return None


def _ensure_ok(response: requests.Response, what: str) -> requests.Response:
    if response.status_code >= 300:
        raise SmokeFailure(f"{what} failed with HTTP {response.status_code}: {response.text}")
    return response


def authenticate() -> dict[str, str]:
    _log("🔐 Authenticating as admin via Cognito…")
    headers = admin_authentication()
    _log("  ✅ Got auth token")
    return headers


def create_project_with_query(
    client: requests.Session, headers: dict[str, str], project_name: str, query: str
) -> tuple[str, str]:
    _log(f"🏗️  Creating project: {project_name}")
    project_payload = ProjectDetails(name=project_name, description="E2E smoke run", users=[]).model_dump()
    project_id = _ensure_ok(
        _post(client, "/projects/", project_payload, headers), "create project"
    ).json()["id"]
    _log(f"  ✅ project_id={project_id}")

    _log("📝 Adding cohort query")
    add_resp = _ensure_ok(
        _post(
            client,
            "/cohort/save/",
            {"query": query, "name": "E2E smoke query", "project_id": project_id},
            headers,
        ),
        "save cohort query",
    )
    query_id = add_resp.json()["query_id"]
    _log(f"  ✅ query_id={query_id}")

    _log("📤 Submitting query to trusts")
    _ensure_ok(
        _post(
            client,
            "/cohort/submit/",
            {
                "authenticationToken": headers.get("authorization", headers.get("Authorization", "")),
                "query": query,
                "name": "E2E smoke query",
                "project_id": project_id,
                "query_id": query_id,
            },
            headers,
            timeout=60,
        ),
        "submit cohort query",
    )
    _log("  ✅ submitted")
    return project_id, query_id


def wait_for_trusts_queried(
    client: requests.Session, headers: dict[str, str], project_id: str, timeout_s: int = 120
) -> int:
    """Block until the project's query has been answered by ≥1 trust.

    `/projects/{id}/stage` rejects a project whose query.trustsQueried is 0,
    and the trust query is dispatched async by `/cohort/submit/`. Without this
    poll the smoke races the submission and fails at staging.
    """
    _log(f"⏳ Waiting for trusts to answer the cohort query (timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    last_count = -1
    while time.monotonic() < deadline:
        resp = _try_request(_get, client, f"/projects/{project_id}", headers)
        if resp is None or resp.status_code >= 300:
            time.sleep(5)
            continue
        query = resp.json().get("query") or {}
        count = int(query.get("trustsQueried") or 0)
        if count != last_count:
            _log(f"  📊 trustsQueried={count}")
            last_count = count
        if count > 0:
            return count
        time.sleep(5)
    raise SmokeFailure(
        f"No trust answered the cohort query within {timeout_s}s. "
        "Check trust-api / data-access-api logs for query failures."
    )


def stage_and_approve(client: requests.Session, headers: dict[str, str], project_id: str) -> list[dict[str, Any]]:
    _log("🏥 Fetching trusts")
    trusts = _ensure_ok(_get(client, "/trust/", headers), "list trusts").json()
    if not trusts:
        raise SmokeFailure("No trusts registered with the hub — start the trust services and seed first")
    _log(f"  ✅ found {len(trusts)} trust(s): {[t['name'] for t in trusts]}")

    wait_for_trusts_queried(client, headers, project_id)

    trust_ids = [t["id"] for t in trusts]
    _log("📋 Staging project")
    _ensure_ok(
        _post(client, f"/projects/{project_id}/stage/", {"trusts": trust_ids}, headers),
        "stage project",
    )
    _log("✅ Approving project (step function)")
    _ensure_ok(
        _post(client, f"/step/project/{project_id}/approve/", {"trusts": trust_ids}, headers),
        "approve project",
    )
    _log("  ✅ approved")
    return trusts


def _import_progress(status: dict[str, Any]) -> tuple[int, int]:
    """Return (successful, total) counts for one trust's import status.

    A trust that's still waiting for `projectCreationCompleted` reports no
    importStatus yet — treat that as 0/0 so the caller polls again.
    """
    import_status = status.get("importStatus")
    if not import_status:
        return 0, 0
    successful = int(import_status.get("successful", 0))
    failed = int(import_status.get("failed", 0))
    processing = int(import_status.get("processing", 0))
    queued = int(import_status.get("queued", 0))
    queue_failed = int(import_status.get("queueFailed", 0))
    return successful, successful + failed + processing + queued + queue_failed


def wait_for_image_pull(
    client: requests.Session,
    headers: dict[str, str],
    project_id: str,
    threshold: float,
    timeout_s: int,
) -> None:
    _log(f"⏳ Waiting for image pull (≥{int(threshold * 100)}% per trust, timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    last_summary = ""
    poll_interval = 10
    while time.monotonic() < deadline:
        resp = _try_request(_get, client, f"/projects/{project_id}/image/status", headers)
        if resp is None:
            time.sleep(poll_interval)
            continue
        if resp.status_code == 404:
            # Imaging tasks not yet dispatched — keep polling.
            time.sleep(poll_interval)
            continue
        if resp.status_code >= 300:
            raise SmokeFailure(f"image-status failed: HTTP {resp.status_code} {resp.text}")
        statuses = resp.json()
        if not statuses:
            time.sleep(poll_interval)
            continue

        per_trust = []
        all_ready = True
        for s in statuses:
            successful, total = _import_progress(s)
            ratio = (successful / total) if total else 0.0
            per_trust.append(f"{s['trustName']}: {successful}/{total} ({ratio:.0%})")
            if total == 0 or ratio < threshold:
                all_ready = False

        summary = " | ".join(per_trust)
        if summary != last_summary:
            _log(f"  📊 {summary}")
            last_summary = summary
        if all_ready:
            _log("  ✅ image pull threshold reached")
            return
        time.sleep(poll_interval)

    raise SmokeFailure(
        f"Image pull did not reach {int(threshold * 100)}% within {timeout_s}s. "
        f"Last status: {last_summary or 'no per-trust status yet'}"
    )


def create_model(client: requests.Session, headers: dict[str, str], project_id: str, model_name: str) -> str:
    _log(f"🤖 Creating model: {model_name}")
    resp = _ensure_ok(
        _post(
            client,
            "/model",
            {"name": model_name, "description": "E2E smoke model", "projectId": project_id},
            headers,
        ),
        "create model",
    )
    model_id = resp.json()["id"]
    _log(f"  ✅ model_id={model_id}")
    return model_id


def _blacklisted_filenames() -> set[str]:
    """Mirror flip-ui's BLACKLISTED_MODEL_FILES filter so the smoke doesn't upload
    framework internals (server_app.py, strategy.py, flip.py, …) that the UI rejects."""
    raw = os.environ.get("BLACKLISTED_MODEL_FILES", "")
    return {name.strip() for name in raw.split(",") if name.strip()}


def upload_files(
    client: requests.Session, headers: dict[str, str], model_id: str, files_dir: Path
) -> list[str]:
    if not files_dir.is_dir():
        raise SmokeFailure(f"--model-files-dir does not exist: {files_dir}")
    blacklist = _blacklisted_filenames()
    all_paths = sorted(p for p in files_dir.iterdir() if p.is_file())
    skipped = [p.name for p in all_paths if p.name in blacklist]
    paths = [p for p in all_paths if p.name not in blacklist]
    if not paths:
        raise SmokeFailure(f"No files found under {files_dir}")
    if skipped:
        _log(f"⏭️  Skipping {len(skipped)} blacklisted file(s): {', '.join(skipped)}")
    _log(f"📤 Uploading {len(paths)} file(s) from {files_dir}")
    uploaded: list[str] = []
    for path in paths:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        presigned_resp = _ensure_ok(
            _post(
                client,
                f"/files/preSignedUrl/model/{model_id}",
                {"fileName": path.name, "contentType": content_type},
                headers,
            ),
            f"presigned URL for {path.name}",
        )
        policy = presigned_resp.json()
        # Presigned POST policy: every signed field must be appended verbatim
        # and `file` must come last. S3 does not accept the flip-api auth
        # header on this request.
        with path.open("rb") as fh:
            post_resp = requests.post(
                policy["url"],
                data=policy["fields"],
                files={"file": (path.name, fh, content_type)},
                timeout=120,
            )
        if post_resp.status_code >= 300:
            raise SmokeFailure(f"S3 upload failed for {path.name}: HTTP {post_resp.status_code}")
        # The presigned POST only puts bytes in S3 — the DB row is written by
        # /files/process-scanned-file, which is the SNS-driven webhook the
        # antivirus scanner calls in prod. The UI invokes it directly after a
        # 3s grace, so the file shows up in the model dashboard. Without this
        # call, training initiates against a model that the UI considers
        # empty (and `required_files` enforcement would block a real user).
        _ensure_ok(
            _post(client, f"/files/process-scanned-file/{model_id}/{path.name}", {}, headers),
            f"process-scanned-file for {path.name}",
        )
        _log(f"  ✅ {path.name} ({path.stat().st_size} bytes)")
        uploaded.append(path.name)
    return uploaded


def initiate_training(
    client: requests.Session, headers: dict[str, str], model_id: str, trust_names: list[str]
) -> None:
    _log(f"🚀 Initiating training across trusts: {trust_names}")
    resp = _post(client, f"/fl/initiate/{model_id}", {"trusts": trust_names}, headers)
    if resp.status_code != 204:
        raise SmokeFailure(f"initiate training failed: HTTP {resp.status_code} {resp.text}")
    _log("  ✅ training initiated (model status now INITIATED)")


def wait_for_training_started(
    client: requests.Session, headers: dict[str, str], model_id: str, timeout_s: int
) -> str:
    _log(f"⏳ Waiting for FL pipeline to advance the model (timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    last_status = ""
    poll_interval = 5
    while time.monotonic() < deadline:
        resp = _try_request(_post, client, f"/step/model/{model_id}", {}, headers)
        if resp is None or resp.status_code >= 300:
            time.sleep(poll_interval)
            continue
        status = resp.json().get("status", "")
        if status != last_status:
            _log(f"  📊 status={status}")
            last_status = status
        if status == ModelStatus.ERROR.value:
            raise SmokeFailure("Model entered ERROR state — check flip-api / fl-server logs")
        if status in TRAINING_PROGRESS_STATUSES:
            return status
        time.sleep(poll_interval)
    raise SmokeFailure(
        f"Model did not advance past INITIATED within {timeout_s}s (last status: {last_status or 'unknown'}). "
        "Check that fl-server + fl-clients are running and that the FL scheduler picked up the job."
    )


def wait_for_training_finished(
    client: requests.Session, headers: dict[str, str], model_id: str, timeout_s: int
) -> str:
    """Block until the model reports RESULTS_UPLOADED (or surface ERROR fast).

    This is the long pole — real training on the xray tutorial against the
    development XNAT data takes several minutes per round. The default timeout
    is generous; bump --training-finish-timeout if your stack is slower.
    """
    _log(f"⏳ Waiting for training to finish + results upload (timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    last_status = ""
    poll_interval = 15
    while time.monotonic() < deadline:
        resp = _try_request(_post, client, f"/step/model/{model_id}", {}, headers)
        if resp is None or resp.status_code >= 300:
            time.sleep(poll_interval)
            continue
        status = resp.json().get("status", "")
        if status != last_status:
            _log(f"  📊 status={status}")
            last_status = status
        if status == ModelStatus.ERROR.value:
            raise SmokeFailure("Model entered ERROR state — check flip-api / fl-server logs")
        if status == ModelStatus.RESULTS_UPLOADED.value:
            return status
        time.sleep(poll_interval)
    raise SmokeFailure(
        f"Training did not finish within {timeout_s}s (last status: {last_status or 'unknown'}). "
        "Bump --training-finish-timeout, or check fl-server logs for stuck rounds."
    )


def wait_for_training_running(
    client: requests.Session, headers: dict[str, str], model_id: str, timeout_s: int
) -> str:
    """Block until the model reports TRAINING_STARTED — a genuinely live FL job.

    ``wait_for_training_started`` returns on the first status past INITIATED, which
    can be the transient PREPARED. The --abort-midway stop test wants a running job,
    so poll on for TRAINING_STARTED specifically. If training races to RESULTS_UPLOADED
    first, return that — stopping an already-finished job is still a valid idempotency
    check, the caller just notes it.
    """
    _log(f"⏳ Waiting for model to reach TRAINING_STARTED (timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    last_status = ""
    poll_interval = 5
    while time.monotonic() < deadline:
        resp = _try_request(_post, client, f"/step/model/{model_id}", {}, headers)
        if resp is None or resp.status_code >= 300:
            time.sleep(poll_interval)
            continue
        status = resp.json().get("status", "")
        if status != last_status:
            _log(f"  📊 status={status}")
            last_status = status
        if status == ModelStatus.ERROR.value:
            raise SmokeFailure("Model entered ERROR state before training started — check fl-server logs")
        if status in (ModelStatus.TRAINING_STARTED.value, ModelStatus.RESULTS_UPLOADED.value):
            return status
        time.sleep(poll_interval)
    raise SmokeFailure(
        f"Model did not reach TRAINING_STARTED within {timeout_s}s (last status: {last_status or 'unknown'})."
    )


def stop_training(client: requests.Session, headers: dict[str, str], model_id: str, *, attempt: int) -> None:
    """POST /fl/stop/{model_id} and assert a clean HTTP 204 — the #490 contract."""
    _log(f"🛑 Stop attempt #{attempt}: POST /fl/stop/{model_id}")
    resp = _post(client, f"/fl/stop/{model_id}", {}, headers)
    if resp.status_code != 204:
        raise SmokeFailure(f"stop attempt #{attempt} expected HTTP 204, got {resp.status_code}: {resp.text}")
    _log(f"  ✅ stop #{attempt} returned 204")


def assert_model_stopped(client: requests.Session, headers: dict[str, str], model_id: str) -> None:
    """After a successful stop the model should report STOPPED.

    There is no GET /model/{id}; /step/model/{id} is the codebase's canonical
    "current model status" call (it is what every wait_for_* helper polls).
    """
    resp = _ensure_ok(_post(client, f"/step/model/{model_id}", {}, headers), "fetch model status after stop")
    status = resp.json().get("status", "")
    if status != ModelStatus.STOPPED.value:
        raise SmokeFailure(f"expected model status STOPPED after stop, got {status!r}")
    _log(f"  ✅ model status is {status}")


def download_results(
    client: requests.Session, headers: dict[str, str], model_id: str, dest_dir: Path
) -> list[Path]:
    """Pull every FL-result artefact from S3 to ``dest_dir`` and return paths.

    /files/model/{model_id}/fl/results returns a list of presigned S3 URLs
    (one per artefact). The download itself is unauthenticated S3 — pass the
    URLs straight to requests.get, no flip-api headers.
    """
    _log(f"📥 Fetching FL result presigned URLs for model {model_id}")
    resp = _ensure_ok(_get(client, f"/files/model/{model_id}/fl/results", headers), "list FL results")
    urls = resp.json()
    if not urls:
        raise SmokeFailure(
            "FL result list is empty — fl-server should have uploaded at least one artefact "
            "by RESULTS_UPLOADED time."
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    _log(f"  ✅ {len(urls)} artefact(s); downloading to {dest_dir}")
    paths: list[Path] = []
    for url in urls:
        # Assumes path-style S3 URLs and unencoded keys (both hold for current
        # FL artefact naming). Falls back to model_id + index otherwise.
        key = url.split("?", 1)[0].rsplit("/", 1)[-1] or f"{model_id}-{len(paths)}"
        out = dest_dir / key
        with requests.get(url, stream=True, timeout=120) as r:
            if r.status_code >= 300:
                raise SmokeFailure(f"S3 GET for {key} failed: HTTP {r.status_code}")
            with out.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    fh.write(chunk)
        _log(f"    📦 {key} ({out.stat().st_size} bytes)")
        paths.append(out)
    return paths


def run_data_enrichment(cwd: Path, cmd: str, project_id: str) -> None:
    """Run an optional data-enrichment step between image pull and training initiation.

    Generic hook -- the shell command runs in ``cwd`` with ``FLIP_PROJECT_ID``
    exported, so any project-aware enrichment (e.g. the spleen segmentation
    tutorial's upload-labels-to-XNAT step) can resolve the per-trust XNAT id from
    the central-hub project id. Non-zero exit raises ``SmokeFailure``.

    ``cwd`` existence and the cwd/cmd pairing are validated upfront in
    :func:`parse_args` so misuse fails before the multi-minute image-pull wait.
    """
    _log(f"🧪 Data enrichment: running `{cmd}` in {cwd} (FLIP_PROJECT_ID={project_id})")
    env = {**os.environ, "FLIP_PROJECT_ID": project_id}
    result = subprocess.run(cmd, shell=True, cwd=str(cwd), env=env)
    if result.returncode != 0:
        raise SmokeFailure(
            f"Data-enrichment command failed (exit {result.returncode}): {cmd}"
        )
    _log("  ✅ data enrichment complete")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--model-files-dir",
        type=Path,
        required=True,
        help="Directory whose files are uploaded to the model. Flower: "
        "../../flip-fl-base-flower/tutorials/xray_classification/app. "
        "NVFLARE: ../../flip-fl-base/tutorials/image_classification/xray_classification/app_files.",
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        default=DEFAULT_QUERY_FILE,
        help="SQL file to use as the project's cohort query (default: %(default)s)",
    )
    parser.add_argument(
        "--project-name",
        default=None,
        help=f"Project name (default: '{DEFAULT_PROJECT_NAME_PREFIX} <epoch>')",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Reuse an existing approved project: skip cohort submission and approval; jump straight "
        "to model creation + upload + training. Image-pull wait still runs (cheap when already at "
        "100%, correct when a prior --abort-midway run left pulls in flight). Lets you iterate on "
        "training code without re-creating the project for every retry.",
    )
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument(
        "--image-pull-threshold",
        type=float,
        default=0.8,
        help="Per-trust successful/total ratio that counts as 'mostly pulled' (default: %(default)s)",
    )
    parser.add_argument(
        "--image-pull-timeout",
        type=int,
        default=900,
        help="Seconds to wait for the image-pull threshold (default: %(default)s)",
    )
    parser.add_argument(
        "--training-start-timeout",
        type=int,
        default=300,
        help="Seconds to wait for the model to advance past INITIATED (default: %(default)s)",
    )
    parser.add_argument(
        "--training-finish-timeout",
        type=int,
        default=3600,
        help="Seconds to wait for the model to reach RESULTS_UPLOADED (default: %(default)s)",
    )
    parser.add_argument(
        "--abort-midway",
        action="store_true",
        help="#490 stop-training check: once training is running, POST /fl/stop/{model_id} twice "
        "(first aborts the live job, second is an idempotent no-op) instead of waiting for training "
        "to finish + downloading results. Both stops must return HTTP 204.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Directory to download FL results into (default: a fresh tempdir, kept after exit).",
    )
    parser.add_argument(
        "--data-enrichment-cwd",
        type=Path,
        default=None,
        help="With --data-enrichment-cmd, run that shell command in this directory "
        "between image pull and training initiation, with FLIP_PROJECT_ID exported. "
        "Generic hook for project-specific steps (e.g. the spleen segmentation "
        "tutorial's upload-labels-to-XNAT step).",
    )
    parser.add_argument(
        "--data-enrichment-cmd",
        default=None,
        help="Shell command for the data-enrichment step (paired with --data-enrichment-cwd).",
    )
    args = parser.parse_args(argv)

    # Validate the data-enrichment pair upfront so a typo doesn't surface only
    # after the 5–15 min image-pull wait. Both flags are required together;
    # the cwd must exist.
    if bool(args.data_enrichment_cmd) != bool(args.data_enrichment_cwd):
        parser.error("--data-enrichment-cmd and --data-enrichment-cwd must be used together")
    if args.data_enrichment_cwd is not None and not args.data_enrichment_cwd.exists():
        parser.error(f"--data-enrichment-cwd does not exist: {args.data_enrichment_cwd}")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_name = args.project_name or f"{DEFAULT_PROJECT_NAME_PREFIX} {int(time.time())}"

    if not args.query_file.exists():
        _log(f"❌ Query file not found: {args.query_file}")
        return 2

    query = args.query_file.read_text()
    headers = authenticate()
    client = requests.Session()
    client.headers.update({"Content-Type": "application/json"})

    try:
        if args.project_id:
            project_id = args.project_id
            _log(f"♻️  Reusing existing project_id={project_id} (skipping cohort + approval)")
            trusts = _ensure_ok(_get(client, "/trust/", headers), "list trusts").json()
            if not trusts:
                raise SmokeFailure("No trusts registered with the hub")
            _log(f"  ✅ found {len(trusts)} trust(s): {[t['name'] for t in trusts]}")
        else:
            project_id, _query_id = create_project_with_query(client, headers, project_name, query)
            trusts = stage_and_approve(client, headers, project_id)
        # Create the model and upload files before waiting for image pull. This
        # surfaces model-creation / upload errors immediately instead of after
        # 5–15 minutes of XNAT pulling, and the FL pipeline only consumes the
        # images at training time anyway.
        model_name = resolve_model_name(args.model_name, args.abort_midway)
        model_id = create_model(client, headers, project_id, model_name)
        upload_files(client, headers, model_id, args.model_files_dir)
        # Always wait for image pull, including on --project-id reuse: a prior
        # run on this project may have left pulls in flight (aborted midway,
        # failed, or simply queued back-to-back before the first pull finished),
        # in which case skipping the wait here would have wait_for_training_started
        # sit blocked on the (still pulling) FL clients until it times out.
        wait_for_image_pull(
            client, headers, project_id, args.image_pull_threshold, args.image_pull_timeout
        )
        if args.data_enrichment_cmd:
            run_data_enrichment(args.data_enrichment_cwd, args.data_enrichment_cmd, project_id)
        initiate_training(client, headers, model_id, [t["name"] for t in trusts])
        wait_for_training_started(client, headers, model_id, args.training_start_timeout)
        if args.abort_midway:
            # #490: exercise the FL "stop training" path instead of running to completion.
            running_status = wait_for_training_running(
                client, headers, model_id, args.training_start_timeout
            )
            if running_status == ModelStatus.RESULTS_UPLOADED.value:
                _log("  ⚠️  training finished before the stop — both stops now exercise the "
                     "idempotent (already-terminal) path only")
            # Stop #1 aborts the running job; stop #2 is an idempotent no-op. Both must 204.
            stop_training(client, headers, model_id, attempt=1)
            assert_model_stopped(client, headers, model_id)
            time.sleep(5)
            stop_training(client, headers, model_id, attempt=2)
            assert_model_stopped(client, headers, model_id)
            final_status = ModelStatus.STOPPED.value
            results_dir = None
            downloaded = []
        else:
            results_dir = args.results_dir or Path(tempfile.mkdtemp(prefix="flip-e2e-results-"))
            final_status = wait_for_training_finished(
                client, headers, model_id, args.training_finish_timeout
            )
            downloaded = download_results(client, headers, model_id, results_dir)
    except SmokeFailure as exc:
        _log(f"\n❌ Smoke failed: {exc}")
        return 1
    except requests.exceptions.RequestException as exc:
        # Backstop for one-shot calls that bypass _try_request (e.g. create_model).
        _log(f"\n❌ Smoke failed: unhandled request error: {type(exc).__name__}: {exc}")
        return 1

    _log("\n" + "=" * 60)
    _log("🎉 Smoke passed")
    _log(f"   project_id   = {project_id}")
    _log(f"   model_id     = {model_id}")
    _log(f"   final_status = {final_status}")
    if args.abort_midway:
        _log("   stop #1 (abort running job) + stop #2 (idempotent no-op) both returned 204")
    else:
        _log(f"   results_dir  = {results_dir} ({len(downloaded)} file(s))")
    _log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
