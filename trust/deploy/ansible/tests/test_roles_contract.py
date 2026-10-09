# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Static guard: the shared roles' input contract (FLIP#1213 review).

The roles are composed by onprem.yml, deploy/providers/AWS/site.yml and (next) azure.yml.
A missing or misspelt input must stop the play, not fall back to a value that silently
re-owns the net-N dirs for the wrong backend or stages no FL kit at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).resolve().parent.parent
ROLES_DIR = ANSIBLE_DIR / "roles"


def _load(path: Path):
    return yaml.safe_load(path.read_text())


def _first_task(role: str) -> dict:
    tasks = _load(ROLES_DIR / role / "tasks" / "main.yml") or []
    assert tasks, f"{role} has no tasks"
    return tasks[0]


def _assert_conditions(task: dict) -> str:
    body = task.get("assert") or task.get("ansible.builtin.assert")
    assert body, f"first task is not an assert: {task.get('name')}"
    that = body.get("that")
    return " ".join(that if isinstance(that, list) else [that])


@pytest.mark.parametrize("role", ["flip_fl_kit", "flip_trust_dirs"])
def test_fl_backend_has_no_silent_default(role: str) -> None:
    defaults = _load(ROLES_DIR / role / "defaults" / "main.yml") or {}
    assert "fl_backend" not in defaults, f"{role} must not default fl_backend: the play has to say which backend"


def test_fl_kit_role_checks_its_inputs_first() -> None:
    conditions = _assert_conditions(_first_task("flip_fl_kit"))
    assert "fl_kit_source in" in conditions, "fl_kit_source must be one of s3/precreate/none"
    assert "fl_backend in" in conditions, "fl_backend must be nvflare or flower"


def test_trust_dirs_role_checks_fl_backend_first() -> None:
    task = _first_task("flip_trust_dirs")
    assert "fl_backend in" in _assert_conditions(task)
    assert "flkit" in (task.get("tags") or []), "the check must run with the flkit-tagged tasks that read fl_backend"


def test_walker_skips_galaxy_roles_installed_beside_the_local_ones(tmp_path, monkeypatch) -> None:
    import test_onprem_playbook as walker

    galaxy = tmp_path / "geerlingguy.docker" / "tasks"
    galaxy.mkdir(parents=True)
    (galaxy / "main.yml").write_text(
        "- name: Ensure docker users are added to the docker group.\n  user:\n    name: ubuntu\n    groups: docker\n"
    )
    monkeypatch.setattr(walker, "ROLES_DIR", tmp_path)
    assert list(walker._role_tasks("geerlingguy.docker")) == [], "a galaxy role is not ours to walk"
