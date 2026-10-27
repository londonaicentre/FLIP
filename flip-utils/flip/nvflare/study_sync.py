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

"""Keep NVFLARE's ``local/study_runtime.yaml`` in step with the projects this trust has staged (FLIP#1678).

Each FLIP project is an NVFLARE study. NVFLARE leaves the study-to-data mapping to the site, in
``local/study_runtime.yaml``, and its Kubernetes job launcher re-reads that file before every launch. This sidecar of
the fl-client pod writes it: one entry per project with staged cohort data (``<cohort-data>/<project>/<model>/``), so
the project's job pods mount, read-only,

- its cohort data at ``/data/<project>/cohort``, and
- for an imaging project, its own directory of XNAT's archive at ``/data/<project>/imaging``, with XNAT's group.

A project with no entry has no job image here, so NVFLARE does not start its job at this trust. Run as
``python -m flip.nvflare.study_sync <local dir>``, after ``flip.nvflare.k8s_job_mode`` has switched the kit.
"""

import argparse
import copy
import json
import os
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml

from flip.nvflare.k8s_job_mode import POD_TEMPLATE, STUDY_RUNTIME, _write_json, default_study, runtime_document

COHORT_VOLUME = "flip-cohort-data"
XNAT_VOLUME = "flip-xnat-archive"
MAIN_CONTAINER = "nvflare_job"  # NVFLARE's sentinel for the job container in a pod template
DEFAULT_INTERVAL = 2.0
ARCHIVE_REFRESH_SECONDS = 30.0
REQUEST_TIMEOUT = 10


@dataclass(frozen=True)
class SyncConfig:
    """What the sync needs to know about the trust."""

    local_dir: Path
    job_image: str
    cohort_data_dir: Path
    cohort_data_pvc: str
    xnat_archive_pvc: str | None
    xnat_gid: int
    imaging_api_url: str | None
    headers: Mapping[str, str] = field(default_factory=dict)
    interval: float = DEFAULT_INTERVAL


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for the study sync")
    return value


def config_from_env(local_dir: Path, env: Mapping[str, str]) -> SyncConfig:
    """Read the sync's settings from the environment the chart gives the sidecar.

    Raises:
        ValueError: naming the first missing or invalid setting.
    """
    header = env.get("TRUST_INTERNAL_SERVICE_KEY_HEADER", "X-Trust-Internal-Service-Key")
    return SyncConfig(
        local_dir=local_dir,
        job_image=_required(env, "FL_K8S_JOB_IMAGE"),
        cohort_data_dir=Path(_required(env, "FL_STUDY_COHORT_DATA_DIR")),
        cohort_data_pvc=_required(env, "FL_STUDY_COHORT_DATA_PVC"),
        xnat_archive_pvc=env.get("FL_STUDY_XNAT_ARCHIVE_PVC", "").strip() or None,
        xnat_gid=int(env.get("FL_STUDY_XNAT_GID", "1001")),
        imaging_api_url=env.get("IMAGING_API_URL", "").strip().rstrip("/") or None,
        headers={header: env.get("TRUST_INTERNAL_SERVICE_KEY", "")},
        interval=float(env.get("FL_STUDY_SYNC_INTERVAL", DEFAULT_INTERVAL)),
    )


def _is_project(name: str) -> bool:
    """A hub project id, as data-access-api names the directory: a lowercase UUID, so also a valid NVFLARE study."""
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


def staged_projects(cohort_data_dir: Path) -> list[str]:
    """The projects with at least one staged model. A model directory being written or removed starts with a dot."""
    if not cohort_data_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in cohort_data_dir.iterdir()
        if p.is_dir() and _is_project(p.name) and any(m.is_dir() and not m.name.startswith(".") for m in p.iterdir())
    )


def _main_container(spec: dict[str, Any]) -> dict[str, Any]:
    containers: list[dict[str, Any]] = spec.setdefault("containers", [])
    for container in containers:
        if container.get("name") == MAIN_CONTAINER:
            return container
    main = {"name": MAIN_CONTAINER}
    containers.append(main)
    return main


def _mount(spec: dict[str, Any], volume: str, pvc: str, path: str, sub_path: str) -> None:
    claim = {"claimName": pvc, "readOnly": True}
    spec.setdefault("volumes", []).append({"name": volume, "persistentVolumeClaim": claim})
    _main_container(spec).setdefault("volumeMounts", []).append(
        {"name": volume, "mountPath": path, "subPath": sub_path, "readOnly": True}
    )


def study_entry(
    job_image: str,
    template: Mapping[str, Any],
    project_id: str,
    cohort_pvc: str,
    xnat_pvc: str | None,
    xnat_id: str | None,
    gid: int,
) -> dict[str, Any]:
    """One project's ``study_runtime.yaml`` entry: the site's job pod, plus that project's read-only data.

    Args:
        job_image (str): The site's job image.
        template (Mapping[str, Any]): The chart's job-pod template.
        project_id (str): The hub project id, the study name.
        cohort_pvc (str): The cohort-data PVC.
        xnat_pvc (str | None): XNAT's data PVC, or None when the archive mount is off.
        xnat_id (str | None): The project's XNAT project id, when it has archived sessions.
        gid (int): XNAT's group: the archive is ``0750``/``0640``, owned by XNAT.

    Returns:
        dict[str, Any]: The entry.
    """
    pod = copy.deepcopy(dict(template))
    spec = pod.setdefault("spec", {})
    groups = spec.setdefault("securityContext", {}).setdefault("supplementalGroups", [])
    if gid not in groups:
        groups.append(gid)
    _mount(spec, COHORT_VOLUME, cohort_pvc, f"/data/{project_id}/cohort", project_id)
    if xnat_pvc and xnat_id:
        _mount(spec, XNAT_VOLUME, xnat_pvc, f"/data/{project_id}/imaging", f"archive/{xnat_id}")
    return {"container": {"image": job_image}, "pod_template": pod}


