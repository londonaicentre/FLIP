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
"""Capture a project's API responses as fixtures for the public demo (FLIP#1746).

Calls, as the signed-in admin, exactly the endpoints ``flip-ui/mocks/demo-server.ts``
serves for one project and writes each response as JSON under
``<out-root>/<slug>/data/`` in the names ``flip-ui/mocks/demo/registry.ts`` expects
(``project``, ``models``, ``cohort``, ``image_status``, then ``m<n>_<kind>`` per model).
It writes raw captures and prints every trust they name. Map each one in the project's
``capture.json``, then run ``npm run demo-fixtures -- scrub <slug>`` in flip-ui: the bundle
ships the files as they are, so they must be on the demo world before they are committed.

    make -C flip-api capture_demo_project EXTRA_ARGS="--project-id <uuid> --slug ehr-risk"

Authentication is the e2e smoke's: ``FLIP_E2E_TOKEN`` for a remote hub, else the
configured identity provider as admin.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

from flip_api.utils import constants
from tests.integration.utils import admin_authentication

MODEL_KINDS: list[tuple[str, str, str]] = [
    # (fixture kind, method, path template)
    ("step", "POST", "/step/model/{model_id}"),
    ("config", "GET", "/files/model/{model_id}/config.json"),
    ("metrics", "GET", "/model/{model_id}/metrics"),
    ("logs", "GET", "/model/{model_id}/logs"),
    ("files", "GET", "/files/model/{model_id}/get/files"),
]
# Not captured: /files/model/{id}/fl/results — the demo is browse-only and serves [] for every model.


def _log(msg: str) -> None:
    print(msg, flush=True)


def authenticate() -> dict[str, str]:
    token = os.environ.get("FLIP_E2E_TOKEN")
    if token:
        return {"scheme": "Bearer", "authorization": f"Bearer {token}"}
    return admin_authentication()


def _request(client: requests.Session, method: str, path: str, headers: dict[str, str]) -> requests.Response:
    url = f"{constants.BASE_URL}{path}"
    if method == "POST":
        return client.post(url, headers=headers, timeout=60)
    return client.get(url, headers=headers, timeout=60)


def _fetch(client: requests.Session, method: str, path: str, headers: dict[str, str], *, optional: bool = False) -> Any:
    """Return the JSON body, ``None`` for a 404 on an optional endpoint, and raise on anything else."""
    response = _request(client, method, path, headers)
    if optional and response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"{method} {path} answered HTTP {response.status_code}: {response.text[:200]}")
    return response.json()


def _file_body(client: requests.Session, presigned: Any, what: str) -> Any:
    """Follow the hub's ``{fileName, url}`` answer to the file itself: the demo serves the body.

    The URL is presigned, so no hub headers go with it.
    """
    if not isinstance(presigned, dict) or "url" not in presigned:
        raise RuntimeError(f"{what}: expected a presigned-URL answer, got {str(presigned)[:120]}")
    response = client.get(presigned["url"], timeout=60)
    if response.status_code != 200:
        raise RuntimeError(f"{what}: object store answered HTTP {response.status_code}")
    return response.json()


def _write(out_dir: Path, name: str, payload: Any) -> Path:
    target = out_dir / f"{name}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def _trusts_named(*payloads: Any) -> dict[str, tuple[str, str]]:
    """Every ``{id, name, code}`` trust reference in the payloads, keyed by id."""
    seen: dict[str, tuple[str, str]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if {"id", "name", "code"} <= value.keys():
                seen[str(value["id"])] = (str(value["code"]), str(value["name"]))
            for inner in value.values():
                visit(inner)
        elif isinstance(value, list):
            for inner in value:
                visit(inner)

    for payload in payloads:
        visit(payload)
    return seen


def capture(client: requests.Session, headers: dict[str, str], project_id: str, out_dir: Path) -> list[Path]:
    """Write the fixture set for ``project_id`` into ``out_dir`` and return the files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    project = _fetch(client, "GET", f"/projects/{project_id}", headers)
    written.append(_write(out_dir, "project", project))
    models = _fetch(client, "GET", f"/projects/{project_id}/models", headers)
    written.append(_write(out_dir, "models", models))
    cohort = _fetch(client, "GET", f"/cohort/{project['query']['id']}", headers)
    written.append(_write(out_dir, "cohort", cohort))
    # Per-trust frozen cohort membership (FLIP#857), shown beside the imaging status on the project page.
    written.append(
        _write(out_dir, "cohort_snapshots", _fetch(client, "GET", f"/projects/{project_id}/cohort-snapshots", headers))
    )
    # A tabular-only project (has_imaging=false) has no imaging stage: the route answers 404.
    image_status = _fetch(client, "GET", f"/projects/{project_id}/image/status", headers, optional=True)
    if image_status is not None:
        written.append(_write(out_dir, "image_status", image_status))

    steps: list[Any] = []
    for n, model in enumerate(models["data"], start=1):
        for kind, method, template in MODEL_KINDS:
            path = template.format(model_id=model["id"])
            payload = _fetch(client, method, path, headers)
            if kind == "config":
                payload = _file_body(client, payload, path)
            if kind == "step":
                steps.append(payload)
            written.append(_write(out_dir, f"m{n}_{kind}", payload))

    _log(f"📦 {len(written)} fixtures written to {out_dir}")
    named = _trusts_named(project, *steps)
    # Trusts that only answered the cohort query appear by id alone; capture.json must map them too.
    by_id_only = {
        str(trust_id)
        for trust_id in [*cohort.get("trustRecordCounts", {}), *project.get("query", {}).get("queriedTrustIds", [])]
        if str(trust_id) not in named
    }
    _log("Trusts named by this capture: map each to a roster site (or null) in capture.json's trustMap:")
    for trust_id, (code, name) in sorted(named.items()):
        _log(f"  {trust_id}  {code}  {name}")
    for trust_id in sorted(by_id_only):
        _log(f"  {trust_id}  (cohort only: answered the query, took no part)")
    return written


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-id", required=True, help="UUID of the project to capture.")
    parser.add_argument("--slug", required=True, help="Directory name under --out-root, e.g. ehr-risk.")
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("../flip-ui/mocks/demo/projects"),
        help="Where <slug>/data/ is written (default: the UI's demo project registry).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    headers = authenticate()
    client = requests.Session()
    capture(client, headers, args.project_id, args.out_root / args.slug / "data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
