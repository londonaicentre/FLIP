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

"""A project's study entry where job pods read XNAT's archive in place (FLIP#1857)."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests
import yaml
from nvflare.app_opt.job_launcher.study_runtime import load_study_runtime_file, resolve_study_runtime

from flip.nvflare.components.project_study import add_project_study
from flip.nvflare.k8s_job_mode import POD_TEMPLATE, STUDY_RUNTIME

PROJECT = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
XNAT_PROJECT = "7e2cc411-ba93-48af-8474-79e56c266013"
IMAGE = "ghcr.io/londonaicentre/flare-fl-client:stag"
STREAM = {"name": "IMAGES_TRANSPORT", "value": "stream"}


@pytest.fixture
def local(tmp_path) -> Path:
    """A switched kit's ``local/`` with the chart's template for ``jobPods.images: archive``."""
    local = tmp_path / "local"
    (local / "pod_specs").mkdir(parents=True)
    job = {"name": "nvflare_job", "env": [STREAM], "volumeMounts": [{"name": "dshm", "mountPath": "/dev/shm"}]}
    volumes = [
        {"name": "dshm", "emptyDir": {"medium": "Memory"}},
        {"name": "xnat-archive", "persistentVolumeClaim": {"claimName": "xnat-web", "readOnly": True}},
    ]
    pod = {"apiVersion": "v1", "kind": "Pod", "spec": {"containers": [job], "volumes": volumes}}
    (local / POD_TEMPLATE).write_text(yaml.safe_dump(pod))
    default = {"container": {"image": IMAGE}, "pod_template": POD_TEMPLATE}
    (local / STUDY_RUNTIME).write_text(json.dumps({"format_version": 2, "studies": {"default": default}}))
    return local


@pytest.fixture
def imaging_api():
    """imaging-api's project routes: the test sets ``projects`` and ``experiments``."""
    api = Mock(projects=[{"ID": XNAT_PROJECT, "secondary_ID": PROJECT}], experiments=[{"ID": "XNAT_E1"}])

    def get(url, **kwargs):
        api.calls.append((url, kwargs))
        return Mock(json=lambda: api.projects if url.endswith("/projects/") else api.experiments)

    api.calls = []
    with (
        patch("flip.nvflare.components.project_study.FlipConstants") as constants,
        patch("flip.nvflare.components.project_study.requests.get", side_effect=get),
    ):
        constants.IMAGING_API_URL = "http://imaging-api:8000/"
        constants.TRUST_INTERNAL_SERVICE_KEY_HEADER = "x-trust-internal-service-key"  # pragma: allowlist secret
        constants.TRUST_INTERNAL_SERVICE_KEY = "test-trust-internal-key"  # pragma: allowlist secret
        constants.XNAT_ARCHIVE_DIR = "/app/data/xnat-archive"
        yield api


def _job(local: Path) -> dict:
    [job] = json.loads((local / STUDY_RUNTIME).read_text())["studies"][PROJECT]["pod_template"]["spec"]["containers"]
    return job


def test_a_projects_job_pods_mount_only_that_projects_archive_directory_read_only(local, imaging_api):
    add_project_study(local, PROJECT)

    job = _job(local)
    assert job["volumeMounts"] == [
        {"name": "dshm", "mountPath": "/dev/shm"},
        {
            "name": "xnat-archive",
            "mountPath": "/app/data/xnat-archive",
            "subPath": f"archive/{XNAT_PROJECT}",
            "readOnly": True,
        },
    ]
    assert job["env"] == [{"name": "IMAGES_TRANSPORT", "value": "archive"}]
    assert [url for url, _ in imaging_api.calls] == [
        "http://imaging-api:8000/projects/",
        f"http://imaging-api:8000/projects/{XNAT_PROJECT}/experiments",
    ]
    assert all(
        kwargs["headers"] == {"x-trust-internal-service-key": "test-trust-internal-key"}
        for _, kwargs in imaging_api.calls
    )


def test_nvflare_reads_the_entry_as_that_studys_runtime(local, imaging_api):
    add_project_study(local, PROJECT)

    runtime_file = str(local / STUDY_RUNTIME)
    runtime = resolve_study_runtime(load_study_runtime_file(runtime_file, launcher_mode="k8s"), PROJECT, runtime_file)

    assert runtime.pod_template["spec"]["containers"][0]["volumeMounts"][-1]["subPath"] == f"archive/{XNAT_PROJECT}"


@pytest.mark.parametrize(
    ("projects", "experiments"),
    [
        ([], []),  # tabular: the project has no XNAT project
        ([{"ID": XNAT_PROJECT, "secondary_ID": PROJECT}], []),  # nothing archived yet: no directory to mount
        ([{"ID": XNAT_PROJECT, "secondary_ID": "another-project"}], [{"ID": "XNAT_E1"}]),
        ([{"ID": XNAT_PROJECT, "secondary_ID": PROJECT}, {"ID": "x", "secondary_ID": PROJECT}], [{"ID": "XNAT_E1"}]),
        ([{"ID": "../other", "secondary_ID": PROJECT}], [{"ID": "XNAT_E1"}]),
    ],
)
def test_without_one_archived_xnat_project_the_pod_mounts_nothing_and_streams(
    local, imaging_api, projects, experiments
):
    imaging_api.projects, imaging_api.experiments = projects, experiments

    add_project_study(local, PROJECT)

    job = _job(local)
    assert job["volumeMounts"] == [{"name": "dshm", "mountPath": "/dev/shm"}]
    assert job["env"] == [STREAM]


def test_the_pod_streams_when_imaging_api_cannot_answer(local, imaging_api):
    """A job of a project with no imaging must start while XNAT is down; a pod with no mount stays in its project."""
    with patch("flip.nvflare.components.project_study.requests.get", side_effect=requests.ReadTimeout("slow")):
        add_project_study(local, PROJECT)

    job = _job(local)
    assert job["volumeMounts"] == [{"name": "dshm", "mountPath": "/dev/shm"}]
    assert job["env"] == [STREAM]
