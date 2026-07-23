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

Drives the six Cypress demo segments (flip-ui/test/cypress/demo/) one at a
time — each in its own Dockerised Cypress run — and performs the slow
platform waits (cohort responses, imaging import, FL training) OFF-camera
between segments, reusing tests/e2e_smoke.py's wait functions. Finally the
segment mp4s are cropped + concatenated into one walkthrough video by
flip-ui/scripts/assemble-demo-video.sh.

The recording is genuine end to end: real Cognito sign-ins through the UI,
real trust round-trips, real S3 presigned uploads, real federated training.

Prerequisites:
    - Full dev stack up (`make up`) with trusts registered and Orthanc seeded.
    - A live AWS SSO session (`aws sso login --sso-session FLIP`) — presigned
      uploads/downloads and the admin token fallback need it.
    - Demo Cognito users (optional but recommended): `make demo-users`, then
      restart flip-api so seeding grants their roles. Without them the
      segments fall back to the well-known admin for both parts.

Usage (preferred):
    make demo-video                                   # from the repo root
    make demo-video DEMO_ARGS="--skip-xnat"
    make demo-video DEMO_ARGS="--project-id <uuid> --from-segment 4"

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
from pathlib import Path
from typing import Any

import requests

from flip_api.utils import constants
from tests import e2e_smoke, xnat_seg_upload
from tests.e2e_smoke import SmokeFailure, _get, _log

REPO_ROOT = Path(__file__).resolve().parents[2]
FLIP_UI_DIR = REPO_ROOT / "flip-ui"
DEMO_DIR = FLIP_UI_DIR / "test" / "cypress" / "demo"
STATE_FILE = DEMO_DIR / "state.json"
VIDEOS_DIR = DEMO_DIR / "videos"
OUT_DIR = DEMO_DIR / "out"
CYPRESS_IMAGE = "cypress/included:14.5.2"

SEGMENTS = [
    "01-create-project",
    "02-approve",
    "03-xnat-ohif",
    "04-create-model-train",
    "05-follow-progress",
    "06-download-results",
]

