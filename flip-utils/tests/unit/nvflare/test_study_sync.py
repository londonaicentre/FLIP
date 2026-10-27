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

"""The study-sync sidecar: one NVFLARE study entry per staged project (FLIP#1678)."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from flip.nvflare import study_sync
from flip.nvflare.study_sync import SyncConfig, staged_projects, study_entry, sync_once

P1 = "8b2e9d6e-5a53-4f2e-9c37-2c8f4f0f2d11"
P2 = "16fd2706-8baf-433b-82eb-8c7fada847da"
MODEL = "1f0b9d1c-7d0a-4c9e-9f49-0b3a3f6f1a22"
XNAT_ID = "a1111111-1111-4111-8111-111111111111"
IMAGE = "ghcr.io/londonaicentre/flare-fl-client:stag"
COHORT_PVC = "trust-release-flip-trust-cohort-data"
XNAT_PVC = "trust-release-flip-trust-xnat-web"

# What the chart renders as the job-pod template (fl-client-job-pods.yaml), trimmed to what the sync must keep.
TEMPLATE = """
apiVersion: v1
kind: Pod
metadata:
  labels:
    app.kubernetes.io/component: fl-job
spec:
  automountServiceAccountToken: false
  containers:
    - name: nvflare_job
      env:
        - name: LOG_LEVEL
          value: INFO
      volumeMounts:
        - name: images
          mountPath: /app/data/images/net-1
          subPath: net-1
  volumes:
    - name: images
      emptyDir: {}
"""


def _stage(cohort_data, project, model=MODEL):
    (cohort_data / project / model).mkdir(parents=True)


@pytest.fixture
def cfg(tmp_path):
    local = tmp_path / "local"
    (local / "pod_specs").mkdir(parents=True)
    (local / "pod_specs" / "job-pod.yaml").write_text(TEMPLATE)
    cohort_data = tmp_path / "cohort-data"
    cohort_data.mkdir()
    return SyncConfig(
        local_dir=local,
        job_image=IMAGE,
        cohort_data_dir=cohort_data,
        cohort_data_pvc=COHORT_PVC,
        xnat_archive_pvc=XNAT_PVC,
        xnat_gid=1001,
        imaging_api_url="http://imaging-api:8000",
        headers={"X-Trust-Internal-Service-Key": "k"},
        interval=2.0,
    )


def _runtime(cfg):
    return json.loads((cfg.local_dir / "study_runtime.yaml").read_text())


def _imaging_api(projects, experiments):
    """Stand in for imaging-api's GET /projects/ and GET /projects/{ID}/experiments."""

    def get(url, headers=None, timeout=None):
        response = MagicMock()
        response.raise_for_status.return_value = None
        if url.endswith("/projects/"):
            response.json.return_value = projects
        else:
            response.json.return_value = experiments.get(url.rstrip("/").split("/")[-2], [])
        return response

    return get


# --- which projects are staged ---------------------------------------------------------------------


def test_staged_projects_needs_a_non_hidden_model_dir(cfg):
    # Review Focus 4: a project with only work directories is still being staged, not staged.
    (cfg.cohort_data_dir / P1).mkdir()
    (cfg.cohort_data_dir / P1 / f".tmp-{MODEL}-abc").mkdir()
    (cfg.cohort_data_dir / P1 / f".del-{MODEL}-def").mkdir()
    assert staged_projects(cfg.cohort_data_dir) == []

    (cfg.cohort_data_dir / P1 / MODEL).mkdir()
    assert staged_projects(cfg.cohort_data_dir) == [P1]


@pytest.mark.parametrize("name", ["not-a-uuid", P1.upper(), f".tmp-{P1}", "default"])
def test_staged_projects_never_turns_an_unexpected_name_into_a_study(cfg, name):
    _stage(cfg.cohort_data_dir, name)

    assert staged_projects(cfg.cohort_data_dir) == []


# --- what an entry gives a project's job pods ------------------------------------------------------


