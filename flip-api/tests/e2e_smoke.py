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
that matches FL_BACKEND — both tutorials are now in-tree under
fl-tutorials/<backend>/ (Flower at fl-tutorials/flower/, NVFLARE at
fl-tutorials/nvflare/). Both reuse the same `query.sql` against the trust mock
OMOP data. Override either path with `--model-files-dir` and `--query-file`.

Usage (preferred):
    make e2e_smoke                    # NVFLARE tutorial (default backend)
    make e2e_smoke FL_BACKEND=flower  # Flower tutorial

Direct invocation:
    cd flip-api
    uv run python -m tests.e2e_smoke \\
        --model-files-dir ../fl-tutorials/flower/xray_classification/app \\
        --query-file ../fl-tutorials/flower/xray_classification/query.sql

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

# How long to wait for uploaded files to clear malware scanning (#52). The
# scan runs server-side as a background task; tutorial-sized files finish in
# seconds, but a multi-GB checkpoint has to be downloaded and structurally
# scanned first, hence the generous ceiling.
FILE_SCAN_TIMEOUT_S = 600

# Anything strictly past INITIATED. RESULTS_UPLOADED is included so a fast
# finish short-circuits wait_for_training_finished cleanly on the first poll.
TRAINING_PROGRESS_STATUSES = {
    ModelStatus.PREPARED.value,
    ModelStatus.RUNNING.value,
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


def _maybe_refresh(headers: dict[str, str]) -> bool:
    """Refresh the bearer token in-place via Cognito REFRESH_TOKEN_AUTH.

    Used when the smoke runs token-driven against a remote hub (FLIP_E2E_TOKEN):
    a long run can outlast the access token's TTL, so a 401 triggers a refresh.
    Returns True if the token was refreshed.
    """
    refresh = os.environ.get("FLIP_E2E_REFRESH_TOKEN")
    client_id = os.environ.get("AWS_COGNITO_APP_CLIENT_ID")
    if not refresh or not client_id:
        return False
    import boto3

    try:
        resp = boto3.client("cognito-idp", region_name=os.environ.get("AWS_REGION", "eu-west-2")).initiate_auth(
            ClientId=client_id,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh},
        )
        headers["authorization"] = "Bearer " + resp["AuthenticationResult"]["AccessToken"]
        _log("  🔄 refreshed access token")
        return True
    except Exception as exc:  # noqa: BLE001 - refresh is best-effort
        _log(f"  ⚠️  token refresh failed: {exc}")
        return False


def _post(
    client: requests.Session, path: str, json: dict[str, Any], headers: dict[str, str], timeout: int = 30
) -> requests.Response:
    resp = client.post(f"{constants.BASE_URL}{path}", json=json, headers=headers, timeout=timeout)
    if resp.status_code == 401 and _maybe_refresh(headers):
        resp = client.post(f"{constants.BASE_URL}{path}", json=json, headers=headers, timeout=timeout)
    return resp


def _get(client: requests.Session, path: str, headers: dict[str, str], timeout: int = 30) -> requests.Response:
    resp = client.get(f"{constants.BASE_URL}{path}", headers=headers, timeout=timeout)
    if resp.status_code == 401 and _maybe_refresh(headers):
        resp = client.get(f"{constants.BASE_URL}{path}", headers=headers, timeout=timeout)
    return resp


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
    # FLIP_E2E_TOKEN lets the smoke run against a remote hub (stag/prod) with a
    # pre-obtained bearer token, bypassing the Cognito USER_PASSWORD_AUTH flow
    # (which is unavailable when the admin has MFA). _maybe_refresh keeps it live.
    token = os.environ.get("FLIP_E2E_TOKEN")
    if token:
        _log("🔐 Using FLIP_E2E_TOKEN (pre-supplied bearer token)")
        return {"scheme": "Bearer", "authorization": f"Bearer {token}"}
    _log("🔐 Authenticating as admin via Cognito…")
    headers = admin_authentication()
    _log("  ✅ Got auth token")
    return headers


