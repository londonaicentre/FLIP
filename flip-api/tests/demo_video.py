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
"""Records the end-to-end FLIP demo video against the live dev stack.

Plays the six on-camera segments (tests/demo_segments.py) in one Playwright
browser, each recorded to its own mp4 (tests/demo_recorder.py), and performs
the slow platform waits (cohort responses, imaging import, FL training)
OFF-camera between them, reusing tests/e2e_smoke.py's wait functions. Finally
the segment mp4s are concatenated into one walkthrough video by
flip-ui/scripts/assemble-demo-video.sh.

The recording is genuine end to end: real sign-ins through the UI (Keycloak in dev),
real trust round-trips, real presigned uploads to the object store, real federated training.

Prerequisites:
    - Full dev stack up (`make up`) with trusts registered and Orthanc seeded.
    - Playwright's Chromium (`uv run playwright install chromium`) and ffmpeg on PATH.
    - Demo users (optional but recommended): `make demo-users`, then
      restart flip-api so seeding grants their roles. Without them the
      segments fall back to the well-known admin for both parts.

Usage (preferred):
    make demo-video                                   # from the repo root
    make demo-video DEMO_ARGS="--skip-xnat"
    make demo-video DEMO_ARGS="--project-id <uuid> --from-segment 4"
    make demo-video DEMO_ARGS="--app ehr --project-id <uuid> --from-segment 4"   # resume = same --app

Direct invocation:
    cd flip-api && env DB_HOST=localhost uv run python -m tests.demo_video
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Collection
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import Browser, sync_playwright

from flip_api.auth.identity.keycloak import password_grant
from flip_api.config import get_settings
from flip_api.utils import constants
from tests import demo_segments, e2e_smoke, seed_demo_projects, xnat_seg_upload
from tests.demo_recorder import take
from tests.demo_segments import Persona
from tests.e2e_smoke import SmokeFailure, _get, _log, describe_tutorial

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSEMBLE_SCRIPT = REPO_ROOT / "flip-ui" / "scripts" / "assemble-demo-video.sh"
# Gitignored. Segment mp4s are kept between runs so a resume can assemble the earlier ones.
DEMO_DIR = REPO_ROOT / "flip-api" / "demo-video"
STATE_FILE = DEMO_DIR / "state.json"
SEGMENTS_DIR = DEMO_DIR / "segments"
OUT_DIR = DEMO_DIR / "out"

# Per-app recording profiles: on-camera names plus the per-backend tutorial locations (mirroring
# the e2e_smoke Makefile defaults). CLI name flags override the profile values.
#
# The project *description* is deliberately not here: it is the clinical task from e2e_smoke's
# TUTORIALS, resolved from the app directory below. Both the recorder and the smoke write it to the
# same ProjectDetails.description field and it is read in the same projects list, so a second copy
# here only bought two registers of prose for one study and a description no length guard covered.
# The on-camera names stay local — they are a presentation choice, not a description of the study.
APPS: dict[str, dict[str, Any]] = {
    "xray": {
        "project_name": "Federated Chest X-ray Classification",
        "model_name": "Chest X-ray CNN",
        "model_description": "CNN classifier trained with federated averaging across the participating trusts.",
        "backends": {
            "nvflare": {
                "app_dir": "fl-tutorials/nvflare/image_classification/xray_classification/app_files",
                "query_file": "fl-tutorials/nvflare/image_classification/xray_classification/query.sql",
                "label": "NVFLARE",
            },
            "flower": {
                "app_dir": "fl-tutorials/flower/xray_classification/app",
                "query_file": "fl-tutorials/flower/xray_classification/query.sql",
                "label": "Flower",
            },
        },
    },
    "ehr": {
        "project_name": "Federated Type-2 Diabetes Risk Prediction",
        "model_name": "T2DM Risk MLP",
        "model_description": (
            "Multi-layer perceptron trained with federated averaging on each trust's own OMOP "
            "records — tabular only, no imaging leaves any hospital."
        ),
        # FLIP's tabular/EHR path: the cohort arrives through flip.get_dataframe as arbitrary SQL
        # over each trust's OMOP CDM, and no DICOM is touched at all. The project must therefore be
        # created with "Includes imaging data" off (FLIP#1071's has_imaging). Left on -- which is the
        # form's default -- the hub dispatches CREATE_IMAGING, each trust queues pulls that can never
        # succeed, and the run dies at the image-pull wait or the XNAT segment.
        "has_imaging": False,
        "backends": {
            "nvflare": {
                "app_dir": "fl-tutorials/nvflare/tabular_classification/ehr_risk_prediction/app_files",
                "query_file": "fl-tutorials/nvflare/tabular_classification/ehr_risk_prediction/query.sql",
                "label": "NVFLARE",
            },
            "flower": {
                "app_dir": "fl-tutorials/flower/ehr_risk_prediction/app",
                "query_file": "fl-tutorials/flower/ehr_risk_prediction/query.sql",
                "label": "Flower",
            },
        },
    },
    "spleen": {
        "project_name": "Federated 3D Spleen Segmentation",
        "model_name": "Spleen 3D U-Net",
        "model_description": "3D U-Net trained with federated averaging on trust-held abdominal CT volumes.",
        "backends": {
            "nvflare": {
                "app_dir": "fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/app_files",
                "query_file": "fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/query.sql",
                "label": "NVFLARE",
            },
            "flower": {
                "app_dir": "fl-tutorials/flower/3d_spleen_segmentation/app",
                "query_file": "fl-tutorials/flower/3d_spleen_segmentation/query.sql",
                "label": "Flower",
            },
        },
    },
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record the end-to-end FLIP demo video")
    parser.add_argument("--app", default="xray", choices=sorted(APPS), help="Which tutorial app the demo records")
    parser.add_argument(
        "--fl-backend",
        default=os.environ.get("FL_BACKEND", "nvflare"),
        choices=sorted(APPS["xray"]["backends"]),
    )
    parser.add_argument("--trusts", default=None, help="Comma-separated trust codes/names (default: all registered)")
    parser.add_argument("--project-name", default=None, help="Override the app profile's project name")
    parser.add_argument(
        "--project-description", default=None, help="Override the tutorial's task description (e2e_smoke's TUTORIALS)"
    )
    parser.add_argument("--model-name", default=None, help="Override the app profile's model name")
    parser.add_argument("--model-description", default=None, help="Override the app profile's model description")
    parser.add_argument(
        "--data-enrichment-cwd",
        default=None,
        help="Directory to run --data-enrichment-cmd in (e.g. fl-tutorials/datasets/spleen)",
    )
    parser.add_argument(
        "--data-enrichment-cmd",
        default=None,
        help=(
            "Shell command run OFF-camera between the imaging import and the model segments, with "
            "FLIP_PROJECT_ID exported — e.g. fl-tutorials/datasets/spleen/upload_spleen_labels_to_xnat.py "
            "(labels must be in place before training)"
        ),
    )
    parser.add_argument("--project-id", default=None, help="Reuse an existing project (skips segment 1 context)")
    parser.add_argument("--model-id", default=None, help="Reuse an existing model (with --from-segment 5/6)")
    parser.add_argument(
        "--from-segment",
        type=int,
        default=1,
        choices=range(1, 7),
        help="First segment to record; earlier segments must already have their mp4s for assembly",
    )
    parser.add_argument("--skip-xnat", action="store_true", help="Skip the XNAT/OHIF segment")
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help=(
            "On a fresh run, keep the demo researcher's workspace as it is, instead of deleting its earlier projects "
            "and seeding the demo catalogue owned by it"
        ),
    )
    parser.add_argument(
        "--keep-project",
        action="append",
        default=[],
        metavar="ID",
        help="On a fresh run, spare this project of the researcher's from the workspace reset (repeatable)",
    )
    parser.add_argument("--ui-url", default="http://localhost:44357", help="The dev flip-ui the segments sign in to")
    parser.add_argument(
        "--xnat-url",
        default="http://127.0.0.1:8105",
        help=(
            "Trust XNAT base URL for segment 3. Keep the IPv4 literal: the XNAT ports are published by "
            "Docker Swarm ingress, which accepts but never answers ::1 connections — python-requests "
            "resolves localhost to ::1 and read-times-out (browsers fall back to IPv4 on their own)."
        ),
    )
    parser.add_argument(
        "--publish-segmentations",
        action="store_true",
        help=(
            "After data enrichment, convert each NIfTI label already in XNAT to DICOM-SEG and upload it as an "
            "ROI collection (tests.xnat_seg_upload), so segment 3 can show the segmentation overlaid in OHIF"
        ),
    )
    parser.add_argument(
        "--seg-segment-label", default="Spleen", help="Anatomical label encoded in the published DICOM-SEG"
    )
    parser.add_argument(
        "--seg-limit",
        type=int,
        default=4,
        help=(
            "Sessions per trust to publish a DICOM-SEG for (0 = all). The demo opens one session, and each "
            "conversion downloads a full series, so the default keeps the off-camera wait short"
        ),
    )
    parser.add_argument(
        "--seg-collection-name",
        default="Spleen segmentation",
        help=(
            "Name the OHIF Masks > Import dialog lists the DICOM-SEG collection under — must match the "
            "--collection-name given to tests.xnat_seg_upload during data enrichment"
        ),
    )
    parser.add_argument("--xnat-username", default=os.environ.get("XNAT_ADMIN_USER", "admin"))
    parser.add_argument("--xnat-password", default=os.environ.get("XNAT_ADMIN_PASSWORD", "admin"))
    parser.add_argument("--cohort-timeout", type=int, default=300)
    parser.add_argument("--image-pull-threshold", type=float, default=0.9)
    parser.add_argument("--image-pull-timeout", type=int, default=2400)
    parser.add_argument("--training-start-timeout", type=int, default=900)
    parser.add_argument("--metrics-timeout", type=int, default=1500)
    parser.add_argument("--training-finish-timeout", type=int, default=5400)
    parser.add_argument(
        "--out",
        default=None,
        help="Final assembled mp4 path (default: <demo>/out/flip-demo-<app>.mp4, so apps never overwrite each other)",
    )
    parser.add_argument("--no-assemble", action="store_true", help="Record segments but skip the ffmpeg assembly")
    parser.add_argument(
        "--video-scale",
        type=int,
        default=3,
        help=(
            "Browser device-scale factor for the recording: 3 records the 1280x800 viewport at 3840x2400 "
            "(4K-class). Use 1 for fast draft runs. All segments in one assembly must share the same scale."
        ),
    )
    return parser.parse_args(argv)


def resolve_ui_credentials() -> tuple[tuple[str, str], tuple[str, str], list[str]]:
    """Resolve the (researcher, admin) UI login credentials for the segments.

    Prefers the dedicated demo users (DEMO_RESEARCHER_* / DEMO_ADMIN_* env
    vars, provisioned by `make demo-users`); falls back to the well-known
    admin so the video can still be recorded on a stack without demo users —
    the same person then plays both parts. Roles that fell back are returned
    so the segments can disclose it on camera (the CLI warning alone is
    invisible in the assembled mp4).

    Returns:
        tuple[tuple[str, str], tuple[str, str], list[str]]: ((researcher_email,
        researcher_password), (admin_email, admin_password), roles that fell
        back to the well-known admin — subset of ["researcher", "admin"]).
    """
    admin_password = os.environ.get("ADMIN_USER_PASSWORD", "")
    fallback = (constants.ADMIN_EMAIL_1, admin_password)
    fallback_roles: list[str] = []

    researcher = (os.environ.get("DEMO_RESEARCHER_EMAIL", ""), os.environ.get("DEMO_RESEARCHER_PASSWORD", ""))
    if not (researcher[0] and researcher[1]):
        _log("⚠️  DEMO_RESEARCHER_EMAIL/PASSWORD not set — falling back to the admin user for researcher parts")
        researcher = fallback
        fallback_roles.append("researcher")

    admin = (os.environ.get("DEMO_ADMIN_EMAIL", ""), os.environ.get("DEMO_ADMIN_PASSWORD", ""))
    if not (admin[0] and admin[1]):
        _log("⚠️  DEMO_ADMIN_EMAIL/PASSWORD not set — falling back to the well-known admin user")
        admin = fallback
        fallback_roles.append("admin")

    if not (researcher[1] and admin[1]):
        raise SmokeFailure(
            "No usable UI credentials: set DEMO_RESEARCHER_*/DEMO_ADMIN_* or ADMIN_USER_PASSWORD in the environment"
        )
    return researcher, admin, fallback_roles


def read_state() -> dict[str, str]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def write_state(state: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=4))


def segment_video(number: int, name: str) -> Path:
    return SEGMENTS_DIR / f"{number:02d}-{name}.mp4"


def _pick_experiment(xnat: requests.Session, xnat_url: str, experiments: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the session to open in OHIF, preferring one with a segmentation to show.

    Data enrichment only reaches the sessions it has labels for, so the first session of
    a project is not necessarily one carrying a DICOM-SEG collection — opening that one
    would film the viewer with nothing to overlay. Scan for a session that has an ROI
    collection and take it; fall back to the first session when none do (the classification
    apps, where there is nothing to import).

    Args:
        xnat (requests.Session): Authenticated XNAT session.
        xnat_url (str): Base URL of the trust's XNAT.
        experiments (list[dict[str, Any]]): Experiment rows from the project listing.

    Returns:
        dict[str, Any]: The chosen row, flagged with ``_has_roi_collection``.
    """
    for experiment in experiments:
        resp = xnat.get(f"{xnat_url}/data/experiments/{experiment['ID']}/assessors?format=json", timeout=60)
        if resp.status_code >= 300:
            continue
        if any(row.get("xsiType") == "icr:roiCollectionData" for row in resp.json()["ResultSet"]["Result"]):
            experiment["_has_roi_collection"] = True
            _log(f"  🎨 session {experiment['ID']} carries a segmentation collection")
            return experiment

    return experiments[0]


