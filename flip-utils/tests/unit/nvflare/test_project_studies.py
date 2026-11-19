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

"""``flip.nvflare.project_studies``: the site's study file, one entry per project (FLIP#1678)."""

import json
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from nvflare.app_opt.job_launcher.study_runtime import load_study_runtime_file, resolve_study_runtime

from flip.nvflare.k8s_job_mode import POD_TEMPLATE, STUDY_RUNTIME_DEFAULT
from flip.nvflare.project_studies import is_project, main, study_runtime

PROJECT = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
OTHER = "0d3c6f8a-1b2c-4d5e-8f90-a1b2c3d4e5f6"
IMAGE = "ghcr.io/londonaicentre/flare-fl-client:stag"
DEFAULT = {"container": {"image": IMAGE}, "pod_template": POD_TEMPLATE}


@pytest.fixture(autouse=True)
def constants(tmp_path):
    """What the fl-client knows of the images folder it shares with imaging-api."""
    with patch("flip.nvflare.project_studies.FlipConstants") as constants:
        constants.IMAGES_DIR = str(tmp_path / "images")
        constants.NET_ID = "net-1"
        yield constants


def _kit(tmp_path: Path, images_volume: bool = True) -> tuple[Path, Path]:
    """A switched kit's ``local/`` and the net's images folder, as the fl-client mounts it.

    The template is the chart's: it mounts no images and streams, and declares the shared images volume only where
    the site's job pods can attach it.
    """
    local, net_dir = tmp_path / "local", tmp_path / "images" / "net-1"
    (local / "pod_specs").mkdir(parents=True)
    net_dir.mkdir(parents=True)
    job = {
        "name": "nvflare_job",
        "env": [{"name": "NET_ID", "value": "net-1"}, {"name": "IMAGES_TRANSPORT", "value": "stream"}],
        "volumeMounts": [{"name": "dshm", "mountPath": "/dev/shm"}],
    }
    volumes = [{"name": "dshm", "emptyDir": {"medium": "Memory"}}]
    if images_volume:
        volumes.append({"name": "images", "persistentVolumeClaim": {"claimName": "shared-images"}})
    pod = {"apiVersion": "v1", "kind": "Pod", "spec": {"containers": [job], "volumes": volumes}}
    (local / POD_TEMPLATE).write_text(yaml.safe_dump(pod))
    (local / STUDY_RUNTIME_DEFAULT).write_text(json.dumps({"format_version": 2, "studies": {"default": DEFAULT}}))
    return local, net_dir


def test_a_projects_job_pods_mount_only_that_projects_images_folder(tmp_path):
    local, net_dir = _kit(tmp_path)

    entry = study_runtime(local, [PROJECT])["studies"][PROJECT]

    assert entry["container"] == {"image": IMAGE}
    [job] = entry["pod_template"]["spec"]["containers"]
    assert job["volumeMounts"] == [
        {"name": "dshm", "mountPath": "/dev/shm"},
        {"name": "images", "mountPath": f"{net_dir}/{PROJECT}", "subPath": f"net-1/{PROJECT}", "readOnly": True},
    ]
    # It reads its studies by path, where the template alone would stream them.
    assert job["env"] == [{"name": "NET_ID", "value": "net-1"}, {"name": "IMAGES_TRANSPORT", "value": "project"}]


def test_the_template_itself_never_mounts_images(tmp_path):
    """A job in the default study, or in a study with no entry, cannot see the cache."""
    local, _ = _kit(tmp_path)

    document = study_runtime(local, [PROJECT])

    [job] = yaml.safe_load((local / POD_TEMPLATE).read_text())["spec"]["containers"]
    assert [m["name"] for m in job["volumeMounts"]] == ["dshm"]
    assert document["studies"]["default"] == DEFAULT


def test_the_projects_folder_exists_before_the_job_pod_mounts_it(tmp_path):
    local, net_dir = _kit(tmp_path)

    study_runtime(local, [PROJECT])

    assert (net_dir / PROJECT).is_dir()


