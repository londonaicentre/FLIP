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
``local/study_runtime.yaml``, which the site's operator maintains and its Kubernetes launcher re-reads at every
launch. The fl-client only reads that file: its kit is read-only while it runs (FLIP#1570). The trust's chart
therefore mounts the file from a ConfigMap, and the operator fills it with
``make -C trust/deploy/helm sync-project-studies``, which runs this module inside the fl-client and applies what
it prints::

    python -m flip.nvflare.project_studies <local_dir> [<project id> ...]

A project's entry is the default study's job image and pod template and, where the template declares the shared
images volume, a read-only mount of the project's own folder of it. A job pod therefore sees only its own
project's studies, at the paths imaging-api returns.

The template itself mounts no images, so a job outside a project's study never sees the cache: one in the default
study streams its studies, and a study with no entry has no job image, so NVFLARE does not start its job.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml

from flip.constants import FlipConstants
from flip.nvflare.k8s_job_mode import STUDY_RUNTIME_DEFAULT

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
        # Read-only: imaging-api writes the cache, and a job never changes what it trains on.
        container.setdefault("volumeMounts", []).append(
            {"name": IMAGES_VOLUME, "mountPath": str(path), "subPath": folder, "readOnly": True}
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