def _entry(cfg, xnat_id=None):
    import yaml

    return study_entry(IMAGE, yaml.safe_load(TEMPLATE), P1, COHORT_PVC, XNAT_PVC, xnat_id, 1001)


def _main(spec):
    return next(c for c in spec["containers"] if c["name"] == "nvflare_job")


def test_entry_mounts_cohort_data_read_only_at_the_project_path(cfg):
    spec = _entry(cfg)["pod_template"]["spec"]

    assert {"name": "flip-cohort-data", "persistentVolumeClaim": {"claimName": COHORT_PVC, "readOnly": True}} in spec[
        "volumes"
    ]
    assert {
        "name": "flip-cohort-data",
        "mountPath": f"/data/{P1}/cohort",
        "subPath": P1,
        "readOnly": True,
    } in _main(spec)["volumeMounts"]


def test_entry_adds_the_xnat_archive_only_with_an_xnat_id(cfg):
    spec = _entry(cfg, xnat_id=XNAT_ID)["pod_template"]["spec"]

    assert {"name": "flip-xnat-archive", "persistentVolumeClaim": {"claimName": XNAT_PVC, "readOnly": True}} in spec[
        "volumes"
    ]
    assert {
        "name": "flip-xnat-archive",
        "mountPath": f"/data/{P1}/imaging",
        "subPath": f"archive/{XNAT_ID}",
        "readOnly": True,
    } in _main(spec)["volumeMounts"]
    assert "flip-xnat-archive" not in {v["name"] for v in _entry(cfg)["pod_template"]["spec"]["volumes"]}


def test_entry_sets_the_xnat_group_and_never_fsgroup(cfg):
    security = _entry(cfg, xnat_id=XNAT_ID)["pod_template"]["spec"]["securityContext"]

    assert security["supplementalGroups"] == [1001]
    assert "fsGroup" not in security


def test_entry_keeps_the_template_and_copies_the_image(cfg):
    entry = _entry(cfg)
    spec = entry["pod_template"]["spec"]

    assert entry["container"] == {"image": IMAGE}
    assert spec["automountServiceAccountToken"] is False
    assert _main(spec)["env"] == [{"name": "LOG_LEVEL", "value": "INFO"}]
    assert {"name": "images", "emptyDir": {}} in spec["volumes"]


# --- the sync -------------------------------------------------------------------------------------


def test_sync_writes_the_default_study_plus_each_staged_project(cfg):
    _stage(cfg.cohort_data_dir, P1)
    _stage(cfg.cohort_data_dir, P2)

    with patch("flip.nvflare.study_sync.requests.get", side_effect=_imaging_api([], {})):
        sync_once(cfg, {})

    runtime = _runtime(cfg)
    assert runtime["format_version"] == 2
    assert set(runtime["studies"]) == {"default", P1, P2}
    assert runtime["studies"]["default"] == {"container": {"image": IMAGE}, "pod_template": "pod_specs/job-pod.yaml"}


def test_sync_gives_an_imaging_project_its_archive(cfg):
    _stage(cfg.cohort_data_dir, P1)
    projects = [{"ID": XNAT_ID, "secondary_ID": P1}, {"ID": "other", "secondary_ID": P2}]

    with patch("flip.nvflare.study_sync.requests.get", side_effect=_imaging_api(projects, {XNAT_ID: [{"ID": "e"}]})):
        archives = sync_once(cfg, {})

    assert archives == {P1: XNAT_ID}
    mounts = _main(_runtime(cfg)["studies"][P1]["pod_template"]["spec"])["volumeMounts"]
    assert f"archive/{XNAT_ID}" in {m.get("subPath") for m in mounts}


