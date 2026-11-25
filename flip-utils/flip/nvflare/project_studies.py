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

"""Build NVFLARE's ``study_runtime.yaml`` for a site: the default study plus one entry per FLIP project (FLIP#1678).

Each FLIP project is an NVFLARE study. NVFLARE leaves the study-to-data mapping to the site, in
``local/study_runtime.yaml``, which its Kubernetes launcher re-reads at every launch. FLIP's launcher
(``flip.nvflare.project_launcher``) builds a project's entry with ``project_entry`` when the project's job starts.
The operator can still write the site's file, a ConfigMap the chart mounts (the kit is read-only while the client
runs, FLIP#1570), with ``make -C trust/deploy/helm sync-project-studies``, which runs this module inside the
fl-client and applies what it prints; a project's job does not use it::

    python -m flip.nvflare.project_studies <local_dir> [<project id> ...]

A project's entry is the default study's job image and pod template and, where the template declares the shared
images volume, a read-only mount of the project's own folder of it. A job pod therefore sees only its own
project's studies, at the paths imaging-api returns. Where the chart rendered an archive patch beside the template
(``jobPods.images: archive``, FLIP#1857), a project whose sessions XNAT has archived gets the patch (XNAT's volume,
group and placement) and a read-only mount of its own directory of XNAT's archive, and its jobs read their studies in
place. Every other job pod, a tabular project's among them, carries nothing of XNAT's and runs on any node.

The template itself mounts no images, so a job outside a project's study never sees the cache: one in the default
study streams its studies, and a study with no entry has no job image, so NVFLARE does not start its job.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import requests
import yaml

from flip.constants import FlipConstants
from flip.constants.flip_constants import XNAT_ARCHIVE_DIR
from flip.nvflare.k8s_job_mode import ARCHIVE_POD_PATCH, ARCHIVE_VOLUME, STUDY_RUNTIME_DEFAULT

# The chart's name for the shared images volume in the job-pod template.
IMAGES_VOLUME = "images"
IMAGES_TRANSPORT = "IMAGES_TRANSPORT"
_REQUEST_TIMEOUT = 30


def is_project(study: object) -> bool:
    """Whether a study names a FLIP project: a lowercase UUID, so also safe as a folder name."""
    try:
        return isinstance(study, str) and str(uuid.UUID(study)) == study
    except ValueError:
        return False


def _read_studies_by(container: dict[str, Any], transport: str) -> None:
    """Set how the job reads its studies, where the template alone would stream them (``IMAGES_TRANSPORT``)."""
    env = [e for e in container.get("env", []) if e["name"] != IMAGES_TRANSPORT]
    container["env"] = [*env, {"name": IMAGES_TRANSPORT, "value": transport}]


def mount_project_images(pod: dict[str, Any], project: str) -> None:
    """Mount the project's folder of the shared images volume in a pod that declares the volume.

    The folder is created here, by the fl-client, which shares the net's images folder with imaging-api: the kubelet
    would otherwise create the missing ``subPath`` as root, and imaging-api could not write the project's studies.
    """
    if not any(volume["name"] == IMAGES_VOLUME for volume in pod["spec"].get("volumes", [])):
        return
    folder = f"{FlipConstants.NET_ID}/{project}"
    path = Path(FlipConstants.IMAGES_DIR, folder)
    path.mkdir(exist_ok=True)
    for container in pod["spec"]["containers"]:
        # Read-only: imaging-api writes the cache, and a job never changes what it trains on.
        container.setdefault("volumeMounts", []).append(
            {"name": IMAGES_VOLUME, "mountPath": str(path), "subPath": folder, "readOnly": True}
        )
        _read_studies_by(container, "project")


def _streams(project: str, reason: str) -> None:
    """Tell the operator, on stderr (stdout is the file), that a project's job pods stream rather than mount."""
    print(f"⚠️  project {project}: its job pods stream their studies, {reason}", file=sys.stderr)


