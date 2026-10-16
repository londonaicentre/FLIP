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
re-owns the net-N dirs for the wrong backend or stages no FL kit at all. The same goes for
flip_observability_config's hand-kept file list: a file added under trust/observability
that the list misses never reaches a provisioned host.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ANSIBLE_DIR = Path(__file__).resolve().parent.parent
ROLES_DIR = ANSIBLE_DIR / "roles"
OBSERVABILITY_DIR = ANSIBLE_DIR.parent.parent / "observability"
# The trust/observability subtrees the Loki / Alloy / Grafana containers read; the rest of the
# tree (log_config/, the README) is Python and docs, never copied to a host.
OBSERVABILITY_SERVICES = ("alloy", "loki", "grafana")


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


def test_fl_kit_source_has_no_silent_default() -> None:
    """A play that omits fl_kit_source must stop, not stage nothing and report success."""
    defaults = _load(ROLES_DIR / "flip_fl_kit" / "defaults" / "main.yml") or {}
    assert "fl_kit_source" not in defaults, "flip_fl_kit must not default fl_kit_source: the play has to say it"


def test_fl_kit_role_checks_its_inputs_first() -> None:
    conditions = _assert_conditions(_first_task("flip_fl_kit"))
    assert "fl_kit_source is defined and fl_kit_source in" in conditions, (
        "fl_kit_source must be one of s3/precreate/none"
    )
    assert "fl_backend is defined and fl_backend in" in conditions, "fl_backend must be nvflare or flower"


def test_trust_dirs_role_checks_fl_backend_first() -> None:
    task = _first_task("flip_trust_dirs")
    assert "fl_backend in" in _assert_conditions(task)
    assert "flkit" in (task.get("tags") or []), "the check must run with the flkit-tagged tasks that read fl_backend"


@pytest.mark.parametrize(
    ("role", "path_var"),
    [("flip_fl_kit", "fl_kit_dir"), ("flip_observability_config", "observability_dest_dir")],
)
def test_host_paths_follow_flip_dir(role: str, path_var: str) -> None:
    """A play that relocates flip_dir must move the kit and config trees with the images and XNAT ones.

    A literal /opt/flip here splits the layout: the host provisions cleanly and the first
    up-trust mounts an empty directory.
    """
    defaults = _load(ROLES_DIR / role / "defaults" / "main.yml") or {}
    assert defaults.get("flip_dir") == "/opt/flip", f"{role} must default flip_dir like flip_trust_dirs does"
    path = str(defaults.get(path_var, ""))
    assert path.startswith("{{ flip_dir }}/"), f"{role}'s {path_var} must derive from flip_dir, not {path!r}"


def test_walker_skips_galaxy_roles_installed_beside_the_local_ones(tmp_path, monkeypatch) -> None:
    import test_onprem_playbook as walker

    galaxy = tmp_path / "geerlingguy.docker" / "tasks"
    galaxy.mkdir(parents=True)
    (galaxy / "main.yml").write_text(
        "- name: Ensure docker users are added to the docker group.\n  user:\n    name: ubuntu\n    groups: docker\n"
    )
    monkeypatch.setattr(walker, "ROLES_DIR", tmp_path)
    assert list(walker._role_tasks("geerlingguy.docker")) == [], "a galaxy role is not ours to walk"


def _observability_config_files() -> list[str]:
    defaults = _load(ROLES_DIR / "flip_observability_config" / "defaults" / "main.yml") or {}
    return defaults.get("observability_config_files") or []


def test_observability_role_copies_every_config_file_in_the_tree() -> None:
    """The role copies a hand-kept list, so a file added under trust/observability must be listed too.

    Grafana reads its whole provisioning dir, so a missing dashboard or datasource is a panel that
    silently never appears on a provisioned host, not a provisioning failure.
    """
    on_disk = {
        str(path.relative_to(OBSERVABILITY_DIR))
        for service in OBSERVABILITY_SERVICES
        for path in (OBSERVABILITY_DIR / service).rglob("*")
        if path.is_file() and not path.name.startswith(".")
    }
    assert on_disk, f"no config files found under {OBSERVABILITY_DIR} — this guard has drifted"
    assert set(_observability_config_files()) == on_disk, (
        "flip_observability_config's observability_config_files must list exactly the files under "
        f"trust/observability/{{{','.join(OBSERVABILITY_SERVICES)}}}"
    )


def test_observability_role_creates_the_dir_of_every_file_it_copies() -> None:
    tasks = _load(ROLES_DIR / "flip_observability_config" / "tasks" / "main.yml") or []
    [dirs_task] = [task for task in tasks if task.get("name") == "create observability config directories"]
    created = set(dirs_task.get("loop") or [])
    missing = {str(Path(f).parent) for f in _observability_config_files()} - created
    assert not missing, f"the copy task would write into directories the role never creates: {sorted(missing)}"


def test_trust_dirs_role_creates_the_cohort_membership_stores() -> None:
    """Every play composing flip_trust_dirs gets the FLIP#857 stores, owned by data-access-api's uid.

    The stores moved into this role from the plays; one dropped in a later refactor is a host that
    provisions cleanly and then fails every project approval's freeze, refusing all row-level data.
    """
    defaults = _load(ROLES_DIR / "flip_trust_dirs" / "defaults" / "main.yml") or {}
    assert defaults.get("flip_cohort_snapshot_dirs") == [
        "{{ flip_dir }}/cohort-snapshots/trust-1",
        "{{ flip_dir }}/cohort-snapshots/trust-2",
    ], "the default stores must be the production compose defaults for slots 1 and 2, under flip_dir"
    tasks = _load(ROLES_DIR / "flip_trust_dirs" / "tasks" / "main.yml") or []
    [stores] = [task for task in tasks if "flip_cohort_snapshot_dirs" in str(task.get("loop", ""))]
    body = stores["file"]
    assert (body["owner"], body["group"], body["mode"]) == ("1000", "1000", "0750"), (
        "data-access-api runs as uid 1000 in its image and must be able to write its store"
    )
    assert "flkit" in (stores.get("tags") or []), "an EC2 --tags flkit re-stage must lay the stores down too"