CONTAINER_DONE_STATUSES = ("Complete", "Failed", "Killed")


def clear_xnat_sessions(xnat: requests.Session, xnat_url: str, username: str) -> None:
    """Sweep the user's XNAT sessions, this one included, just before the take.

    Piles of stale JSESSIONs make XNAT show a sessions-per-IP attention banner and stale-expiry
    dialogs in the recording. Swept last, so no poll of ours re-opens one before the browser signs in.
    """
    try:
        xnat.delete(f"{xnat_url}/xapi/users/active/{username}", timeout=30)
        _log("  🧹 cleared existing XNAT sessions for the demo user")
    except requests.exceptions.RequestException:
        _log("  ⚠️  could not clear XNAT sessions; continuing")


def wait_for_xnat_conversions(
    xnat: requests.Session,
    xnat_url: str,
    xnat_project: str,
    timeout_s: int = 900,
    stall_s: int = 90,
    poll_s: int = 10,
) -> None:
    """Block until the project's container jobs (dcm2niix) have drained, so XNAT is responsive on camera.

    The pull threshold is met while XNAT is still converting hundreds of fresh studies, and a page
    opened then takes ~40 s to load. Returns early, with a warning, when the active count stops
    moving for ``stall_s`` (a finalize stall never drains) or at ``timeout_s``; the take goes on.

    Args:
        xnat (requests.Session): Authenticated XNAT session.
        xnat_url (str): Base URL of the trust's XNAT.
        xnat_project (str): XNAT project id whose containers to wait on.
        timeout_s (int): Longest wait in seconds.
        stall_s (int): How long an unchanged active count counts as stalled.
        poll_s (int): Seconds between polls.
    """
    _log(f"⏳ Waiting for XNAT container jobs in {xnat_project} to drain (timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    last_active, last_change = None, time.monotonic()
    while time.monotonic() < deadline:
        try:
            resp = xnat.get(f"{xnat_url}/xapi/projects/{xnat_project}/containers", timeout=60)
            resp.raise_for_status()
            active = sum(1 for c in resp.json() if not str(c.get("status", "")).startswith(CONTAINER_DONE_STATUSES))
        except (requests.exceptions.RequestException, ValueError) as exc:
            _log(f"  ⚠️  container query error ({type(exc).__name__}); retrying")
        else:
            if active == 0:
                _log("  ✅ no container jobs running")
                return
            if active != last_active:
                _log(f"  📊 {active} container job(s) still running")
                last_active, last_change = active, time.monotonic()
            elif time.monotonic() - last_change >= stall_s:
                _log(f"  ⚠️  {active} container job(s) unchanged for {stall_s}s (stalled?); recording anyway")
                return
        time.sleep(poll_s)
    _log(f"  ⚠️  container jobs still running after {timeout_s}s; recording anyway")


def resolve_xnat_ids(
    xnat_url: str, username: str, password: str, flip_project_id: str, timeout_s: int = 600
) -> dict[str, str]:
    """Find the trust-XNAT project mirroring a FLIP project, plus one session to open in OHIF.

    The imaging import creates one XNAT project per FLIP project with
    ``secondary_ID`` set to the FLIP project UUID
    (trust/imaging-api services/projects.py), so match on that. Once found, waits for the
    project's conversions to drain, then clears the user's XNAT sessions for the take.

    Args:
        xnat_url (str): Base URL of the trust's XNAT (e.g. http://localhost:8105).
        username (str): XNAT login.
        password (str): XNAT password.
        flip_project_id (str): FLIP project UUID to match against secondary_ID.
        timeout_s (int): How long to keep polling for the project/experiments.

    Returns:
        dict[str, str]: project and experiment ids, and ``has_segmentation`` ("yes" or "").
    """
    _log(f"🔎 Resolving XNAT project for FLIP project {flip_project_id} at {xnat_url}")
    deadline = time.monotonic() + timeout_s
    # One pooled session: right after a big import XNAT answers slowly, and
    # per-request basic auth would also mint a fresh JSESSION every poll.
    xnat = requests.Session()
    xnat.auth = (username, password)
    while time.monotonic() < deadline:
        try:
            resp = xnat.get(f"{xnat_url}/data/projects?format=json", timeout=60)
            if resp.status_code < 300:
                rows = resp.json()["ResultSet"]["Result"]
                match = next((r for r in rows if r.get("secondary_ID") == flip_project_id), None)
                if match:
                    xnat_project_id = match["ID"]
                    exp_resp = xnat.get(
                        f"{xnat_url}/data/projects/{xnat_project_id}/experiments?format=json",
                        timeout=60,
                    )
                    if exp_resp.status_code < 300:
                        experiments = exp_resp.json()["ResultSet"]["Result"]
                        if experiments:
                            experiment = _pick_experiment(xnat, xnat_url, experiments)
                            resolved = {
                                "project": xnat_project_id,
                                "experiment": experiment["ID"],
                                "has_segmentation": "yes" if experiment.get("_has_roi_collection") else "",
                            }
                            _log(f"  ✅ XNAT ids: {resolved}")
                            wait_for_xnat_conversions(xnat, xnat_url, xnat_project_id)
                            clear_xnat_sessions(xnat, xnat_url, username)
                            return resolved
        except requests.exceptions.RequestException as exc:
            _log(f"  ⚠️  XNAT query error ({type(exc).__name__}); retrying")
        time.sleep(10)
    raise SmokeFailure(
        f"Could not resolve an XNAT project with secondary_ID={flip_project_id} (and ≥1 experiment) "
        f"at {xnat_url} within {timeout_s}s"
    )


def wait_for_first_metrics(client: requests.Session, headers: dict[str, str], model_id: str, timeout_s: int) -> None:
    """Block until the model has at least one training metric point.

    Segment 5's charts are only worth recording once real data exists. Not
    fatal on timeout — the dashboard still shows the live timeline — so this
    degrades to a loud warning.
    """
    _log(f"⏳ Waiting for first training metrics (timeout {timeout_s}s)")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = e2e_smoke._try_request(_get, client, f"/model/{model_id}/metrics", headers)
        if resp is not None and resp.status_code < 300:
            try:
                charts = resp.json() or []
            except ValueError:
                charts = []
            points = sum(len(series.get("data") or []) for chart in charts for series in chart.get("metrics") or [])
            if points > 0:
                _log(f"  ✅ metrics flowing ({points} point(s))")
                return
        time.sleep(10)
    _log("⚠️  No metrics appeared in time — recording segment 5 anyway (charts may show the empty state)")


def app_files_for(app_dir: Path) -> list[str]:
    """List the uploadable app files, mirroring the UI's blacklist filter."""
    blacklist = e2e_smoke._blacklisted_filenames()
    names = sorted(
        p.name
        for p in app_dir.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix != ".pyc" and p.name not in blacklist
    )
    if not names:
        raise SmokeFailure(f"No uploadable app files found in {app_dir}")
    return names


def sign_in_headers(persona: Persona) -> dict[str, str]:
    """Hub auth headers for ``persona``, through the dev Keycloak's password grant (as the UI signs in).

    Raises:
        SmokeFailure: When the hub's identity provider is not Keycloak.
    """
    settings = get_settings()
    if settings.AUTH_BACKEND != "keycloak" or not settings.KEYCLOAK_PUBLIC_URL:
        raise SmokeFailure(f"Signing in as {persona.email} needs the dev Keycloak (AUTH_BACKEND=keycloak)")
    realm, client_id = settings.KEYCLOAK_REALM, settings.KEYCLOAK_CLIENT_ID
    tokens = password_grant(settings.KEYCLOAK_PUBLIC_URL, realm, client_id, persona.email, persona.password)
    return {"scheme": "Bearer", "authorization": "Bearer " + tokens["access_token"]}


def reset_researcher_workspace(
    client: requests.Session,
    headers: dict[str, str],
    researcher: Persona,
    trusts: list[dict[str, Any]],
    keep: Collection[str] = (),
) -> int:
    """Put the demo researcher's workspace in the same state for every take: the catalogue and nothing else.

    The researcher's project list is the opening shot. The demo researcher exists only for the recorder,
    so every project it owns outside the demo catalogue is left over from an earlier take and is deleted,
    matched on its own user id so other people's projects, including ones shared with it, stay out of reach.
    The catalogue (tests.seed_demo_projects) is then seeded as the researcher, signed in with its own login,
    so the opening shot shows projects it created itself; it is kept across resets, as re-seeding the
    approved entries restarts their imaging imports. When the researcher fell back to the well-known admin
    there is no dedicated account, so nothing is touched.

    Args:
        client (requests.Session): HTTP session for hub calls.
        headers (dict[str, str]): Admin auth headers.
        researcher (Persona): The researcher the segments sign in as.
        trusts (list[dict[str, Any]]): Trusts the catalogue is staged/approved at.
        keep (Collection[str]): Ids of the researcher's projects to spare (``--keep-project``), e.g. one
            another tool still points at.

    Returns:
        int: How many projects were deleted.

    Raises:
        SmokeFailure: When the demo researcher is not a hub user, or a hub call fails.
    """
    if researcher.fallback:
        _log("⚠️  No demo researcher: keeping the workspace (the fallback admin's projects are not the recorder's)")
        return 0
    user_id = seed_demo_projects.find_user_id(client, headers, researcher.email)
    # The owner filter narrows the listing; the id check is what guarantees nobody else's project goes.
    listed = seed_demo_projects.paged(client, headers, f"/projects?owner={user_id}", "list the researcher's projects")
    catalogue = {entry["name"] for entry in seed_demo_projects.CATALOGUE}
    owned = [
        p
        for p in listed
        if str(p.get("ownerId")) == str(user_id) and p.get("name") not in catalogue and str(p["id"]) not in keep
    ]
    for project in owned:
        resp = client.delete(f"{constants.BASE_URL}/projects/{project['id']}", headers=headers, timeout=60)
        e2e_smoke._ensure_ok(resp, f"delete project {project['id']}")
    _log(f"🧹 Workspace reset: deleted {len(owned)} earlier project(s) owned by {researcher.email}")
    owner = seed_demo_projects.Owner(user_id, sign_in_headers(researcher))
    seed_demo_projects.seed_catalogue(client, headers, trusts, seed_demo_projects.CATALOGUE, owner)
    return len(owned)


def check_reused_project_matches_profile(
    client: requests.Session, headers: dict[str, str], project_id: str, app: str, profile: dict[str, Any]
) -> None:
    """Refuse to resume a project whose creation-time ``has_imaging`` contradicts the recorded app.

    Everything after segment 1 — the image-pull wait, the XNAT segment, which app and query are
    uploaded — is decided by the ``--app`` profile, and ``--app`` defaults to ``xray``. A fresh run
    creates the project from the same profile, so the two cannot disagree; on reuse (``--project-id``,
    or ``--from-segment`` over a ``state.json`` project) they can, and the failure is late and opaque:
    an EHR project resumed without ``--app ehr`` waits on
    an imaging import that never starts, and the inverse skips imaging and uploads the wrong app. The
    project's own flag is authoritative (``e2e_smoke.project_has_imaging`` — the smoke reads it back
    for the same reason), so fail here, naming the profile to pass, rather than derive only the
    imaging decisions from it and still ship the other profile's app.

    Args:
        client (requests.Session): HTTP session for hub calls.
        headers (dict[str, str]): Auth headers.
        project_id (str): The reused project.
        app (str): The ``--app`` value in force.
        profile (dict[str, Any]): Its ``APPS`` profile.

    Raises:
        SmokeFailure: When the project's ``has_imaging`` differs from the profile's.
    """
    project_imaging = e2e_smoke.project_has_imaging(client, headers, project_id)
    profile_imaging = bool(profile.get("has_imaging", True))
    if project_imaging != profile_imaging:
        matching = sorted(name for name, p in APPS.items() if bool(p.get("has_imaging", True)) == project_imaging)
        raise SmokeFailure(
            f"project {project_id} was created with has_imaging={str(project_imaging).lower()}, but --app {app} "
            f"records a {'imaging' if profile_imaging else 'tabular-only'} study — pass --app "
            f"{' / '.join(matching)} (the app that created it) to resume it"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    profile = APPS[args.app]
    # Imaging unless the app says otherwise: only the tabular tutorials opt out.
    has_imaging = bool(profile.get("has_imaging", True))
    tutorial = profile["backends"][args.fl_backend]
    if bool(args.data_enrichment_cwd) != bool(args.data_enrichment_cmd):
        raise SmokeFailure("--data-enrichment-cwd and --data-enrichment-cmd must be provided together")
    if args.data_enrichment_cwd and not Path(args.data_enrichment_cwd).is_dir():
        raise SmokeFailure(f"--data-enrichment-cwd does not exist: {args.data_enrichment_cwd}")
    app_dir = REPO_ROOT / tutorial["app_dir"]
    query_file = REPO_ROOT / tutorial["query_file"]
    for required in (app_dir, query_file):
        if not required.exists():
            raise SmokeFailure(f"Tutorial path missing: {required}")

    project_name = args.project_name or profile["project_name"]
    # One description of this study, shared with the smoke and guarded against the 250-char cap there.
    project_description = args.project_description or describe_tutorial(app_dir).task
    model_name = args.model_name or profile["model_name"]
    model_description = args.model_description or profile["model_description"]

    researcher_login, admin_login, fallback_roles = resolve_ui_credentials()
    researcher = Persona(*researcher_login, role="researcher", fallback="researcher" in fallback_roles)
    admin = Persona(*admin_login, role="admin", fallback="admin" in fallback_roles)
    _log(f"🎭 Researcher part: {researcher.email} | Admin part: {admin.email}")

    # Hub session for the off-camera waits (same auth as e2e_smoke).
    client = requests.Session()
    client.headers.update({"Content-Type": "application/json"})
    headers = e2e_smoke.authenticate()

    trusts = e2e_smoke._ensure_ok(_get(client, "/trust/", headers), "list trusts").json()
    trusts = e2e_smoke.select_trusts(trusts, args.trusts)
    if not trusts:
        raise SmokeFailure("No trusts registered on the hub")
    _log(f"🏥 Demo trusts: {[t.get('code') or t['name'] for t in trusts]}")

    # A fresh start wipes the previous segment videos; a resume keeps them for assembly.
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.from_segment == 1:
        for old in SEGMENTS_DIR.glob("*.mp4"):
            old.unlink()
        STATE_FILE.unlink(missing_ok=True)
        state: dict[str, str] = {}
        if not args.keep_workspace:
            reset_researcher_workspace(client, headers, researcher, trusts, keep=args.keep_project)
    else:
        state = read_state()
        if args.project_id:
            state["projectId"] = args.project_id
        if args.model_id:
            state["modelId"] = args.model_id
        if not state.get("projectId"):
            raise SmokeFailure("--from-segment > 1 needs --project-id (or an existing state.json)")
        write_state(state)

    with sync_playwright() as playwright:
        # The real flag, not only the context's device_scale_factor: without it the screencast
        # hands out CSS-pixel frames and the 4K video is an upscale.
        browser = playwright.chromium.launch(args=[f"--force-device-scale-factor={args.video_scale}"])
        try:
            record_segments(
                browser,
                args,
                client,
                headers,
                trusts,
                state,
                researcher=researcher,
                admin=admin,
                has_imaging=has_imaging,
                project_name=project_name,
                project_description=project_description,
                query_sql=query_file.read_text(),
                model_name=model_name,
                model_description=model_description,
                app_files=[app_dir / name for name in app_files_for(app_dir)],
                backend_label=tutorial["label"],
            )
        finally:
            browser.close()

    if args.no_assemble:
        _log(f"⏭️  --no-assemble: segment mp4s left in {SEGMENTS_DIR}")
        return 0

    out_path = Path(args.out).resolve() if args.out else (OUT_DIR / f"flip-demo-{args.app}.mp4").resolve()
    _log("🎞️  Assembling final video …")
    result = subprocess.run(["bash", str(ASSEMBLE_SCRIPT), str(SEGMENTS_DIR), str(out_path)])
    if result.returncode != 0:
        raise SmokeFailure(f"Video assembly failed — segment mp4s are intact in {SEGMENTS_DIR}")
    _log(f"🎉 Demo video ready: {out_path}")
    return 0


def record_segments(
    browser: Browser,
    args: argparse.Namespace,
    client: requests.Session,
    headers: dict[str, str],
    trusts: list[dict[str, Any]],
    state: dict[str, str],
    *,
    researcher: Persona,
    admin: Persona,
    has_imaging: bool,
    project_name: str,
    project_description: str,
    query_sql: str,
    model_name: str,
    model_description: str,
    app_files: list[Path],
    backend_label: str,
) -> None:
    """Record the segments from ``--from-segment`` on, with the platform waits between them off camera."""
    scale = args.video_scale

    # ── Segment 1: researcher creates project + cohort query ──────────────
    if args.from_segment <= 1:
        with take(browser, segment_video(1, "create-project"), args.ui_url, scale) as rec:
            state["projectId"] = demo_segments.create_project(
                rec, researcher, project_name, project_description, query_sql, has_imaging
            )
        write_state(state)
    project_id = state["projectId"]
    _log(f"🆔 project_id={project_id}")
    if args.project_id or args.from_segment > 1:  # a project this run did not create from the profile
        check_reused_project_matches_profile(client, headers, project_id, args.app, APPS[args.app])

    required_trust_ids = {str(t["id"]) for t in trusts}
    required_trust_names = {t["name"] for t in trusts}
    e2e_smoke.wait_for_trusts_responded(
        client, headers, project_id, timeout_s=args.cohort_timeout, required_trust_ids=required_trust_ids
    )

    # ── Segment 2: admin stages + approves ────────────────────────────────
    if args.from_segment <= 2:
        with take(browser, segment_video(2, "approve"), args.ui_url, scale) as rec:
            demo_segments.approve(rec, admin, project_id, has_imaging)

    # ── Off-camera: the imaging import (the ~6 min wait) ──────────────────
    # A non-imaging study dispatches no CREATE_IMAGING, so no import tasks are ever created and the
    # wait would loop to its timeout rather than return.
    if has_imaging:
        e2e_smoke.wait_for_image_pull(
            client,
            headers,
            project_id,
            threshold=args.image_pull_threshold,
            timeout_s=args.image_pull_timeout,
            required_trust_names=required_trust_names,
        )
    else:
        _log("⏭️  No imaging for this study — skipping the image-pull wait")

    # ── Off-camera: optional data enrichment (e.g. spleen labels) ────────
    if args.data_enrichment_cwd and args.data_enrichment_cmd:
        e2e_smoke.run_data_enrichment(Path(args.data_enrichment_cwd), args.data_enrichment_cmd, project_id)

    # Second half of enrichment for a segmentation app: the NIfTI labels the app
    # trains on are invisible to a viewer, so republish them as DICOM-SEG for the
    # OHIF beat. Runs after the labels are in place, and only when asked for.
    if args.publish_segmentations:
        if xnat_seg_upload.main(
            [
                "--flip-project-id",
                project_id,
                "--segment-label",
                args.seg_segment_label,
                "--collection-name",
                args.seg_collection_name,
                "--limit",
                str(args.seg_limit),
            ]
        ):
            raise SmokeFailure("publishing DICOM-SEG collections failed — see the log above")

    # ── Segment 3: XNAT + OHIF at one trust ───────────────────────────────
    # A non-imaging study has no XNAT project and never will, so the skip follows the app.
    if not args.skip_xnat and has_imaging and args.from_segment <= 3:
        xnat_ids = resolve_xnat_ids(args.xnat_url, args.xnat_username, args.xnat_password, project_id)
        with take(browser, segment_video(3, "xnat-ohif"), args.xnat_url, scale) as rec:
            demo_segments.xnat_ohif(
                rec,
                args.xnat_username,
                args.xnat_password,
                xnat_ids["project"],
                xnat_ids["experiment"],
                # Only when the session really has a collection, so classification apps skip the mask beat.
                args.seg_collection_name if xnat_ids["has_segmentation"] else None,
            )

    # ── Segment 4: model + app upload + initiate training ────────────────
    if args.from_segment <= 4:
        with take(browser, segment_video(4, "create-model-train"), args.ui_url, scale) as rec:
            state["modelId"] = demo_segments.create_model_and_train(
                rec, researcher, project_id, has_imaging, model_name, model_description, app_files, backend_label
            )
        write_state(state)
    model_id = state.get("modelId", "")
    if not model_id:
        raise SmokeFailure("--from-segment > 4 needs --model-id (or a state.json from segment 4)")
    _log(f"🆔 model_id={model_id}")

    # ── Off-camera: training spins up and produces its first metrics ─────
    e2e_smoke.wait_for_model_advanced(client, headers, model_id, timeout_s=args.training_start_timeout)
    wait_for_first_metrics(client, headers, model_id, timeout_s=args.metrics_timeout)

    # ── Segment 5: live progress ──────────────────────────────────────────
    if args.from_segment <= 5:
        with take(browser, segment_video(5, "follow-progress"), args.ui_url, scale) as rec:
            demo_segments.follow_progress(rec, researcher, project_id, model_id)

    # ── Off-camera: training runs to completion + results upload ─────────
    e2e_smoke.wait_for_training_finished(client, headers, model_id, timeout_s=args.training_finish_timeout)

    # ── Segment 6: download the results ──────────────────────────────────
    with take(browser, segment_video(6, "download-results"), args.ui_url, scale) as rec:
        demo_segments.download_results(rec, researcher, project_id, model_id)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as failure:
        _log(f"❌ {failure}")
        sys.exit(1)
