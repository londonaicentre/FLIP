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

PLAYBOOK_PATH = Path(__file__).parent / "site_local_trust.yml"


@pytest.fixture(scope="module")
def playbook() -> list[dict]:
    with PLAYBOOK_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _iter_tasks(playbook: list[dict]):
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
        assert (
            not docker_users
        ), f"Task '{task.get('name', '<unnamed>')}' sets docker_users={docker_users!r}; this gives the listed user root-equivalent access."

    for play in playbook:
        play_vars = play.get("vars") or {}
        docker_users = play_vars.get("docker_users")
        assert (
            not docker_users
        ), f"Play '{play.get('name', '<unnamed>')}' sets docker_users={docker_users!r}; this gives the listed user root-equivalent access."


def test_no_usermod_adding_to_docker_group(playbook: list[dict]) -> None:
    """No ad-hoc shell/command/user task may add a user to the docker group."""
    suspect_substrings = ("usermod -aG docker", "usermod --append --groups docker", "gpasswd -a")
    for task in _iter_tasks(playbook):
        for module in ("shell", "command", "ansible.builtin.shell", "ansible.builtin.command"):
            cmd = task.get(module)
            if isinstance(cmd, str):
                for needle in suspect_substrings:
                    assert needle not in cmd, (
                        f"Task '{task.get('name', '<unnamed>')}' appears to add a user to the docker group via {module}: {cmd!r}"
                    )
        user_module = task.get("user") or task.get("ansible.builtin.user") or {}
        if isinstance(user_module, dict):
            groups = user_module.get("groups") or ""
            append = user_module.get("append")
            if isinstance(groups, str) and "docker" in groups.split(",") and append:
                pytest.fail(
                    f"Task '{task.get('name', '<unnamed>')}' appends user to the docker group via the user module."
                )