def test_no_imaging_mount_without_archived_experiments(cfg):
    # The kubelet would create a missing subPath as a root-owned archive/<id> inside XNAT's own archive.
    _stage(cfg.cohort_data_dir, P1)
    projects = [{"ID": XNAT_ID, "secondary_ID": P1}]

    with patch("flip.nvflare.study_sync.requests.get", side_effect=_imaging_api(projects, {XNAT_ID: []})):
        archives = sync_once(cfg, {})

    assert archives == {}
    volumes = {v["name"] for v in _runtime(cfg)["studies"][P1]["pod_template"]["spec"]["volumes"]}
    assert "flip-xnat-archive" not in volumes


def test_sync_asks_imaging_api_only_about_staged_projects(cfg):
    _stage(cfg.cohort_data_dir, P1)
    projects = [{"ID": XNAT_ID, "secondary_ID": P1}, {"ID": "unstaged-xnat", "secondary_ID": P2}]
    get = MagicMock(side_effect=_imaging_api(projects, {XNAT_ID: [{"ID": "e"}]}))

    with patch("flip.nvflare.study_sync.requests.get", get):
        sync_once(cfg, {})

    asked = [c.args[0] for c in get.call_args_list]
    assert asked == ["http://imaging-api:8000/projects/", f"http://imaging-api:8000/projects/{XNAT_ID}/experiments"]


def test_sync_keeps_the_last_archives_when_imaging_api_is_unreachable(cfg):
    # Review Focus 5: an imaging-api outage never drops a running project's imaging mount, nor empties the file.
    _stage(cfg.cohort_data_dir, P1)

    with patch("flip.nvflare.study_sync.requests.get", side_effect=requests.ConnectionError("down")):
        archives = sync_once(cfg, {P1: XNAT_ID})

    assert archives == {P1: XNAT_ID}
    mounts = _main(_runtime(cfg)["studies"][P1]["pod_template"]["spec"])["volumeMounts"]
    assert f"archive/{XNAT_ID}" in {m.get("subPath") for m in mounts}


def test_sync_does_not_rewrite_unchanged_content(cfg):
    _stage(cfg.cohort_data_dir, P1)
    with patch("flip.nvflare.study_sync.requests.get", side_effect=_imaging_api([], {})):
        sync_once(cfg, {})
        path = cfg.local_dir / "study_runtime.yaml"
        os.utime(path, (1, 1))

        sync_once(cfg, {})

    assert path.stat().st_mtime == 1


def test_sync_skips_a_tick_until_the_pod_template_exists(cfg):
    # The sidecar can start before the fl-client has copied the chart's template into local/.
    (cfg.local_dir / "pod_specs" / "job-pod.yaml").unlink()
    _stage(cfg.cohort_data_dir, P1)

    assert sync_once(cfg, {P1: XNAT_ID}) == {P1: XNAT_ID}
    assert not (cfg.local_dir / "study_runtime.yaml").exists()


def test_sync_without_the_archive_mount_never_asks_imaging_api(cfg):
    _stage(cfg.cohort_data_dir, P1)
    cfg = SyncConfig(**{**cfg.__dict__, "xnat_archive_pvc": None})

    with patch("flip.nvflare.study_sync.requests.get") as get:
        sync_once(cfg, {})

    get.assert_not_called()
    assert P1 in _runtime(cfg)["studies"]


def test_main_once_syncs_from_the_environment(tmp_path, cfg):
    _stage(cfg.cohort_data_dir, P1)
    env = {
        "FL_K8S_JOB_IMAGE": IMAGE,
        "FL_STUDY_COHORT_DATA_DIR": str(cfg.cohort_data_dir),
        "FL_STUDY_COHORT_DATA_PVC": COHORT_PVC,
        "FL_STUDY_XNAT_ARCHIVE_PVC": "",
    }

    assert study_sync.main([str(cfg.local_dir), "--once"], env=env) == 0

    assert P1 in _runtime(cfg)["studies"]


def test_main_refuses_a_missing_setting(cfg, capsys):
    assert study_sync.main([str(cfg.local_dir), "--once"], env={}) == 1
    assert "FL_K8S_JOB_IMAGE" in capsys.readouterr().err
