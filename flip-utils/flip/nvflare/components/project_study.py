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
therefore sees only its own project's studies, at the paths imaging-api returns.

The template itself mounts no images, so a job outside a project's study never sees the cache: one in the default
study streams its studies, and a study that is not a project id gets no entry, so NVFLARE has no image for it and
does not start the job.
"""

import threading
import uuid
from pathlib import Path
from typing import Any

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
IMAGES_TRANSPORT = "IMAGES_TRANSPORT"


def is_project(study: object) -> bool:
    """Whether a study names a FLIP project: a lowercase UUID, so also safe as a folder name."""
    try:
        return isinstance(study, str) and str(uuid.UUID(study)) == study
    except ValueError:
        return False


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
        container.setdefault("volumeMounts", []).append(
            {"name": IMAGES_VOLUME, "mountPath": str(path), "subPath": folder}
        )
        # Read the studies by path, not by stream: flip.constants.FlipConstants.IMAGES_TRANSPORT.
        env = [e for e in container.get("env", []) if e["name"] != IMAGES_TRANSPORT]
        container["env"] = [*env, {"name": IMAGES_TRANSPORT, "value": "project"}]


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
