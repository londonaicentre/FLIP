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

"""``flip.nvflare.project_launcher``: a project's study entry is built at launch (FLIP#1678).

These drive NVFLARE's own ``_resolve_study_runtime`` underneath, so the file lookup, the legacy-file refusal and the
strict v2 parser are NVFLARE's, not a stand-in.
"""

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from flip.nvflare.k8s_job_mode import POD_TEMPLATE, STUDY_RUNTIME, STUDY_RUNTIME_DEFAULT
from flip.nvflare.project_launcher import ProjectK8sJobLauncher
from flip.nvflare.project_studies import xnat_project_id

PROJECT = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
XNAT_PROJECT = "5f0c7a52-0e3a-4c55-9d4e-0f7a1b2c3d4e"
IMAGE = "ghcr.io/londonaicentre/flare-fl-client:stag"
OPERATOR_IMAGE = "ghcr.io/londonaicentre/flare-fl-client:operator"
DEFAULT = {"container": {"image": IMAGE}, "pod_template": POD_TEMPLATE}


@pytest.fixture(autouse=True)
def constants(tmp_path):
    """What the fl-client knows of the images folder it shares with imaging-api."""
    with patch("flip.nvflare.project_studies.FlipConstants") as constants:
        constants.IMAGES_DIR = str(tmp_path / "images")
        constants.NET_ID = "net-1"
        yield constants


@pytest.fixture(autouse=True)
def approved():
    """imaging-api names the XNAT project it created when this trust approved the project; None = no such project."""
    with patch("flip.nvflare.project_launcher.xnat_project_id", return_value=XNAT_PROJECT) as lookup:
        yield lookup


@pytest.fixture
def launcher() -> ProjectK8sJobLauncher:
    """The launcher with only what study resolution reads; constructing it would need a cluster."""
    launcher = object.__new__(ProjectK8sJobLauncher)
    launcher.logger = logging.getLogger("test_project_launcher")
    launcher.study_data_pvc_file_path = None
    return launcher


def _workspace(tmp_path: Path, operator_file: dict | None = None, studies_file: bool = True) -> Path:
    """A switched kit: the template mounts no images, and the default study sits where k8s_job_mode puts it."""
    workspace = tmp_path / "workspace"
    local = workspace / "local"
    (local / "pod_specs").mkdir(parents=True)
    (tmp_path / "images" / "net-1").mkdir(parents=True)
    job = {"name": "nvflare_job", "env": [{"name": "IMAGES_TRANSPORT", "value": "stream"}]}
    volumes = [{"name": "images", "persistentVolumeClaim": {"claimName": "shared-images"}}]
    pod = {"apiVersion": "v1", "kind": "Pod", "spec": {"containers": [job], "volumes": volumes}}
    (local / POD_TEMPLATE).write_text(yaml.safe_dump(pod))
    default = {"format_version": 2, "studies": {"default": DEFAULT}}
    if not studies_file:
        (local / STUDY_RUNTIME).write_text(json.dumps(default))
        return workspace
    (local / STUDY_RUNTIME_DEFAULT).write_text(json.dumps(default))
    if operator_file is not None:
        (local / STUDY_RUNTIME).write_text(json.dumps(operator_file))
    return workspace


def _mounts(runtime) -> list[dict]:
    [job] = runtime.pod_template["spec"]["containers"]
    return job.get("volumeMounts", [])


def test_a_project_nobody_synced_gets_its_entry_at_launch(tmp_path, launcher):
    workspace = _workspace(tmp_path)  # the ConfigMap was never created: the link dangles

    runtime = launcher._resolve_study_runtime(str(workspace), PROJECT)

    assert runtime.container_image == IMAGE
    assert _mounts(runtime) == [
        {
            "name": "images",
            "mountPath": f"{tmp_path}/images/net-1/{PROJECT}",
            "subPath": f"net-1/{PROJECT}",
            "readOnly": True,
        }
    ]


