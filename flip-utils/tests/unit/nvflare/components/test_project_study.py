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

"""``flip.nvflare.components.project_study``: a project's study entry, written when its job launches (FLIP#1678)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_constant import FLContextKey
from nvflare.apis.job_def import JobMetaKey
from nvflare.app_opt.job_launcher.study_runtime import load_study_runtime_file, resolve_study_runtime

from flip.nvflare.components.project_study import ProjectStudy, add_project_study, is_project
from flip.nvflare.k8s_job_mode import POD_TEMPLATE, STUDY_RUNTIME

PROJECT = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
OTHER = "0d3c6f8a-1b2c-4d5e-8f90-a1b2c3d4e5f6"
IMAGE = "ghcr.io/londonaicentre/flare-fl-client:stag"


def _kit(tmp_path: Path, images_mount: bool = True) -> tuple[Path, Path]:
    """A switched kit's ``local/`` and the net's images folder, as the fl-client mounts it."""
    local, net_dir = tmp_path / "local", tmp_path / "images" / "net-1"
    (local / "pod_specs").mkdir(parents=True)
    net_dir.mkdir(parents=True)
    mounts = [{"name": "dshm", "mountPath": "/dev/shm"}]
    if images_mount:
        mounts.insert(0, {"name": "images", "mountPath": str(net_dir), "subPath": "net-1"})
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "spec": {"containers": [{"name": "nvflare_job", "volumeMounts": mounts}], "volumes": [{"name": "images"}]},
    }
    (local / POD_TEMPLATE).write_text(yaml.safe_dump(pod))
    default = {"container": {"image": IMAGE}, "pod_template": POD_TEMPLATE}
    (local / STUDY_RUNTIME).write_text(json.dumps({"format_version": 2, "studies": {"default": default}}))
    return local, net_dir


def _studies(local: Path) -> dict:
    return json.loads((local / STUDY_RUNTIME).read_text())["studies"]


def _launch(local: Path, study: object) -> None:
    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    props = {
        FLContextKey.JOB_META: {JobMetaKey.STUDY.value: study},
        FLContextKey.WORKSPACE_OBJECT: MagicMock(get_site_config_dir=lambda: str(local)),
    }
    fl_ctx.get_prop.side_effect = lambda key, default=None: props.get(key, default)
    ProjectStudy().handle_event(EventType.BEFORE_JOB_LAUNCH, fl_ctx)


def test_a_projects_job_pods_mount_only_that_projects_images_folder(tmp_path):
    local, net_dir = _kit(tmp_path)

    _launch(local, PROJECT)

    entry = _studies(local)[PROJECT]
    assert entry["container"] == {"image": IMAGE}
    [images, dshm] = entry["pod_template"]["spec"]["containers"][0]["volumeMounts"]
    assert images == {"name": "images", "mountPath": f"{net_dir}/{PROJECT}", "subPath": f"net-1/{PROJECT}"}
    assert dshm == {"name": "dshm", "mountPath": "/dev/shm"}


def test_the_projects_folder_exists_before_the_job_pod_mounts_it(tmp_path):
    local, net_dir = _kit(tmp_path)

    _launch(local, PROJECT)

    assert (net_dir / PROJECT).is_dir()


def test_nvflare_reads_the_entry_as_that_studys_runtime(tmp_path):
    local, _ = _kit(tmp_path)
    _launch(local, PROJECT)

    runtime_file = str(local / STUDY_RUNTIME)
    runtime = resolve_study_runtime(load_study_runtime_file(runtime_file, launcher_mode="k8s"), PROJECT, runtime_file)

    assert runtime.container_image == IMAGE
    assert runtime.pod_template["spec"]["containers"][0]["volumeMounts"][0]["subPath"] == f"net-1/{PROJECT}"


def test_each_project_keeps_its_own_entry_beside_the_default(tmp_path):
    local, _ = _kit(tmp_path)

    _launch(local, PROJECT)
    _launch(local, OTHER)

    studies = _studies(local)
    assert set(studies) == {"default", PROJECT, OTHER}
    assert studies["default"] == {"container": {"image": IMAGE}, "pod_template": POD_TEMPLATE}


def test_a_second_job_of_the_project_leaves_the_file_alone(tmp_path):
    local, _ = _kit(tmp_path)
    _launch(local, PROJECT)
    before = (local / STUDY_RUNTIME).stat().st_mtime_ns

    _launch(local, PROJECT)

    assert (local / STUDY_RUNTIME).stat().st_mtime_ns == before


def test_a_template_with_no_images_mount_is_used_as_it_is(tmp_path):
    local, net_dir = _kit(tmp_path, images_mount=False)

    _launch(local, PROJECT)

    assert _studies(local)[PROJECT]["pod_template"] == yaml.safe_load((local / POD_TEMPLATE).read_text())
    assert list(net_dir.iterdir()) == []


@pytest.mark.parametrize("study", [None, "default", "../net-2", PROJECT.upper(), 7])
def test_a_study_that_is_not_a_project_gets_no_entry(tmp_path, study):
    local, net_dir = _kit(tmp_path)

    _launch(local, study)

    assert set(_studies(local)) == {"default"}
    assert list(net_dir.iterdir()) == []


def test_other_events_are_ignored(tmp_path):
    local, _ = _kit(tmp_path)

    ProjectStudy().handle_event(EventType.AFTER_JOB_LAUNCH, MagicMock())

    assert set(_studies(local)) == {"default"}


def test_a_default_study_without_a_template_gives_the_image_alone(tmp_path):
    local, _ = _kit(tmp_path)
    (local / STUDY_RUNTIME).write_text(
        json.dumps({"format_version": 2, "studies": {"default": {"container": {"image": IMAGE}}}})
    )

    add_project_study(local, PROJECT)

    assert _studies(local)[PROJECT] == {"container": {"image": IMAGE}}


def test_only_a_lowercase_uuid_names_a_project():
    assert is_project(PROJECT)
    assert not is_project(PROJECT.replace("-", ""))