def create_project_with_query(
    client: requests.Session, headers: dict[str, str], project_name: str, query: str, dicom_to_nifti: bool = True
) -> tuple[str, str]:
    _log(f"🏗️  Creating project: {project_name} (dicom_to_nifti={dicom_to_nifti})")
    project_payload = ProjectDetails(
        name=project_name, description="E2E smoke run", users=[], dicom_to_nifti=dicom_to_nifti
    ).model_dump()
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


def select_trusts(trusts: list[dict[str, Any]], selection: str | None) -> list[dict[str, Any]]:
    """Filter the ``GET /trust/`` listing down to a ``--trusts`` selection.

    Args:
        trusts (list[dict[str, Any]]): Trusts as returned by ``GET /trust/``.
        selection (str | None): Comma-separated trust codes or names, matched
            case-insensitively. None/empty selects every registered trust.

    Returns:
        list[dict[str, Any]]: The matching trusts, in listing order.

    Raises:
        SmokeFailure: If any selection token matches no registered trust.
    """
    if not selection:
        return trusts
    wanted = {token.strip().casefold() for token in selection.split(",") if token.strip()}
    selected = [
        t for t in trusts if (t.get("code") or "").casefold() in wanted or (t.get("name") or "").casefold() in wanted
    ]
    matched = {(t.get("code") or "").casefold() for t in selected} | {
        (t.get("name") or "").casefold() for t in selected
    }
    missing = sorted(wanted - matched)
    if missing:
        known = sorted(f"{t.get('code') or '?'} ({t.get('name')})" for t in trusts)
        raise SmokeFailure(f"--trusts entries not registered on the hub: {missing}. Registered trusts: {known}")
    return selected


