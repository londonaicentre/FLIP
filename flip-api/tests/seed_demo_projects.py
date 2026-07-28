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
"""Pre-populate the platform with a curated catalogue of radiology projects.

A fresh FLIP deployment boots with an empty /projects page. This seeds a set
of believable radiology studies — spleen segmentation on CT, chest X-ray
classification, and friends — spread across HONEST lifecycle states, built
through the real API exactly like a user would: cohort queries are genuinely
submitted to the trusts, staging/approval go through the real step
endpoints, and model files are uploaded via the presigned-URL flow. Nothing
is fabricated: no fake metrics, logs, or training results are written, so
every project can continue its real lifecycle from wherever it sits.

States used, in ladder order:
    unstaged        → project only
    unstaged_query  → + cohort query submitted to the trusts
    staged          → + staged at every registered trust
    approved        → + approved (imaging import kicks off for real!)
    approved_model  → + a model with its training app files uploaded

Note an approved entry starts a genuine imaging import at the trusts — on a
dev stack that is exactly the point (the projects are then fully explorable),
but it is real work for Orthanc/XNAT.

Created ids are recorded in tests/seed_demo_projects.json; `--cleanup`
deletes everything listed there.

Usage:
    make seed-demo-projects                     # from the repo root
    make -C flip-api seed_demo_projects
    make -C flip-api seed_demo_projects EXTRA_ARGS="--cleanup"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.utils import constants
from tests import e2e_smoke
from tests.e2e_smoke import SmokeFailure, _ensure_ok, _get, _log, _post

REPO_ROOT = Path(__file__).resolve().parents[2]
RECORD_FILE = Path(__file__).parent / "seed_demo_projects.json"

XRAY_QUERY = REPO_ROOT / "fl-tutorials/nvflare/image_classification/xray_classification/query.sql"
SPLEEN_QUERY = REPO_ROOT / "fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/query.sql"
XRAY_APP_DIR = REPO_ROOT / "fl-tutorials/nvflare/image_classification/xray_classification/app_files"
SPLEEN_APP_DIR = REPO_ROOT / "fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/app_files"

# The curated catalogue. Descriptions stay within the UI's 250-char limit;
# queries are the real tutorial cohort queries so their results are genuine.
CATALOGUE: list[dict[str, Any]] = [
    {
        "name": "3D Spleen Segmentation on CT",
        "description": (
            "Federated 3D U-Net for spleen segmentation on abdominal CT. "
            "Trains across all participating trusts on locally-held scans; labels are added "
            "during data enrichment."
        ),
        "state": "approved_model",
        "query_file": SPLEEN_QUERY,
        "model": {
            "name": "Spleen 3D U-Net",
            "description": "3D U-Net trained with federated averaging on trust-held abdominal CT volumes.",
            "files_dir": SPLEEN_APP_DIR,
        },
    },
    {
        "name": "Chest X-ray Findings Classification",
        "description": (
            "Multi-label CNN classifier for pleural effusion and edema on frontal chest radiographs, "
            "trained federatedly without imaging leaving any trust."
        ),
        "state": "approved_model",
        "query_file": XRAY_QUERY,
        "model": {
            "name": "CXR Findings CNN",
            "description": "CNN classifier for effusion/edema findings, aggregated across trusts per round.",
            "files_dir": XRAY_APP_DIR,
        },
    },
    {
        "name": "Pneumothorax Detection on Chest X-ray",
        "description": (
            "Screening model for pneumothorax on emergency-department chest radiographs. "
            "Cohort under review by the participating trusts."
        ),
        "state": "staged",
        "query_file": XRAY_QUERY,
    },
    {
        "name": "Diffusion Model for Synthetic Chest X-rays",
        "description": (
            "Latent diffusion model for privacy-preserving synthetic chest radiograph generation, "
            "for downstream augmentation studies."
        ),
        "state": "unstaged_query",
        "query_file": XRAY_QUERY,
    },
    {
        "name": "Cardiomegaly Screening from Frontal CXR",
        "description": (
            "Early-stage proposal: automated cardiothoracic-ratio estimation on frontal chest "
            "radiographs. Cohort definition still being drafted."
        ),
        "state": "unstaged",
    },
]

STATE_LADDER = ["unstaged", "unstaged_query", "staged", "approved", "approved_model"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed (or clean up) the demo radiology project catalogue")
    parser.add_argument("--cleanup", action="store_true", help=f"Delete everything recorded in {RECORD_FILE.name}")
    parser.add_argument(
        "--trusts", default=None, help="Comma-separated trust codes/names to stage/approve at (default: all)"
    )
    parser.add_argument(
        "--states",
        default=None,
        help=(
            "Comma-separated subset of target states to seed (default: all). Useful to defer the "
            "approved entries — approval starts a real imaging import at every trust — e.g. "
            "--states unstaged,unstaged_query,staged first, --states approved_model later."
        ),
    )
    return parser.parse_args(argv)


def select_entries(catalogue: list[dict[str, Any]], states_arg: str | None) -> list[dict[str, Any]]:
    """Filter the catalogue down to the --states subset (the full catalogue when unset).

    Args:
        catalogue (list[dict[str, Any]]): The full catalogue of seed entries.
        states_arg (str | None): Comma-separated target states, as passed to --states.

    Returns:
        list[dict[str, Any]]: The entries whose target state was requested.

    Raises:
        SmokeFailure: If a requested state is not on the ladder.
    """
    if not states_arg:
        return catalogue
    wanted = {s.strip() for s in states_arg.split(",") if s.strip()}
    unknown = wanted - set(STATE_LADDER)
    if unknown:
        raise SmokeFailure(f"Unknown --states value(s): {sorted(unknown)}. Known: {STATE_LADDER}")
    entries = [e for e in catalogue if e["state"] in wanted]
    _log(f"🎯 --states selection: seeding {len(entries)} of {len(catalogue)} catalogue entries")
    return entries


def existing_project_names(client: requests.Session, headers: dict[str, str], names: set[str]) -> set[str]:
    """Return the subset of catalogue names that already exist as projects on the hub.

    /projects is paginated and its `search` matches substrings of name AND
    description, so query per name and keep exact name matches only.

    Args:
        client (requests.Session): HTTP session for hub calls.
        headers (dict[str, str]): Auth headers.
        names (set[str]): Candidate catalogue project names.

    Returns:
        set[str]: The names already present on the hub.
    """
    found: set[str] = set()
    for name in sorted(names):
        resp = _ensure_ok(
            _get(client, f"/projects/?search={quote(name)}&pageSize=50", headers), f"look up project {name!r}"
        )
        if any(row.get("name") == name for row in resp.json().get("data", [])):
            found.add(name)
    return found


def submit_query(client: requests.Session, headers: dict[str, str], project_id: str, query_file: Path) -> None:
    query = query_file.read_text()
    save_resp = _ensure_ok(
        _post(
            client,
            "/cohort/save/",
            {"query": query, "name": "Radiology cohort query", "project_id": project_id},
            headers,
        ),
        "save cohort query",
    )
    query_id = save_resp.json()["query_id"]
    _ensure_ok(
        _post(
            client,
            "/cohort/submit/",
            {
                "authenticationToken": headers.get("authorization", ""),
                "query": query,
                "name": "Radiology cohort query",
                "project_id": project_id,
                "query_id": str(query_id),
            },
            headers,
            timeout=60,
        ),
        "submit cohort query",
    )
    _log("    ✅ cohort query submitted to trusts")


def build_project(
    client: requests.Session,
    headers: dict[str, str],
    entry: dict[str, Any],
    trusts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Drive one catalogue entry through the real API up to its target state.

    Args:
        client (requests.Session): HTTP session for hub calls.
        headers (dict[str, str]): Auth headers.
        entry (dict[str, Any]): Catalogue entry (name/description/state/…).
        trusts (list[dict[str, Any]]): Trusts to stage/approve at.

    Returns:
        dict[str, Any]: Record of what was created ({name, project_id, model_id?}).
    """
    rung = STATE_LADDER.index(entry["state"])
    _log(f"🏗️  {entry['name']}  (target: {entry['state']})")

    payload = ProjectDetails(name=entry["name"], description=entry["description"], users=[]).model_dump()
    project_id = _ensure_ok(_post(client, "/projects/", payload, headers), "create project").json()["id"]
    record: dict[str, Any] = {"name": entry["name"], "project_id": project_id}

    if rung >= STATE_LADDER.index("unstaged_query"):
        submit_query(client, headers, project_id, entry["query_file"])

    if rung >= STATE_LADDER.index("staged"):
        # Staging rejects trusts that have not responded to the query yet.
        e2e_smoke.wait_for_trusts_responded(
            client, headers, project_id, timeout_s=300, required_trust_ids={str(t["id"]) for t in trusts}
        )
        trust_ids = [t["id"] for t in trusts]
        _ensure_ok(
            _post(client, f"/projects/{project_id}/stage/", {"trusts": trust_ids}, headers), "stage project"
        )
        _log("    ✅ staged")

    if rung >= STATE_LADDER.index("approved"):
        trust_ids = [t["id"] for t in trusts]
        _ensure_ok(
            _post(client, f"/step/project/{project_id}/approve/", {"trusts": trust_ids}, headers),
            "approve project",
        )
        _log("    ✅ approved (imaging import dispatched to the trusts)")

    if rung >= STATE_LADDER.index("approved_model"):
        model = entry["model"]
        model_id = _ensure_ok(
            _post(
                client,
                "/model",
                {"name": model["name"], "description": model["description"], "projectId": project_id},
                headers,
            ),
            "create model",
        ).json()["id"]
        record["model_id"] = model_id
        _log(f"    ✅ model created ({model['name']})")
        e2e_smoke.upload_files(client, headers, model_id, model["files_dir"])

    return record


