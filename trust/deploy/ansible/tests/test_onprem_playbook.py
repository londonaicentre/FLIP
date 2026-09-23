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

The play is composed from the roles in ``../roles`` (shared with the EC2 play),
so every walk here expands those roles: a ``docker_users`` grant or a wrong
net-dir ownership introduced inside a role must fail these tests exactly as one
written inline would.
"""

from pathlib import Path

import pytest
import yaml
from jinja2 import Template

ANSIBLE_DIR = Path(__file__).parent.parent
PLAYBOOK_PATH = ANSIBLE_DIR / "onprem.yml"
ROLES_DIR = ANSIBLE_DIR / "roles"

# The per-net images bind sources, which imaging-api AND the fl-client both write into.
NET_DIR_TASK = "create per-net images bind-mount directories"
EXPECTED_IMAGES_BASE_DIRS = ["{{ flip_dir }}/data/images"]
EXPECTED_NET_IDS = ["net-1", "net-2"]


def _load(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def playbook() -> list[dict]:
    return _load(PLAYBOOK_PATH)


def _role_name(entry) -> str:
    """A ``roles:`` entry is a bare name or a dict with ``role``/``name``."""
    if isinstance(entry, dict):
        return entry.get("role") or entry.get("name") or ""
    return str(entry)


def _role_defaults(name: str) -> dict:
    path = ROLES_DIR / name / "defaults" / "main.yml"
    return (_load(path) or {}) if path.is_file() else {}


def _role_tasks(name: str, seen: set[str] | None = None):
    """Yield every task of a local role, following ``import_tasks``/``include_tasks`` inside it.

    Roles that are not in ``../roles`` (galaxy roles such as ``geerlingguy.docker``)
    yield nothing; the task that pulls them in is still yielded by the caller.
    """
    seen = seen if seen is not None else set()
    tasks_dir = ROLES_DIR / name / "tasks"
    main = tasks_dir / "main.yml"
    if not main.is_file():
        return
    yield from _tasks_in_file(main, tasks_dir, seen)


def _tasks_in_file(path: Path, tasks_dir: Path, seen: set[str]):
    for task in _load(path) or []:
        yield task
        for key in ("import_tasks", "include_tasks"):
            target = task.get(key)
            if isinstance(target, dict):
                target = target.get("file")
            if target:
                nested = tasks_dir / target
                if str(nested) not in seen:
                    seen.add(str(nested))
                    yield from _tasks_in_file(nested, tasks_dir, seen)
        for key in ("import_role", "include_role"):
            role = (task.get(key) or {}).get("name")
            if role and role not in seen:
                seen.add(role)
                yield from _role_tasks(role, seen)


def _iter_tasks(playbook: list[dict]):
    """Yield every task the playbook runs: inline ``tasks:``, ``pre_tasks``/``post_tasks``,
    and the tasks of every local role it composes (recursively through imports).
    ``handlers``, ``vars_files``, inventories and galaxy roles are still out of scope.
    """
    seen: set[str] = set()
    for play in playbook:
        for section in ("pre_tasks", "tasks", "post_tasks"):
            for task in play.get(section, []) or []:
                yield task
                for key in ("import_role", "include_role"):
                    role = (task.get(key) or {}).get("name")
                    if role:
                        yield from _role_tasks(role, seen)
        for entry in play.get("roles", []) or []:
            yield from _role_tasks(_role_name(entry), seen)


def _play_vars(playbook: list[dict]) -> dict:
    merged: dict = {}
    for play in playbook:
        merged.update(play.get("vars") or {})
    return merged


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
    via sudo instead. The grant travels as ``docker_users``, which the shared
    ``flip_docker`` role templates from ``flip_docker_users`` — so the check
    renders that template with the on-prem play's own value (or the role default).
    """
    play_vars = _play_vars(playbook)
    assert not play_vars.get("docker_users"), "the play sets docker_users directly"
    assert not play_vars.get("flip_docker_users"), (
        f"the play sets flip_docker_users={play_vars.get('flip_docker_users')!r}; "
        "this gives the listed user root-equivalent access."
    )
    context = {**_role_defaults("flip_docker"), **play_vars}

    for task in _iter_tasks(playbook):
        docker_users = (task.get("vars") or {}).get("docker_users")
        if docker_users is None:
            continue
        rendered = Template(str(docker_users)).render(**context).strip()
        assert rendered in ("", "[]", "None"), (
            f"Task '{task.get('name', '<unnamed>')}' sets docker_users={rendered!r}; "
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


def test_roles_walk_reaches_the_shared_roles(playbook: list[dict]) -> None:
    """The walk must see inside the composed roles, or every check above is vacuous."""
    names = {task.get("name") for task in _iter_tasks(playbook)}
    assert NET_DIR_TASK in names, "the roles walk did not reach flip_trust_dirs — the tests are checking nothing"
    assert "Install Docker" in names, "the roles walk did not reach flip_docker"


def test_fl_backend_has_a_playbook_default(playbook: list[dict]) -> None:
    """A bare ``ansible-playbook onprem.yml`` must not fail on an undefined ``fl_backend``.

    The net-dir task templates on it, so without a play-level default every direct
    (non-Makefile) run of the playbook would abort with an undefined-variable error.
    """
    defaults = [(play.get("vars") or {}).get("fl_backend") for play in playbook]
    assert any(default for default in defaults), "No play sets a default for fl_backend"


def test_onprem_keeps_the_single_images_tree(playbook: list[dict]) -> None:
    """On-prem provisions one images tree, ``/opt/flip/data/images``, with net-1 and net-2 under it.

    The EC2 play overrides ``flip_images_base_dirs`` with one tree per FL kit slot; the
    on-prem play must keep the role default, which is what ``trust/Makefile``'s
    ``$(ensure_net_dirs)`` and the on-prem kit file expect.
    """
    play_vars = _play_vars(playbook)
    assert "flip_images_base_dirs" not in play_vars, "on-prem must not override the images tree layout"
    assert "flip_net_ids" not in play_vars, "on-prem must not override the net ids"
    defaults = _role_defaults("flip_trust_dirs")
    assert defaults.get("flip_images_base_dirs") == EXPECTED_IMAGES_BASE_DIRS
    assert defaults.get("flip_net_ids") == EXPECTED_NET_IDS


def test_net_dirs_are_writable_by_the_fl_client(playbook: list[dict]) -> None:
    """The per-net images dirs must be group-writable by the Flower client (uid/gid 49999).

    imaging-api (uid 1000) owns them on every backend, but the fl-client writes inside them too
    (``flip.add_resource`` staging, NVFLARE ``CleanupImages``). NVFLARE's client shares
    imaging-api's uid so owner write is enough; Flower's is built on upstream ``flwr/base`` and
    runs as ``app`` (uid/gid 49999) with no supplementary group, so a ``ubuntu:ubuntu`` ``0755``
    net dir leaves it as "other" and ``add_resource`` fails with EACCES. Mirrors the K8s chart's
    ``images-init`` and ``trust/Makefile``'s ``$(ensure_net_dirs)``.
    """
    matches = [task for task in _iter_tasks(playbook) if task.get("name") == NET_DIR_TASK]
    assert len(matches) == 1, f"Expected exactly one task named {NET_DIR_TASK!r}, found {len(matches)}"
    task = matches[0]
    file_module = task.get("file") or task.get("ansible.builtin.file") or {}
    assert isinstance(file_module, dict), "the net-dir task no longer uses the file module"
    assert task.get("loop") == "{{ flip_net_dirs }}", "the net-dir task no longer loops over flip_net_dirs"

    context = {**_role_defaults("flip_trust_dirs"), **_play_vars(playbook)}
    owner = Template(str(file_module.get("owner"))).render(**context)
    assert owner == "ubuntu", "imaging-api's uid must own the net dirs on every backend"

    for backend, expected_group, expected_mode in (("flower", "49999", "0775"), ("nvflare", "ubuntu", "0755")):
        rendered = {**context, "fl_backend": backend}
        group = Template(str(file_module.get("group"))).render(**rendered)
        mode = Template(str(file_module.get("mode"))).render(**rendered)
        assert group == expected_group, f"fl_backend={backend} must give the net dirs group {expected_group}"
        assert mode == expected_mode, f"fl_backend={backend} must give the net dirs mode {expected_mode}"


def test_onprem_play_loads_no_mock_data(playbook: list[dict]) -> None:
    """Provisioning never puts data on a trust host: the stores start empty and ``make -C trust
    up-trust`` seeds them from the canonical dataset at bring-up (FLIP#1187). The snapshot
    restore roles are retired, and the vocabulary role needs the hub's S3 bucket via an instance
    role an on-prem host does not have."""
    names = [_role_name(entry) for play in playbook for entry in (play.get("roles", []) or [])]
    assert names, "no roles found in the on-prem play — this guard has drifted"
    for data_role in ("flip_omop_restore", "flip_orthanc_restore", "flip_omop_vocab"):
        assert data_role not in names, f"the on-prem play composes {data_role}"
    assert not (ROLES_DIR / "flip_omop_restore").exists()
    assert not (ROLES_DIR / "flip_orthanc_restore").exists()