def test_nvflare_reads_the_file_as_each_studys_runtime(tmp_path):
    """Through the link the kit holds, as the launcher does: the default study's template path resolves in local/."""
    local, _ = _kit(tmp_path)
    mounted = tmp_path / "studies" / "study_runtime.yaml"
    mounted.parent.mkdir()
    mounted.write_text(json.dumps(study_runtime(local, [PROJECT])))
    runtime_file = local / "study_runtime.yaml"
    runtime_file.symlink_to(mounted)

    studies = load_study_runtime_file(str(runtime_file), launcher_mode="k8s")
    runtime = resolve_study_runtime(studies, PROJECT, str(runtime_file))

    assert runtime.container_image == IMAGE
    assert runtime.pod_template["spec"]["containers"][0]["volumeMounts"][-1]["subPath"] == f"net-1/{PROJECT}"
    # A project the operator has not added has no job image, so NVFLARE does not start its job.
    assert not resolve_study_runtime(studies, OTHER, str(runtime_file)).container_image
    assert resolve_study_runtime(studies, "default", str(runtime_file)).pod_template["kind"] == "Pod"


def test_the_file_lists_exactly_the_projects_given_beside_the_default(tmp_path):
    local, _ = _kit(tmp_path)

    assert set(study_runtime(local, [PROJECT, OTHER, PROJECT])["studies"]) == {"default", PROJECT, OTHER}
    assert study_runtime(local, [])["studies"] == {"default": DEFAULT}


def test_a_template_without_the_images_volume_is_used_as_it_is(tmp_path):
    """Job pods that cannot attach the shared volume stream their studies in every study."""
    local, net_dir = _kit(tmp_path, images_volume=False)

    entry = study_runtime(local, [PROJECT])["studies"][PROJECT]

    assert entry["pod_template"] == yaml.safe_load((local / POD_TEMPLATE).read_text())
    assert list(net_dir.iterdir()) == []


def test_a_default_study_without_a_template_gives_the_image_alone(tmp_path):
    local, _ = _kit(tmp_path)
    default = {"container": {"image": IMAGE}}
    (local / STUDY_RUNTIME_DEFAULT).write_text(json.dumps({"format_version": 2, "studies": {"default": default}}))

    assert study_runtime(local, [PROJECT])["studies"][PROJECT] == default


@pytest.mark.parametrize("study", ["default", "../net-2", PROJECT.upper(), PROJECT.replace("-", "")])
def test_an_id_that_is_not_a_project_is_refused_and_nothing_is_created(tmp_path, study, capsys):
    local, net_dir = _kit(tmp_path)

    assert main([str(local), PROJECT, study]) == 1

    captured = capsys.readouterr()
    assert study in captured.err
    assert captured.out == ""
    assert list(net_dir.iterdir()) == []


def test_the_command_prints_the_file(tmp_path, capsys):
    local, _ = _kit(tmp_path)

    assert main([str(local), PROJECT]) == 0

    assert json.loads(capsys.readouterr().out) == study_runtime(local, [PROJECT])


@pytest.mark.filterwarnings("ignore:'flip.nvflare.project_studies' found in sys.modules:RuntimeWarning")
def test_the_module_runs_as_the_chart_invokes_it(tmp_path, capsys):
    """``python -m flip.nvflare.project_studies``, as ``make -C trust/deploy/helm sync-project-studies`` runs it.

    No images volume: the re-run module reads the real ``FlipConstants``, not the fixture's.
    """
    local, _ = _kit(tmp_path, images_volume=False)

    with patch("sys.argv", ["project_studies", str(local), PROJECT]), pytest.raises(SystemExit) as exited:
        runpy.run_module("flip.nvflare.project_studies", run_name="__main__")

    assert exited.value.code == 0
    assert PROJECT in json.loads(capsys.readouterr().out)["studies"]


def test_a_kit_that_was_not_switched_is_refused(tmp_path, capsys):
    assert main([str(tmp_path), PROJECT]) == 1
    assert "cannot build study_runtime.yaml" in capsys.readouterr().err


def test_only_a_lowercase_uuid_names_a_project():
    assert is_project(PROJECT)
    assert not is_project(PROJECT.replace("-", ""))
    assert not is_project(7)