def cleanup(client: requests.Session, headers: dict[str, str]) -> int:
    if not RECORD_FILE.exists():
        _log(f"Nothing to clean up — {RECORD_FILE} not found")
        return 0
    records = json.loads(RECORD_FILE.read_text()).get("created", [])
    failures = 0
    for record in records:
        project_id = record.get("project_id")
        resp = client.delete(
            f"{constants.BASE_URL}/projects/{project_id}", headers=headers, timeout=60
        )
        if resp.status_code < 300 or resp.status_code == 404:
            _log(f"🗑️  deleted {record.get('name')} ({project_id})")
        else:
            failures += 1
            _log(f"❗ failed to delete {record.get('name')} ({project_id}): HTTP {resp.status_code}")
    if failures == 0:
        RECORD_FILE.unlink()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = requests.Session()
    client.headers.update({"Content-Type": "application/json"})
    headers = e2e_smoke.authenticate()

    if args.cleanup:
        return cleanup(client, headers)

    trusts = _ensure_ok(_get(client, "/trust/", headers), "list trusts").json()
    trusts = e2e_smoke.select_trusts(trusts, args.trusts)
    if not trusts:
        raise SmokeFailure("No trusts registered on the hub — register trusts before seeding")
    _log(f"🏥 Using trusts: {[t.get('code') or t['name'] for t in trusts]}")

    entries = select_entries(CATALOGUE, args.states)

    # Idempotency: a re-run (or a lost record file) must not duplicate the
    # catalogue — approved entries kick off real imaging imports at the trusts.
    already = existing_project_names(client, headers, {e["name"] for e in entries})

    created: list[dict[str, Any]] = []
    try:
        for entry in entries:
            if entry["name"] in already:
                _log(f"⏭️  {entry['name']} already exists on the hub — skipping")
                continue
            created.append(build_project(client, headers, entry, trusts))
    finally:
        if created:
            # Merge with any previous run (e.g. a phased --states seeding) so
            # --cleanup always sees every seeded project.
            previous = json.loads(RECORD_FILE.read_text()).get("created", []) if RECORD_FILE.exists() else []
            RECORD_FILE.write_text(json.dumps({"created": previous + created}, indent=4))
            _log(f"💾 Recorded {len(previous) + len(created)} project(s) in {RECORD_FILE}")

    _log("🎉 Demo catalogue seeded. Remove it later with: make -C flip-api seed_demo_projects EXTRA_ARGS=--cleanup")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as failure:
        _log(f"❌ {failure}")
        sys.exit(1)
