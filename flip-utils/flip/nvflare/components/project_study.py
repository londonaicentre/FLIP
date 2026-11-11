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

"""Give each FLIP project its own entry in NVFLARE's ``local/study_runtime.yaml`` (FLIP#1678).

Each FLIP project is an NVFLARE study. NVFLARE leaves the study-to-data mapping to the site, in
``local/study_runtime.yaml``, which its Kubernetes launcher re-reads at every launch, and it fires
``BEFORE_JOB_LAUNCH`` with the job's study before that read. This component, which ``flip.nvflare.k8s_job_mode`` adds
to the fl-client's ``resources.json``, writes the study's entry then: the default study's job image and pod template
and, where the template declares the shared images volume, a mount of the project's own folder of it. A job pod
therefore sees only its own project's studies, at the paths imaging-api returns. Where the template declares XNAT's
volume instead, the mount is the project's own directory of XNAT's archive, read-only (FLIP#1857), and the job reads
its studies in place.

The template itself mounts no images, so a job outside a project's study never sees the cache: one in the default
study streams its studies, and a study that is not a project id gets no entry, so NVFLARE has no image for it and
does not start the job.
"""

import threading
import uuid
from pathlib import Path
from typing import Any

import requests
import yaml
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_component import FLComponent
from nvflare.apis.fl_constant import FLContextKey
from nvflare.apis.fl_context import FLContext
from nvflare.apis.job_def import JobMetaKey

from flip.constants import FlipConstants
from flip.nvflare.k8s_job_mode import STUDY_RUNTIME, _write_json

# The chart's name for the shared images volume in the job-pod template.
IMAGES_VOLUME = "images"
# ...and for XNAT's own volume, whose archive/<XNAT project> is the project's directory.
ARCHIVE_VOLUME = "xnat-archive"
IMAGES_TRANSPORT = "IMAGES_TRANSPORT"
_REQUEST_TIMEOUT = 10


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


def xnat_project(project: str) -> str | None:
    """The XNAT project holding the hub project's sessions, from imaging-api; None until it has archived one.

    Raises:
        requests.RequestException: if imaging-api cannot answer.
    """
    url = str(FlipConstants.IMAGING_API_URL).rstrip("/")
    headers = {FlipConstants.TRUST_INTERNAL_SERVICE_KEY_HEADER: FlipConstants.TRUST_INTERNAL_SERVICE_KEY}
    projects = requests.get(f"{url}/projects/", headers=headers, timeout=_REQUEST_TIMEOUT)
    projects.raise_for_status()
    ids = [p["ID"] for p in projects.json() if p.get("secondary_ID") == project]
    # FLIP names its XNAT projects with a UUID; anything else is not used as a folder name here.
    if len(ids) != 1 or not is_project(ids[0]):
        return None
    experiments = requests.get(f"{url}/projects/{ids[0]}/experiments", headers=headers, timeout=_REQUEST_TIMEOUT)
    experiments.raise_for_status()
    return ids[0] if experiments.json() else None


def mount_project_archive(pod: dict[str, Any], project: str) -> None:
    """Mount the project's own directory of XNAT's archive, read-only, in a pod that declares XNAT's volume.

    Only once the project has archived sessions: the directory does not exist before, and a pod whose ``subPath``
    is missing on a read-only volume does not start. Until then the pod streams, as the template has it.
    """
    if not any(volume["name"] == ARCHIVE_VOLUME for volume in pod["spec"].get("volumes", [])):
        return
    xnat_id = xnat_project(project)
    if xnat_id is None:
        return
    mount = {
        "name": ARCHIVE_VOLUME,
        "mountPath": FlipConstants.XNAT_ARCHIVE_DIR,
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
        mount_project_images(pod, project)
        mount_project_archive(pod, project)
        entry["pod_template"] = pod
    return entry


def add_project_study(local_dir: Path, project: str) -> None:
    """Write one project's entry into ``study_runtime.yaml``, leaving the file alone when it already holds it."""
    runtime_path = local_dir / STUDY_RUNTIME
    document = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    studies = document["studies"]
    entry = project_entry(local_dir, studies["default"], project)
    if studies.get(project) != entry:
        studies[project] = entry
        _write_json(runtime_path, document)


class ProjectStudy(FLComponent):
    """Writes a project's study entry when one of its jobs is about to launch."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()  # two jobs can launch at once, and both rewrite the one file

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        if event_type != EventType.BEFORE_JOB_LAUNCH:
            return
        study = fl_ctx.get_prop(FLContextKey.JOB_META, {}).get(JobMetaKey.STUDY.value)
        if not is_project(study):
            return
        local_dir = Path(fl_ctx.get_prop(FLContextKey.WORKSPACE_OBJECT).get_site_config_dir())
        with self._lock:
            add_project_study(local_dir, study)
        self.log_info(fl_ctx, f"Study {study} mapped to its project's images in {STUDY_RUNTIME}")