def wait_for_trusts_responded(
    client: requests.Session,
    headers: dict[str, str],
    project_id: str,
    timeout_s: int = 120,
    required_trust_ids: set[str] | None = None,
) -> int:
    """Block until the required trusts have posted a cohort result.

    `/cohort/submit/` dispatches the query asynchronously: the hub records the
    dispatched trusts in `query.queriedTrustIds` immediately, but each trust
    only posts its result a few poll-cycles later (it polls the hub, runs the
    OMOP query, then POSTs `/cohort/results`). `/projects/{id}/stage` rejects a
    project whose staged trusts are not in `query.respondedTrustIds`, so the
    smoke must wait for the results to land — not merely for the dispatch.

    The hub always dispatches to every registered trust (`/cohort/submit/` has
    no subset parameter), so with ``required_trust_ids`` (the ``--trusts``
    subset) only those trusts must respond — a registered-but-offline trust no
    longer blocks the run.

    Args:
        client (requests.Session): HTTP session for hub calls.
        headers (dict[str, str]): Auth headers.
        project_id (str): Project whose cohort query to poll.
        timeout_s (int): Seconds to wait before failing.
        required_trust_ids (set[str] | None): Trust ids that must appear in
            ``respondedTrustIds``. None keeps the legacy behaviour (every
            queried trust must respond).

    Returns:
        int: Number of trusts that had responded when the wait was satisfied.
    """
    _log(f"⏳ Waiting for trusts to return cohort results (timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    last = (-1, -1)
    while time.monotonic() < deadline:
        resp = _try_request(_get, client, f"/projects/{project_id}", headers)
        if resp is None or resp.status_code >= 300:
            time.sleep(5)
            continue
        query = resp.json().get("query") or {}
        queried = len(query.get("queriedTrustIds") or [])
        responded_ids = {str(t) for t in (query.get("respondedTrustIds") or [])}
        responded = len(responded_ids)
        if (queried, responded) != last:
            _log(f"  📊 queriedTrustIds={queried}  respondedTrustIds={responded}")
            last = (queried, responded)
        if required_trust_ids is not None:
            if required_trust_ids <= responded_ids:
                return responded
        elif queried > 0 and responded >= queried:
            return responded
        time.sleep(5)
    raise SmokeFailure(
        f"Not all required trusts returned cohort results within {timeout_s}s. "
        "Check trust-api / data-access-api logs for query failures."
    )


def stage_and_approve(
    client: requests.Session, headers: dict[str, str], project_id: str, trusts_selection: str | None = None
) -> list[dict[str, Any]]:
    _log("🏥 Fetching trusts")
    trusts = _ensure_ok(_get(client, "/trust/", headers), "list trusts").json()
    if not trusts:
        raise SmokeFailure("No trusts registered with the hub — start the trust services and seed first")
    _log(f"  ✅ found {len(trusts)} trust(s): {[t['name'] for t in trusts]}")
    trusts = select_trusts(trusts, trusts_selection)
    if trusts_selection:
        _log(f"  🎯 --trusts selection: {[t.get('code') or t['name'] for t in trusts]}")

    wait_for_trusts_responded(
        client, headers, project_id, required_trust_ids={str(t["id"]) for t in trusts}
    )

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


def _import_progress(status: dict[str, Any]) -> tuple[int, int, int]:
    """Return (successful, in_flight, total) counts for one trust's import status.

    ``in_flight`` is processing + queued — work that may still complete.
    ``total`` includes the terminal ``failed`` + ``queueFailed`` counts so the
    caller can detect when failures alone push the max achievable ratio below
    threshold (otherwise wait_for_image_pull sits idle for the full timeout
    on any run with unrecoverable scan failures).

    A trust that's still waiting for `projectCreationCompleted` reports no
    importStatus yet — treat that as 0/0/0 so the caller polls again.
    """
    import_status = status.get("importStatus")
    if not import_status:
        return 0, 0, 0
    successful = int(import_status.get("successful", 0))
    failed = int(import_status.get("failed", 0))
    processing = int(import_status.get("processing", 0))
    queued = int(import_status.get("queued", 0))
    queue_failed = int(import_status.get("queueFailed", 0))
    return (
        successful,
        processing + queued,
        successful + failed + processing + queued + queue_failed,
    )


def wait_for_image_pull(
    client: requests.Session,
    headers: dict[str, str],
    project_id: str,
    threshold: float,
    timeout_s: int,
    required_trust_names: set[str] | None = None,
) -> None:
    """Block until every required trust's image pull reaches the threshold.

    With ``required_trust_names`` (the ``--trusts`` selection, matched against the
    status entries' ``trustName``), only those trusts must reach the bar — so a
    job targeting a subset of an existing multi-trust project isn't blocked by a
    non-selected (possibly offline) trust's pull entries. None keeps the legacy
    behaviour (every trust in the project must reach the threshold).
    """
    scope = f" across {sorted(required_trust_names)}" if required_trust_names else " per trust"
    _log(f"⏳ Waiting for image pull (≥{int(threshold * 100)}%{scope}, timeout {timeout_s}s)")
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
        if required_trust_names is not None:
            statuses = [s for s in statuses if s.get("trustName") in required_trust_names]
        if not statuses:
            time.sleep(poll_interval)
            continue

        per_trust = []
        all_ready = True
        unreachable: tuple[str, float] | None = None
        for s in statuses:
            successful, in_flight, total = _import_progress(s)
            ratio = (successful / total) if total else 0.0
            per_trust.append(f"{s['trustName']}: {successful}/{total} ({ratio:.0%})")
            # Failed + queueFailed scans never recover, so once
            # (successful + in_flight) / total dips below threshold the run
            # cannot reach the bar — fail fast instead of waiting out the
            # full timeout.
            if total > 0 and (successful + in_flight) / total < threshold:
                unreachable = (s["trustName"], (successful + in_flight) / total)
            if total == 0 or ratio < threshold:
                all_ready = False

        summary = " | ".join(per_trust)
        if summary != last_summary:
            _log(f"  📊 {summary}")
            last_summary = summary
        if unreachable:
            trust_name, max_ratio = unreachable
            raise SmokeFailure(
                f"{trust_name}: failed scans push max reachable ratio to "
                f"{max_ratio:.0%}, below threshold {int(threshold * 100)}%. "
                "Aborting — image pull will not recover."
            )
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
        # The presigned POST only puts bytes into the staging prefix — the DB
        # row is written by /files/process-scanned-file, which also kicks off
        # the malware scan that promotes the file into the bucket the FL
        # bundler reads from (#52). Without this call, training initiates
        # against a model whose files were never promoted.
        _ensure_ok(
            _post(client, f"/files/process-scanned-file/{model_id}/{path.name}", {}, headers),
            f"process-scanned-file for {path.name}",
        )
        _log(f"  ✅ {path.name} ({path.stat().st_size} bytes)")
        uploaded.append(path.name)
    wait_for_files_scanned(client, headers, model_id, uploaded, FILE_SCAN_TIMEOUT_S)
    return uploaded


def wait_for_files_scanned(
    client: requests.Session,
    headers: dict[str, str],
    model_id: str,
    file_names: list[str],
    timeout_s: int,
) -> None:
    """Block until every uploaded file has been scanned and promoted (#52).

    Files sit in ``SCANNING`` until the scan completes; only ``COMPLETED``
    files exist in the bucket the FL bundler reads, so training would fail
    confusingly if we proceeded early. ``INFECTED`` (scan rejected the file)
    and ``ERROR`` are terminal failures worth surfacing immediately.
    """
    _log(f"🔬 Waiting for {len(file_names)} file(s) to pass scanning (timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    poll_interval = 3
    pending: dict[str, str] = {name: "UNKNOWN" for name in file_names}
    while time.monotonic() < deadline:
        resp = _try_request(_post, client, f"/step/model/{model_id}", {}, headers)
        if resp is None or resp.status_code >= 300:
            time.sleep(poll_interval)
            continue
        statuses = {f["name"]: f.get("status", "UNKNOWN") for f in resp.json().get("files", [])}
        bad = {name: statuses.get(name) for name in file_names if statuses.get(name) in ("INFECTED", "ERROR")}
        if bad:
            raise SmokeFailure(
                f"File(s) failed malware scanning: {bad}. "
                "INFECTED means the scan judged the content unsafe and deleted it; "
                "ERROR means the file could not be scanned (fail-closed)."
            )
        pending = {name: statuses.get(name, "UNKNOWN") for name in file_names}
        if all(status == "COMPLETED" for status in pending.values()):
            _log("  ✅ all files scanned and promoted")
            return
        time.sleep(poll_interval)
    unfinished = {name: status for name, status in pending.items() if status != "COMPLETED"}
    raise SmokeFailure(
        f"File(s) did not finish scanning within {timeout_s}s: {unfinished}. "
        "Check flip-api logs for the malware-scan reconcile."
    )


def initiate_training(
    client: requests.Session, headers: dict[str, str], model_id: str, trusts: list[dict[str, Any]]
) -> None:
    trust_ids = [t["id"] for t in trusts]
    # Log the human-friendly trust codes (fall back to name, then id) but send the stable ids.
    labels = [t.get("code") or t.get("name") or t["id"] for t in trusts]
    _log(f"🚀 Initiating training across trusts: {labels}")
    resp = _post(client, f"/fl/initiate/{model_id}", {"trust_ids": trust_ids}, headers)
    if resp.status_code != 204:
        raise SmokeFailure(f"initiate training failed: HTTP {resp.status_code} {resp.text}")
    _log("  ✅ training initiated (model status now INITIATED)")


def wait_for_model_advanced(
    client: requests.Session, headers: dict[str, str], model_id: str, timeout_s: int
) -> str:
    """Block until the model reports any status past INITIATED (PREPARED counts).

    This is the "did the FL scheduler pick the job up at all" gate, not a wait for
    RUNNING — that lives in ``wait_for_model_running``.
    """
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


def wait_for_model_running(
    client: requests.Session, headers: dict[str, str], model_id: str, timeout_s: int
) -> str:
    """Block until the model reports RUNNING — a genuinely live FL job.

    ``wait_for_model_advanced`` returns on the first status past INITIATED, which
    can be the transient PREPARED. The --abort-midway stop test wants a running job,
    so poll on for RUNNING specifically. If training races to RESULTS_UPLOADED
    first, return that — stopping an already-finished job is still a valid idempotency
    check, the caller just notes it.
    """
    _log(f"⏳ Waiting for model to reach RUNNING (timeout {timeout_s}s)")
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
            raise SmokeFailure("Model entered ERROR state before reaching RUNNING — check fl-server logs")
        if status in (ModelStatus.RUNNING.value, ModelStatus.RESULTS_UPLOADED.value):
            return status
        time.sleep(poll_interval)
    raise SmokeFailure(
        f"Model did not reach RUNNING within {timeout_s}s (last status: {last_status or 'unknown'})."
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
        "../fl-tutorials/flower/xray_classification/app. "
        "NVFLARE: ../fl-tutorials/nvflare/image_classification/xray_classification/app_files.",
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
        "100%%, correct when a prior --abort-midway run left pulls in flight). Lets you iterate on "
        "training code without re-creating the project for every retry.",
    )
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument(
        "--no-dicom-to-nifti",
        action="store_true",
        help="Create the project with dicom_to_nifti=false (apps that read DICOMs directly, e.g. the "
        "Ark+ tutorials with ResourceType.ALL, skip the XNAT dcm2niix conversion). Set at project "
        "creation and immutable afterwards; ignored with --project-id.",
    )
    parser.add_argument(
        "--trusts",
        default=None,
        help="Comma-separated trust codes or names (case-insensitive) to run against, e.g. "
        "--trusts GSTT or --trusts 'GSTT,Bangkok Dusit Medical Services'. The cohort query is "
        "still dispatched to every registered trust (the API has no subset submit), but the smoke "
        "only waits for, stages, approves and trains the selected trusts — so a registered-but-"
        "offline trust no longer blocks the run. Default: every registered trust.",
    )
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

    # Against a TLS-terminating proxy (CloudFront → flip-api), FastAPI's
    # trailing-slash redirects come back with an http:// Location because uvicorn
    # sees the internal hop as plain HTTP. Following that http:// URL hits a
    # CloudFront 403. Rewrite redirect Locations to https:// so the smoke's
    # trailing-slash paths resolve. Only attach against an https BASE_URL — on a
    # local http stack the redirect Location is the real target and rewriting it
    # to https triggers an SSL handshake against a plain-http port.
    if constants.BASE_URL.startswith("https://"):

        def _force_https_redirect(response: requests.Response, *_: Any, **__: Any) -> None:
            loc = response.headers.get("location", "")
            if loc.startswith("http://"):
                response.headers["location"] = "https://" + loc[len("http://") :]

        client.hooks["response"].append(_force_https_redirect)

    try:
        if args.project_id:
            project_id = args.project_id
            _log(f"♻️  Reusing existing project_id={project_id} (skipping cohort + approval)")
            trusts = _ensure_ok(_get(client, "/trust/", headers), "list trusts").json()
            if not trusts:
                raise SmokeFailure("No trusts registered with the hub")
            _log(f"  ✅ found {len(trusts)} trust(s): {[t['name'] for t in trusts]}")
            trusts = select_trusts(trusts, args.trusts)
            if args.trusts:
                _log(f"  🎯 --trusts selection: {[t.get('code') or t['name'] for t in trusts]}")
        else:
            project_id, _query_id = create_project_with_query(
                client, headers, project_name, query, dicom_to_nifti=not args.no_dicom_to_nifti
            )
            trusts = stage_and_approve(client, headers, project_id, args.trusts)
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
        # in which case skipping the wait here would have wait_for_model_advanced
        # sit blocked on the (still pulling) FL clients until it times out.
        wait_for_image_pull(
            client,
            headers,
            project_id,
            args.image_pull_threshold,
            args.image_pull_timeout,
            required_trust_names={t["name"] for t in trusts} if args.trusts else None,
        )
        if args.data_enrichment_cmd:
            run_data_enrichment(args.data_enrichment_cwd, args.data_enrichment_cmd, project_id)
        initiate_training(client, headers, model_id, trusts)
        wait_for_model_advanced(client, headers, model_id, args.training_start_timeout)
        if args.abort_midway:
            # #490: exercise the FL "stop training" path instead of running to completion.
            running_status = wait_for_model_running(
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