def xnat_archives(cfg: SyncConfig, projects: list[str]) -> dict[str, str]:
    """Map each staged project with archived sessions to its XNAT project id, from imaging-api.

    A project with no archived session gets no imaging mount: the kubelet would otherwise create a missing ``subPath``
    as a root-owned directory inside XNAT's own archive.

    Raises:
        requests.RequestException: if imaging-api cannot answer.
    """
    response = requests.get(f"{cfg.imaging_api_url}/projects/", headers=dict(cfg.headers), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    wanted = set(projects)
    claims: dict[str, list[str]] = {}
    for project in response.json():
        hub_id, xnat_id = project.get("secondary_ID"), project.get("ID")
        if hub_id in wanted and xnat_id:
            claims.setdefault(hub_id, []).append(xnat_id)
    archives = {}
    for hub_id, xnat_ids in claims.items():
        if len(xnat_ids) > 1:
            # Mounting either could hand the job another project's archive.
            print(f"study sync: XNAT projects {xnat_ids} all claim project {hub_id}; mounting none", file=sys.stderr)
            continue
        experiments = requests.get(
            f"{cfg.imaging_api_url}/projects/{xnat_ids[0]}/experiments",
            headers=dict(cfg.headers),
            timeout=REQUEST_TIMEOUT,
        )
        experiments.raise_for_status()
        if experiments.json():
            archives[hub_id] = xnat_ids[0]
    return archives


def sync_once(cfg: SyncConfig, last_archives: dict[str, str], refresh_archives: bool = True) -> dict[str, str]:
    """Write the study entries once. Rewrites the file only when its content changes.

    Args:
        cfg (SyncConfig): The trust's settings.
        last_archives (dict[str, str]): The XNAT archives the last sync used.
        refresh_archives (bool): Whether to ask imaging-api again.

    Returns:
        dict[str, str]: The XNAT archives this sync used: the last ones when imaging-api could not answer.
    """
    template_path = cfg.local_dir / POD_TEMPLATE
    if not template_path.is_file():
        print(f"study sync: waiting for {template_path}", file=sys.stderr)
        return last_archives
    template = yaml.safe_load(template_path.read_text()) or {}
    projects = staged_projects(cfg.cohort_data_dir)

    archives = last_archives
    if cfg.xnat_archive_pvc and cfg.imaging_api_url and refresh_archives and projects:
        try:
            archives = xnat_archives(cfg, projects)
        except (requests.RequestException, ValueError) as e:
            print(f"study sync: imaging-api did not answer ({e}); keeping the last imaging mounts", file=sys.stderr)

    studies = {
        p: study_entry(
            cfg.job_image, template, p, cfg.cohort_data_pvc, cfg.xnat_archive_pvc, archives.get(p), cfg.xnat_gid
        )
        for p in projects
    }
    document = runtime_document(default_study(cfg.job_image, has_pod_template=True), studies)
    target = cfg.local_dir / STUDY_RUNTIME
    if not target.is_file() or json.loads(target.read_text() or "null") != document:
        _write_json(target, document)
        print(f"study sync: {len(studies)} project stud{'y' if len(studies) == 1 else 'ies'}: {sorted(studies)}")
    return archives


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """CLI entry point: ``python -m flip.nvflare.study_sync <local_dir> [--once]``.

    Returns:
        ``1`` on an invalid setting; otherwise ``0`` after ``--once``, and never while looping.
    """
    parser = argparse.ArgumentParser(prog="flip.nvflare.study_sync", description=__doc__)
    parser.add_argument("local_dir", type=Path, help="The client kit's local/ directory")
    parser.add_argument("--once", action="store_true", help="Sync once and exit")
    parsed = parser.parse_args(argv)
    try:
        cfg = config_from_env(parsed.local_dir, os.environ if env is None else env)
    except ValueError as e:
        print(f"❌ cannot start the study sync: {e}", file=sys.stderr)
        return 1

    archives: dict[str, str] = {}
    known: list[str] = []
    refreshed_at = 0.0
    while True:
        staged = staged_projects(cfg.cohort_data_dir)
        # A newly staged project is looked up at once: its job is submitted a few seconds after staging.
        refresh = staged != known or time.monotonic() - refreshed_at >= ARCHIVE_REFRESH_SECONDS
        archives = sync_once(cfg, archives, refresh_archives=refresh)
        if refresh:
            known, refreshed_at = staged, time.monotonic()
        if parsed.once:
            return 0
        time.sleep(cfg.interval)


if __name__ == "__main__":
    sys.exit(main())