def test_a_project_missing_from_the_operators_file_gets_its_entry(tmp_path, launcher):
    other = "0d3c6f8a-1b2c-4d5e-8f90-a1b2c3d4e5f6"
    workspace = _workspace(tmp_path, {"format_version": 2, "studies": {"default": DEFAULT, other: DEFAULT}})

    runtime = launcher._resolve_study_runtime(str(workspace), PROJECT)

    assert runtime.container_image == IMAGE
    assert _mounts(runtime)[0]["subPath"] == f"net-1/{PROJECT}"


def test_a_stale_entry_in_the_operators_file_is_not_used_for_a_project(tmp_path, launcher):
    """An entry synced before an upgrade carries the old image: the project's job gets the kit's current one."""
    entry = {"container": {"image": OPERATOR_IMAGE}}
    workspace = _workspace(tmp_path, {"format_version": 2, "studies": {"default": DEFAULT, PROJECT: entry}})

    runtime = launcher._resolve_study_runtime(str(workspace), PROJECT)

    assert runtime.container_image == IMAGE


def test_without_an_operators_file_the_default_study_is_read_from_study_runtime(tmp_path, launcher):
    workspace = _workspace(tmp_path, studies_file=False)

    runtime = launcher._resolve_study_runtime(str(workspace), PROJECT)

    assert runtime.container_image == IMAGE


@pytest.mark.parametrize("study", [None, "default", "not-a-project"])
def test_a_job_outside_a_project_runs_as_the_kits_default_study(tmp_path, launcher, study):
    """What a hub older than studies sends: no operator file needed, and the template resolves beside the kit's file."""
    workspace = _workspace(tmp_path)  # the ConfigMap was never created: the link dangles

    runtime = launcher._resolve_study_runtime(str(workspace), study)

    assert runtime.container_image == IMAGE
    assert _mounts(runtime) == [], "the default study streams: its template mounts no images"


def test_an_operators_entry_still_serves_a_study_that_is_not_a_project(tmp_path, launcher):
    entry = {"container": {"image": OPERATOR_IMAGE}}
    workspace = _workspace(tmp_path, {"format_version": 2, "studies": {"other": entry}})

    assert launcher._resolve_study_runtime(str(workspace), "other").container_image == OPERATOR_IMAGE


def test_a_kit_without_its_default_study_fails_naming_the_project(tmp_path, launcher):
    workspace = _workspace(tmp_path)
    (workspace / "local" / STUDY_RUNTIME_DEFAULT).unlink()
    (workspace / "local" / STUDY_RUNTIME).write_text("{}")

    with pytest.raises(RuntimeError, match=PROJECT):
        launcher._resolve_study_runtime(str(workspace), PROJECT)


def test_a_project_this_trust_never_approved_mounts_nothing_and_streams(tmp_path, launcher, approved):
    """The trust's own gate: no XNAT project here, so no studies at rest, whatever the hub named (fail closed)."""
    approved.return_value = None
    workspace = _workspace(tmp_path)

    runtime = launcher._resolve_study_runtime(str(workspace), PROJECT)

    assert runtime.container_image == IMAGE
    assert _mounts(runtime) == []
    assert not (tmp_path / "images" / "net-1" / PROJECT).exists(), "no folder is made for it either"
    approved.assert_called_once_with(PROJECT)


def test_while_imaging_api_cannot_answer_a_projects_job_streams(tmp_path, launcher, constants):
    """The real lookup, with imaging-api down: the job still starts, and mounts nothing."""
    constants.IMAGING_API_URL = "http://127.0.0.1:9"  # nothing listens on the discard port
    workspace = _workspace(tmp_path)

    with patch("flip.nvflare.project_launcher.xnat_project_id", xnat_project_id):
        runtime = launcher._resolve_study_runtime(str(workspace), PROJECT)

    assert runtime.container_image == IMAGE
    assert _mounts(runtime) == []
