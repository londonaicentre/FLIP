#!/usr/bin/env python3
#
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

"""Regression tests for the on-premises trust Ansible playbook.

The on-prem trust host is reachable over plain SSH (unlike the AWS trust which
sits behind SSM Session Manager). Adding the SSH login user to the ``docker``
group would give that user root-equivalent access to the host (any docker group
member can mount ``/`` into a container and chroot in), so this test pins the
playbook to *not* configure that.
"""

from pathlib import Path

import pytest
import yaml
from jinja2 import Template

PLAYBOOK_PATH = Path(__file__).parent.parent / "site_local_trust.yml"

# The per-net images bind sources, which imaging-api AND the fl-client both write into.
NET_DIR_PATHS = {"{{ flip_dir }}/data/images/net-1", "{{ flip_dir }}/data/images/net-2"}


@pytest.fixture(scope="module")
def playbook() -> list[dict]:
    with PLAYBOOK_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _iter_tasks(playbook: list[dict]):
    """Yield every task in the playbook.

    Scope: inline ``tasks:`` of each play only. Values reached indirectly —
    ``vars_files:``, ``group_vars/``, ``host_vars/``, an inventory, or role
    defaults — are *not* resolved, and neither are ``pre_tasks``/``post_tasks``/
    ``handlers``/``roles:`` blocks. That is a complete match for the current
    single-play, single-file playbook, which uses none of them. If the playbook
    grows any of those, widen this walk (and the checks built on it) to match,
    or a ``docker_users`` / ``groups: docker`` value could be introduced without
    failing these tests.
    """
    for play in playbook:
        for task in play.get("tasks", []) or []:
            yield task


def test_docker_role_is_invoked(playbook: list[dict]) -> None:
    """The playbook must still install Docker via geerlingguy.docker."""
    docker_tasks = [
        task
        for task in _iter_tasks(playbook)
        if (task.get("include_role") or {}).get("name") == "geerlingguy.docker"
        or (task.get("import_role") or {}).get("name") == "geerlingguy.docker"
    ]
    assert docker_tasks, "Expected a task that includes the geerlingguy.docker role"


def test_login_user_not_added_to_docker_group(playbook: list[dict]) -> None:
    """The SSH login user must not be granted docker group membership.

    docker group membership = root on the host. Operators should run docker
    via sudo instead.
    """
    for task in _iter_tasks(playbook):
        task_vars = task.get("vars") or {}
        docker_users = task_vars.get("docker_users")
        assert not docker_users, (
            f"Task '{task.get('name', '<unnamed>')}' sets docker_users={docker_users!r}; "
            "this gives the listed user root-equivalent access."
        )

    for play in playbook:
        play_vars = play.get("vars") or {}
        docker_users = play_vars.get("docker_users")
        assert not docker_users, (
            f"Play '{play.get('name', '<unnamed>')}' sets docker_users={docker_users!r}; "
            "this gives the listed user root-equivalent access."
        )


def _user_module_grants_docker(task: dict) -> bool:
    """Return True if an ``ansible.builtin.user`` task grants docker group membership.

    ``groups`` may be a comma-separated string or a YAML list. ``append`` is
    irrelevant to the finding: with ``append: false`` (Ansible's default) the
    user's groups are *replaced* by the listed ones, which still grants docker
    membership.
    """
    user_module = task.get("user") or task.get("ansible.builtin.user") or {}
    if not isinstance(user_module, dict):
        return False
    groups = user_module.get("groups") or []
    if isinstance(groups, str):
        groups = groups.split(",")
    if not isinstance(groups, list):
        return False
    return "docker" in (str(group).strip() for group in groups)


