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

"""The FL server's FLIP site components, written where NVFLARE's parent process loads them (FLIP#1390)."""

import importlib
import json
import runpy
import sys

import pytest
from nvflare.apis.fl_constant import WorkspaceConstants
from nvflare.fuel.utils.dict_utils import augment

from flip.nvflare.server_site_config import PARENT_RESOURCES_FILE, write_server_site_config


def test_the_file_name_is_one_nvflare_loads_for_the_parent_process_only():
    """``*__p_resources.json`` in local/ is merged into the server parent's config, never into a job's."""
    import fnmatch

    assert fnmatch.fnmatch(PARENT_RESOURCES_FILE, WorkspaceConstants.PARENT_RESOURCE_FILE_NAME_PATTERN)
    assert not fnmatch.fnmatch(PARENT_RESOURCES_FILE, WorkspaceConstants.JOB_RESOURCE_FILE_NAME_PATTERN)


def test_it_registers_the_scheduling_reporter_and_every_component_path_imports(tmp_path):
    path = write_server_site_config(tmp_path)

    config = json.loads(path.read_text())
    paths = [c["path"] for c in config["components"]]
    assert "flip.nvflare.components.job_scheduling_reporter.JobSchedulingReporter" in paths
    for dotted in paths:
        module, _, name = dotted.rpartition(".")
        assert hasattr(importlib.import_module(module), name), dotted


def test_it_merges_into_the_provisioned_config_without_a_conflict(tmp_path):
    """NVFLARE augments the provisioned resources.json with this file: components append, and any other
    key both files set would be a conflict, so the file carries components only."""
    provisioned = {"format_version": 2, "components": [{"id": "job_scheduler", "path": "x.DefaultJobScheduler"}]}
    ours = json.loads(write_server_site_config(tmp_path).read_text())

    assert augment(to_dict=provisioned, from_dict=ours, from_override_to=False) == ""
    assert [c["id"] for c in provisioned["components"]][0] == "job_scheduler"
    assert len(provisioned["components"]) == 1 + len(ours["components"])
    assert set(ours) == {"components"}


def test_rewriting_is_idempotent(tmp_path):
    first = write_server_site_config(tmp_path).read_text()

    assert write_server_site_config(tmp_path).read_text() == first


def test_a_missing_local_dir_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_server_site_config(tmp_path / "absent")


def test_it_runs_as_the_entrypoint_script(tmp_path, monkeypatch):
    """The fl-server entrypoint runs ``python -m flip.nvflare.server_site_config /app/local``."""
    monkeypatch.setattr(sys, "argv", ["server_site_config", str(tmp_path)])
    monkeypatch.delitem(sys.modules, "flip.nvflare.server_site_config", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("flip.nvflare.server_site_config", run_name="__main__")

    assert exit_info.value.code == 0
    assert (tmp_path / PARENT_RESOURCES_FILE).is_file()