def _get(path: str) -> Any:
    """GET an imaging-api route with the trust-internal key and return its JSON."""
    url = str(FlipConstants.IMAGING_API_URL).rstrip("/")
    headers = {FlipConstants.TRUST_INTERNAL_SERVICE_KEY_HEADER: FlipConstants.TRUST_INTERNAL_SERVICE_KEY}
    response = requests.get(f"{url}{path}", headers=headers, timeout=_REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def xnat_project_id(project: str) -> str | None:
    """The XNAT project imaging-api created for the hub project when this trust approved it; None if there is none.

    None as well when imaging-api cannot say, and each None says why on stderr: a pod with no mount streams, which
    keeps it to its own project's approved studies.
    """
    try:
        ids: list[str] = [p["ID"] for p in _get("/projects/") if p.get("secondary_ID") == project]
    except (requests.RequestException, KeyError, TypeError, AttributeError) as e:
        _streams(project, f"as imaging-api could not name its XNAT project ({type(e).__name__}: {e})")
        return None
    if not ids:
        _streams(project, "as XNAT holds no project for it (a tabular project, or not approved at this trust)")
        return None
    # FLIP names its XNAT projects with a UUID; anything else is not used as a folder name here.
    if len(ids) != 1 or not is_project(ids[0]):
        _streams(project, f"as more than one XNAT project, or one not named with a UUID, claims it: {ids}")
        return None
    return ids[0]


def xnat_project(project: str) -> str | None:
    """The XNAT project holding the hub project's sessions, from imaging-api; None until it has archived one."""
    xnat_id = xnat_project_id(project)
    if xnat_id is None:
        return None
    try:
        archived = bool(_get(f"/projects/{xnat_id}/experiments"))
    except (requests.RequestException, TypeError, AttributeError) as e:
        _streams(project, f"as imaging-api could not list its XNAT sessions ({type(e).__name__}: {e})")
        return None
    if not archived:
        _streams(project, "as XNAT has archived nothing for it yet: its jobs mount the archive once it has")
        return None
    return xnat_id


def _apply_patch(pod: dict[str, Any], patch: dict[str, Any]) -> None:
    """Add the patch's ``spec`` to the pod's: its lists (volumes) appended, its objects (affinity...) merged."""
    spec = pod["spec"]
    for key, value in patch["spec"].items():
        if isinstance(value, list):
            spec[key] = [*spec.get(key, []), *value]
        elif isinstance(value, dict):
            spec[key] = {**spec.get(key, {}), **value}
        else:
            spec[key] = value


def mount_project_archive(pod: dict[str, Any], project: str, patch: dict[str, Any] | None) -> None:
    """Mount the project's own directory of XNAT's archive, read-only, where the chart rendered an archive patch.

    The patch (XNAT's volume, its group, and on a ReadWriteOnce volume a place on xnat-web's node) is applied here,
    to this project's pod only, so a job that streams never waits for XNAT to be scheduled. Only once the project has
    archived sessions: the directory does not exist before, and a pod whose ``subPath`` is missing on a read-only
    volume does not start. Until then the pod streams, as the template has it.
    """
    if patch is None:
        return
    xnat_id = xnat_project(project)
    if xnat_id is None:
        return
    _apply_patch(pod, patch)
    mount = {
        "name": ARCHIVE_VOLUME,
        "mountPath": XNAT_ARCHIVE_DIR,
        "subPath": f"archive/{xnat_id}",
        "readOnly": True,
    }
    for container in pod["spec"]["containers"]:
        container.setdefault("volumeMounts", []).append(mount)
        _read_studies_by(container, "archive")


def project_entry(local_dir: Path, default: dict[str, Any], project: str) -> dict[str, Any]:
    """One project's study entry: the default study's, plus the project's own images folder.

    Args:
        local_dir (Path): The client kit's ``local/`` directory, which the default study's template path is relative to.
        default (dict[str, Any]): The default study's entry.
        project (str): The project id, the study's name.

    Returns:
        dict[str, Any]: The entry. NVFLARE's study entries do not inherit, so it carries the image and the template.
    """
    entry: dict[str, Any] = {"container": default["container"]}
    if "pod_template" in default:
        pod = yaml.safe_load((local_dir / default["pod_template"]).read_text(encoding="utf-8"))
        patch_file = local_dir / ARCHIVE_POD_PATCH
        patch = yaml.safe_load(patch_file.read_text(encoding="utf-8")) if patch_file.is_file() else None
        mount_project_images(pod, project)
        mount_project_archive(pod, project, patch)
        entry["pod_template"] = pod
    return entry


def study_runtime(local_dir: Path, projects: list[str]) -> dict[str, Any]:
    """Return the site's ``study_runtime.yaml``: the default study the kit was switched with, and each project's.

    Raises:
        ValueError: naming an id that is not a project id.
        OSError: the default study's file or its pod template cannot be read.
    """
    unknown = [project for project in projects if not is_project(project)]
    if unknown:
        raise ValueError(f"not a project id (a lowercase UUID): {', '.join(unknown)}")
    document: dict[str, Any] = yaml.safe_load((local_dir / STUDY_RUNTIME_DEFAULT).read_text(encoding="utf-8"))
    default = document["studies"]["default"]
    for project in sorted(set(projects)):
        document["studies"][project] = project_entry(local_dir, default, project)
    return document


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Prints the file (JSON, which is valid YAML) and returns ``0``, or ``1`` with the reason."""
    parser = argparse.ArgumentParser(prog="flip.nvflare.project_studies", description=__doc__)
    parser.add_argument("local_dir", type=Path, help="The client kit's local/ directory")
    parser.add_argument("projects", nargs="*", help="The ids of the projects this site serves")
    parsed = parser.parse_args(argv)
    try:
        document = study_runtime(parsed.local_dir, parsed.projects)
    except (OSError, ValueError) as e:
        print(f"❌ cannot build study_runtime.yaml: {e}", file=sys.stderr)
        return 1
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