def test_no_usermod_adding_to_docker_group(playbook: list[dict]) -> None:
    """No ad-hoc shell/command/user task may add a user to the docker group."""
    suspect_substrings = ("usermod -aG docker", "usermod --append --groups docker", "gpasswd -a")
    for task in _iter_tasks(playbook):
        for module in ("shell", "command", "ansible.builtin.shell", "ansible.builtin.command"):
            cmd = task.get(module)
            if isinstance(cmd, str):
                for needle in suspect_substrings:
                    assert needle not in cmd, (
                        f"Task '{task.get('name', '<unnamed>')}' appears to add a user to the docker group "
                        f"via {module}: {cmd!r}"
                    )
        if _user_module_grants_docker(task):
            pytest.fail(
                f"Task '{task.get('name', '<unnamed>')}' grants docker group membership via the user module."
            )


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        pytest.param({"user": {"groups": "docker", "append": True}}, True, id="comma-string-append"),
        pytest.param({"user": {"groups": "sudo,docker"}}, True, id="comma-string-no-append"),
        pytest.param({"user": {"groups": "sudo, docker"}}, True, id="comma-string-with-space"),
        pytest.param({"user": {"groups": ["docker"]}}, True, id="yaml-list"),
        pytest.param({"user": {"groups": ["adm", "docker"], "append": False}}, True, id="yaml-list-replace"),
        pytest.param({"ansible.builtin.user": {"groups": "docker"}}, True, id="fqcn-module"),
        pytest.param({"user": {"groups": "sudo"}}, False, id="other-group-string"),
        pytest.param({"user": {"groups": ["adm", "sudo"]}}, False, id="other-group-list"),
        pytest.param({"user": {"name": "ubuntu"}}, False, id="no-groups-key"),
        pytest.param({"shell": "echo docker"}, False, id="no-user-module"),
    ],
)
def test_user_module_docker_grant_detection(task: dict, expected: bool) -> None:
    """The docker-grant checker must catch every ``groups`` form Ansible accepts."""
    assert _user_module_grants_docker(task) is expected


def _net_dir_tasks(playbook: list[dict]) -> list[tuple[dict, dict]]:
    """Return every ``file`` task whose loop creates one of the per-net images dirs."""
    matches = []
    for task in _iter_tasks(playbook):
        file_module = task.get("file") or task.get("ansible.builtin.file") or {}
        if not isinstance(file_module, dict):
            continue
        if NET_DIR_PATHS & set(task.get("loop") or []):
            matches.append((task, file_module))
    return matches


def test_fl_backend_has_a_playbook_default(playbook: list[dict]) -> None:
    """A bare ``ansible-playbook site_local_trust.yml`` must not fail on an undefined ``fl_backend``.

    The net-dir task below templates on it, so without a play-level default every direct
    (non-Makefile) run of the playbook would abort with an undefined-variable error.
    """
    defaults = [(play.get("vars") or {}).get("fl_backend") for play in playbook]
    assert any(default for default in defaults), "No play sets a default for fl_backend"


def test_net_dirs_are_writable_by_the_fl_client(playbook: list[dict]) -> None:
    """The per-net images dirs must be group-writable by the Flower client (uid/gid 49999).

    imaging-api (uid 1000) owns them on every backend, but the fl-client writes inside them too
    (``flip.add_resource`` staging). NVFLARE's client shares
    imaging-api's uid so owner write is enough; Flower's is built on upstream ``flwr/base`` and
    runs as ``app`` (uid/gid 49999) with no supplementary group, so a ``ubuntu:ubuntu`` ``0755``
    net dir leaves it as "other" and ``add_resource`` fails with EACCES. Mirrors the K8s chart's
    ``images-init`` and ``trust/Makefile``'s ``$(ensure_net_dirs)``.
    """
    matches = _net_dir_tasks(playbook)
    assert len(matches) == 1, f"Expected exactly one task creating {sorted(NET_DIR_PATHS)}, found {len(matches)}"
    task, file_module = matches[0]

    assert NET_DIR_PATHS <= set(task["loop"]), f"Task '{task.get('name')}' does not create every net dir"
    assert file_module.get("owner") == "ubuntu", "imaging-api's uid must own the net dirs on every backend"

    for backend, expected_group, expected_mode in (("flower", "49999", "0775"), ("nvflare", "ubuntu", "0755")):
        group = Template(str(file_module.get("group"))).render(fl_backend=backend)
        mode = Template(str(file_module.get("mode"))).render(fl_backend=backend)
        assert group == expected_group, f"fl_backend={backend} must give the net dirs group {expected_group}"
        assert mode == expected_mode, f"fl_backend={backend} must give the net dirs mode {expected_mode}"