# Per-app recording profiles: on-camera names plus the per-backend tutorial
# locations (mirroring the e2e_smoke Makefile defaults). CLI name flags
# override the profile values.
APPS: dict[str, dict[str, Any]] = {
    "xray": {
        "project_name": "Federated Chest X-ray Classification",
        "project_description": (
            "Multi-trust federated study: CNN classification of pleural effusion and edema on chest radiographs."
        ),
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
    "spleen": {
        "project_name": "Federated 3D Spleen Segmentation",
        "project_description": (
            "Multi-trust federated study: 3D U-Net segmentation of the spleen on abdominal CT volumes."
        ),
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
    parser.add_argument("--project-description", default=None, help="Override the app profile's project description")
    parser.add_argument("--model-name", default=None, help="Override the app profile's model name")
    parser.add_argument("--model-description", default=None, help="Override the app profile's model description")
    parser.add_argument(
        "--data-enrichment-cwd",
        default=None,
        help="Directory to run --data-enrichment-cmd in (e.g. the spleen label-upload repo checkout)",
    )
    parser.add_argument(
        "--data-enrichment-cmd",
        default=None,
        help=(
            "Shell command run OFF-camera between the imaging import and the model segments, with "
            "FLIP_PROJECT_ID exported — e.g. the spleen tutorial's upload_labels_to_XNAT.py (labels must be "
            "in place before training)"
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
        "--xnat-url",
        default="http://127.0.0.1:8104",
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
            "Browser device-scale factor for the recording: 3 captures the 1280x800 viewport at 3840x2400 "
            "(4K-class). Use 1 for fast draft runs. All segments in one assembly must share the same scale."
        ),
    )
    return parser.parse_args(argv)


def resolve_ui_credentials() -> tuple[tuple[str, str], tuple[str, str]]:
    """Resolve the (researcher, admin) UI login credentials for the segments.

    Prefers the dedicated demo users (DEMO_RESEARCHER_* / DEMO_ADMIN_* env
    vars, provisioned by `make demo-users`); falls back to the well-known
    admin so the video can still be recorded on a stack without demo users —
    the same person then plays both parts.

    Returns:
        tuple[tuple[str, str], tuple[str, str]]: ((researcher_email,
        researcher_password), (admin_email, admin_password)).
    """
    admin_password = os.environ.get("ADMIN_USER_PASSWORD", "")
    fallback = (constants.ADMIN_EMAIL_1, admin_password)

    researcher = (os.environ.get("DEMO_RESEARCHER_EMAIL", ""), os.environ.get("DEMO_RESEARCHER_PASSWORD", ""))
    if not (researcher[0] and researcher[1]):
        _log("⚠️  DEMO_RESEARCHER_EMAIL/PASSWORD not set — falling back to the admin user for researcher parts")
        researcher = fallback

    admin = (os.environ.get("DEMO_ADMIN_EMAIL", ""), os.environ.get("DEMO_ADMIN_PASSWORD", ""))
    if not (admin[0] and admin[1]):
        _log("⚠️  DEMO_ADMIN_EMAIL/PASSWORD not set — falling back to the well-known admin user")
        admin = fallback

    if not (researcher[1] and admin[1]):
        raise SmokeFailure(
            "No usable UI credentials: set DEMO_RESEARCHER_*/DEMO_ADMIN_* or ADMIN_USER_PASSWORD in the environment"
        )
    return researcher, admin


def read_state() -> dict[str, str]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def write_state(state: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=4))


def run_segment(name: str, env: dict[str, str], video_scale: int = 1) -> Path:
    """Record one demo segment in a Dockerised Cypress run.

    Env values are passed with bare `-e NAME` flags so secrets ride the
    subprocess environment instead of the docker argv (visible in `ps`).

    Args:
        name (str): Segment name (spec basename without .spec.ts).
        env (dict[str, str]): Environment values the spec reads via Cypress.env.

    Returns:
        Path: The recorded segment mp4.
    """
    spec_rel = f"test/cypress/demo/{name}.spec.ts"
    video = VIDEOS_DIR / f"{name}.spec.ts.mp4"
    video.unlink(missing_ok=True)
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    child_env = os.environ.copy()
    cmd = [
        "docker", "run", "--rm", "--network", "host", "--shm-size=2g",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{REPO_ROOT}:/e2e", "-w", "/e2e/flip-ui",
    ]
    # Cypress + Chrome need a writable HOME when running as the host user.
    for key, value in {"HOME": "/tmp/cypress-home", "DEMO_VIDEO_SCALE": str(video_scale), **env}.items():
        child_env[key] = value
        cmd += ["-e", key]
    cmd += [
        "--entrypoint", "cypress", CYPRESS_IMAGE,
        "run", "--browser", "chrome", "--config-file", "cypress.demo.config.ts", "--spec", spec_rel,
    ]

    _log(f"🎬 Recording segment {name} …")
    result = subprocess.run(cmd, env=child_env)
    if result.returncode != 0:
        raise SmokeFailure(
            f"Segment {name} failed (exit {result.returncode}). "
            f"See {VIDEOS_DIR / (name + '.spec.ts.mp4')} and {DEMO_DIR / 'screenshots'} for what happened."
        )
    if not video.exists():
        raise SmokeFailure(f"Segment {name} passed but produced no video at {video}")
    _log(f"  ✅ {video.relative_to(REPO_ROOT)}")
    return video


def _pick_experiment(
    xnat: requests.Session, xnat_url: str, experiments: list[dict[str, Any]]
) -> dict[str, Any]:
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


def resolve_xnat_ids(
    xnat_url: str, username: str, password: str, flip_project_id: str, timeout_s: int = 600
) -> dict[str, str]:
    """Find the trust-XNAT project mirroring a FLIP project, plus one session.

    The imaging import creates one XNAT project per FLIP project with
    ``secondary_ID`` set to the FLIP project UUID
    (trust/imaging-api services/projects.py), so match on that and take the
    first experiment as the study to open in OHIF. The OHIF viewer URL needs
    the subject id and experiment label too (the plugin's viewer.js builds
    ``?subjectId=&projectId=&experimentId=&experimentLabel=``).

    Args:
        xnat_url (str): Base URL of the trust's XNAT (e.g. http://localhost:8104).
        username (str): XNAT login.
        password (str): XNAT password.
        flip_project_id (str): FLIP project UUID to match against secondary_ID.
        timeout_s (int): How long to keep polling for the project/experiments.

    Returns:
        dict[str, str]: project, subject, experiment and label ids for the viewer URL.
    """
    _log(f"🔎 Resolving XNAT project for FLIP project {flip_project_id} at {xnat_url}")
    deadline = time.monotonic() + timeout_s
    # One pooled session: right after a big import XNAT answers slowly, and
    # per-request basic auth would also mint a fresh JSESSION every poll.
    xnat = requests.Session()
    xnat.auth = (username, password)
    # Sweep the user's accumulated sessions first — piles of stale JSESSIONs
    # make XNAT show a sessions-per-IP attention banner and stale-expiry
    # dialogs in the recording.
    try:
        xnat.delete(f"{xnat_url}/xapi/users/active/{username}", timeout=30)
        _log("  🧹 cleared existing XNAT sessions for the demo user")
    except requests.exceptions.RequestException:
        _log("  ⚠️  could not clear XNAT sessions; continuing")
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
                            detail = xnat.get(
                                f"{xnat_url}/data/experiments/{experiment['ID']}?format=json",
                                timeout=60,
                            )
                            fields = detail.json()["items"][0]["data_fields"] if detail.status_code < 300 else {}
                            resolved = {
                                "project": xnat_project_id,
                                "subject": str(fields.get("subject_ID", "")),
                                "experiment": experiment["ID"],
                                "label": str(experiment.get("label") or fields.get("label", "")),
                                "has_segmentation": "yes" if experiment.get("_has_roi_collection") else "",
                            }
                            if resolved["subject"]:
                                _log(f"  ✅ XNAT ids: {resolved}")
                                return resolved
        except requests.exceptions.RequestException as exc:
            _log(f"  ⚠️  XNAT query error ({type(exc).__name__}); retrying")
        time.sleep(10)
    raise SmokeFailure(
        f"Could not resolve an XNAT project with secondary_ID={flip_project_id} (and ≥1 experiment) "
        f"at {xnat_url} within {timeout_s}s"
    )


def wait_for_first_metrics(
    client: requests.Session, headers: dict[str, str], model_id: str, timeout_s: int
) -> None:
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    profile = APPS[args.app]
    tutorial = profile["backends"][args.fl_backend]
    project_name = args.project_name or profile["project_name"]
    project_description = args.project_description or profile["project_description"]
    model_name = args.model_name or profile["model_name"]
    model_description = args.model_description or profile["model_description"]
    if bool(args.data_enrichment_cwd) != bool(args.data_enrichment_cmd):
        raise SmokeFailure("--data-enrichment-cwd and --data-enrichment-cmd must be provided together")
    if args.data_enrichment_cwd and not Path(args.data_enrichment_cwd).is_dir():
        raise SmokeFailure(f"--data-enrichment-cwd does not exist: {args.data_enrichment_cwd}")
    app_dir = REPO_ROOT / tutorial["app_dir"]
    query_file = REPO_ROOT / tutorial["query_file"]
    for required in (app_dir, query_file):
        if not required.exists():
            raise SmokeFailure(f"Tutorial path missing: {required}")

    researcher, admin = resolve_ui_credentials()
    _log(f"🎭 Researcher part: {researcher[0]} | Admin part: {admin[0]}")

    # Hub session for the off-camera waits (same auth as e2e_smoke).
    client = requests.Session()
    client.headers.update({"Content-Type": "application/json"})
    headers = e2e_smoke.authenticate()

    trusts = e2e_smoke._ensure_ok(_get(client, "/trust/", headers), "list trusts").json()
    trusts = e2e_smoke.select_trusts(trusts, args.trusts)
    if not trusts:
        raise SmokeFailure("No trusts registered on the hub")
    _log(f"🏥 Demo trusts: {[t.get('code') or t['name'] for t in trusts]}")

    # Fresh start wipes previous segment videos; resumes keep them for assembly.
    if args.from_segment == 1:
        if VIDEOS_DIR.exists():
            for old in VIDEOS_DIR.glob("*.mp4"):
                old.unlink()
        STATE_FILE.unlink(missing_ok=True)
        if args.project_id:
            write_state({"projectId": args.project_id})
    else:
        state = read_state()
        if args.project_id:
            state["projectId"] = args.project_id
        if args.model_id:
            state["modelId"] = args.model_id
        if not state.get("projectId"):
            raise SmokeFailure("--from-segment > 1 needs --project-id (or an existing state.json)")
        write_state(state)

    # Paths handed to the specs are relative to the Cypress project root
    # (flip-ui/) inside the container mount.
    query_rel = os.path.relpath(query_file, FLIP_UI_DIR)
    app_dir_rel = os.path.relpath(app_dir, FLIP_UI_DIR)

    researcher_env = {"DEMO_RESEARCHER_EMAIL": researcher[0], "DEMO_RESEARCHER_PASSWORD": researcher[1]}

    # ── Segment 1: researcher creates project + cohort query ──────────────
    if args.from_segment <= 1:
        run_segment(
            "01-create-project",
            {
                **researcher_env,
                "DEMO_PROJECT_NAME": project_name,
                "DEMO_PROJECT_DESCRIPTION": project_description,
                "DEMO_QUERY_FILE": query_rel,
            },
            video_scale=args.video_scale,
        )
    project_id = read_state().get("projectId", "")
    if not project_id:
        raise SmokeFailure("Segment 1 did not record a projectId in state.json")
    _log(f"🆔 project_id={project_id}")

    required_trust_ids = {str(t["id"]) for t in trusts}
    required_trust_names = {t["name"] for t in trusts}
    e2e_smoke.wait_for_trusts_responded(
        client, headers, project_id, timeout_s=args.cohort_timeout, required_trust_ids=required_trust_ids
    )

    # ── Segment 2: admin checks connection status, stages + approves ──────
    if args.from_segment <= 2:
        run_segment(
            "02-approve",
            {
                "DEMO_ADMIN_EMAIL": admin[0],
                "DEMO_ADMIN_PASSWORD": admin[1],
                "DEMO_PROJECT_ID": project_id,
            },
            video_scale=args.video_scale,
        )

    # ── Off-camera: the imaging import (the ~6 min wait) ──────────────────
    e2e_smoke.wait_for_image_pull(
        client,
        headers,
        project_id,
        threshold=args.image_pull_threshold,
        timeout_s=args.image_pull_timeout,
        required_trust_names=required_trust_names,
    )

    # ── Off-camera: optional data enrichment (e.g. spleen labels) ────────
    if args.data_enrichment_cwd and args.data_enrichment_cmd:
        e2e_smoke.run_data_enrichment(Path(args.data_enrichment_cwd), args.data_enrichment_cmd, project_id)

    # Second half of enrichment for a segmentation app: the NIfTI labels the app
    # trains on are invisible to a viewer, so republish them as DICOM-SEG for the
    # OHIF beat. Runs after the labels are in place, and only when asked for.
    if args.publish_segmentations:
        if xnat_seg_upload.main(
            [
                "--flip-project-id", project_id,
                "--segment-label", args.seg_segment_label,
                "--collection-name", args.seg_collection_name,
                "--limit", str(args.seg_limit),
            ]
        ):
            raise SmokeFailure("publishing DICOM-SEG collections failed — see the log above")

    # ── Segment 3: XNAT + OHIF at one trust ───────────────────────────────
    if not args.skip_xnat and args.from_segment <= 3:
        xnat_ids = resolve_xnat_ids(args.xnat_url, args.xnat_username, args.xnat_password, project_id)
        run_segment(
            "03-xnat-ohif",
            {
                "CYPRESS_BASE_URL": args.xnat_url,
                "DEMO_XNAT_USERNAME": args.xnat_username,
                "DEMO_XNAT_PASSWORD": args.xnat_password,
                "DEMO_XNAT_PROJECT_ID": xnat_ids["project"],
                "DEMO_XNAT_SUBJECT_ID": xnat_ids["subject"],
                "DEMO_XNAT_EXPERIMENT_ID": xnat_ids["experiment"],
                "DEMO_XNAT_EXPERIMENT_LABEL": xnat_ids["label"],
                # Only set when the session really has a collection to import, so the
                # segment skips the mask beat entirely for classification apps.
                **({"DEMO_XNAT_SEG_NAME": args.seg_collection_name} if xnat_ids["has_segmentation"] else {}),
            },
            video_scale=args.video_scale,
        )

    # ── Segment 4: model + app upload + initiate training ────────────────
    if args.from_segment <= 4:
        run_segment(
            "04-create-model-train",
            {
                **researcher_env,
                "DEMO_PROJECT_ID": project_id,
                "DEMO_MODEL_NAME": model_name,
                "DEMO_MODEL_DESCRIPTION": model_description,
                "DEMO_APP_DIR": app_dir_rel,
                "DEMO_APP_FILES": ",".join(app_files_for(app_dir)),
                "DEMO_BACKEND_LABEL": tutorial["label"],
            },
            video_scale=args.video_scale,
        )
    model_id = read_state().get("modelId", "")
    if not model_id:
        raise SmokeFailure("Segment 4 did not record a modelId in state.json")
    _log(f"🆔 model_id={model_id}")

    # ── Off-camera: training spins up and produces its first metrics ─────
    e2e_smoke.wait_for_model_advanced(client, headers, model_id, timeout_s=args.training_start_timeout)
    wait_for_first_metrics(client, headers, model_id, timeout_s=args.metrics_timeout)

    # ── Segment 5: live progress ──────────────────────────────────────────
    ids_env = {**researcher_env, "DEMO_PROJECT_ID": project_id, "DEMO_MODEL_ID": model_id}
    if args.from_segment <= 5:
        run_segment("05-follow-progress", ids_env, video_scale=args.video_scale)

    # ── Off-camera: training runs to completion + results upload ─────────
    e2e_smoke.wait_for_training_finished(client, headers, model_id, timeout_s=args.training_finish_timeout)

    # ── Segment 6: download the results ──────────────────────────────────
    run_segment("06-download-results", ids_env, video_scale=args.video_scale)

    if args.no_assemble:
        _log("⏭️  --no-assemble: segment mp4s left in " + str(VIDEOS_DIR))
        return 0

    out_path = Path(args.out).resolve() if args.out else (OUT_DIR / f"flip-demo-{args.app}.mp4").resolve()
    _log("🎞️  Assembling final video …")
    result = subprocess.run(
        ["bash", "scripts/assemble-demo-video.sh", str(VIDEOS_DIR), str(out_path), str(args.video_scale)],
        cwd=FLIP_UI_DIR,
    )
    if result.returncode != 0:
        raise SmokeFailure("Video assembly failed — segment mp4s are intact in " + str(VIDEOS_DIR))
    _log(f"🎉 Demo video ready: {out_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as failure:
        _log(f"❌ {failure}")
        sys.exit(1)
