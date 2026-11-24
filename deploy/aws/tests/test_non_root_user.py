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

"""Every hub container runs as a non-root user, its image's own (Security Hub ECS.20, FLIP#1796).

ECS.20 fails a task definition when a container definition sets no ``user``, or a root one. The
uid:gid must also be the one the image already runs as: that user owns the image's ``VOLUME``s,
which is what makes Fargate's scratch volumes writable (FLIP#1795, test_readonly_root_filesystem.py).
So each container's ``user`` is checked, per FL backend, against the Dockerfile of the image it runs.
"""

import re
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DIR.parents[2]
SOURCES = {
    path.relative_to(AWS_DIR).as_posix(): strip_comments(path.read_text())
    for path in sorted(AWS_DIR.rglob("*.tf"))
    if ".terraform" not in path.parts
}
TASK_DEFINITION = re.compile(r'resource "aws_ecs_task_definition" "(\w+)"')
CONTAINER_NAME = re.compile(r'^\s*name\s*=\s*"([\w-]+)"\s*\n\s*image\s*=', re.M)
BACKENDS = ("nvflare", "flower")

SUPERLINK = "fl-services/flower/superlink/Dockerfile"
# flwr/base's `app`, which the SuperLink image keeps; flwr/base is upstream, so it is restated here.
FLWR_BASE_USER = "49999:49999"
# The EFS provisioning task's amazon/aws-cli has no user of its own: it runs as the access points' one.
EFS_ACCESS_POINT_USER = "efs.tf posix_user"
# The in-repo base an image builds FROM, where its user is created.
BASES = {
    "fl-services/nvflare/fl-server/Dockerfile": "fl-services/nvflare/fl-base/Dockerfile",
    SUPERLINK: "fl-services/flower/fl-base/Dockerfile",
}
# (task definition, container) → the image it runs on each backend; a backend without it is absent.
CONTAINERS = {
    ("flip_api", "flip-api"): dict.fromkeys(BACKENDS, "flip-api/Dockerfile"),
    ("fl_api_net_1", "fl-api-net-1"): {
        "nvflare": "fl-services/nvflare/fl-api-base/Dockerfile",
        "flower": "fl-services/flower/fl-api-flower/Dockerfile",
    },
    ("fl_server_net_1", "fl-server-net-1"): {
        "nvflare": "fl-services/nvflare/fl-server/Dockerfile",
        "flower": SUPERLINK,
    },
    ("fl_server_net_1", "fleet-proxy"): {"flower": "fl-services/flower/fleet-proxy/Dockerfile"},
    ("flower_register_supernode_keys", "register-supernode-keys"): {"flower": SUPERLINK},
    ("efs_provision", "provision-efs-certs"): dict.fromkeys(BACKENDS, EFS_ACCESS_POINT_USER),
}


def _containers() -> dict[tuple[str, str], str]:
    """(task definition, container) → the container definition's ``user`` expression ("" if none)."""
    users = {}
    for source in SOURCES.values():
        for task in TASK_DEFINITION.findall(source):
            body = hcl_block(source, f'resource "aws_ecs_task_definition" "{task}"')
            names = list(CONTAINER_NAME.finditer(body))
            assert len(names) == len(re.findall(r"^\s*image\s*=", body, re.M)), f"unnamed container in {task}"
            for name in names:
                # The container's object: from the brace that opens it, before its name, to its match.
                container = hcl_block(body[body.rindex("{", 0, name.start()) :], "{")
                found = re.findall(r"^\s*user\s*=\s*(.+?)\s*$", container, re.M)
                assert len(found) <= 1, f"{task}/{name.group(1)} sets user twice"
                users[task, name.group(1)] = found[0] if found else ""
    return users


USERS = _containers()


def _user_by_backend(expression: str) -> dict[str, str]:
    literal = re.fullmatch(r'"([^"]+)"', expression)
    if literal:
        return dict.fromkeys(BACKENDS, literal.group(1))
    switch = re.fullmatch(r'var\.fl_backend == "flower" \? "([^"]+)" : "([^"]+)"', expression)
    if switch:
        return {"flower": switch.group(1), "nvflare": switch.group(2)}
    raise AssertionError(f"user = {expression!r}: not a form this test reads; extend _user_by_backend")


def _dockerfile(path: str) -> str:
    text = (REPO_ROOT / path).read_text()
    for name, value in re.findall(r"^ARG (\w+)=(\S*)$", text, re.M):
        text = text.replace(f"${{{name}}}", value)
    return text


def _image_user(image: str) -> str:
    """The uid:gid the image runs as, from its last ``USER`` and the ``useradd`` that created it."""
    if image == EFS_ACCESS_POINT_USER:
        posix = hcl_block(SOURCES["efs.tf"], "posix_user")
        return ":".join(re.search(rf"^\s*{key}\s*=\s*(\d+)$", posix, re.M).group(1) for key in ("uid", "gid"))
    text = "\n".join(_dockerfile(path) for path in (image, BASES.get(image)) if path)
    if image == SUPERLINK:
        assert not re.search(r"^USER ", text, re.M), "the SuperLink sets a USER of its own, not flwr/base's"
        return FLWR_BASE_USER
    user = re.findall(r"^USER (\S+)$", _dockerfile(image), re.M)[-1]
    if re.fullmatch(r"\d+:\d+", user):
        return user
    useradd = re.search(rf"useradd ([^\n]*) {user}$", text, re.M)
    assert useradd, f"{image}: no useradd creates USER {user}"
    uid = re.search(r"-u (\d+)", useradd.group(1)).group(1)
    gid = re.search(r"-g (\d+)", useradd.group(1))
    # With no -g, useradd creates the user's own group, numbered as the uid when that is free.
    return f"{uid}:{gid.group(1) if gid else uid}"


def test_every_container_definition_is_covered():
    assert set(USERS) == set(CONTAINERS)


@pytest.mark.parametrize("container", CONTAINERS, ids="/".join)
def test_every_container_sets_a_non_root_user(container):
    assert USERS[container], f"{container} sets no user (ECS.20)"
    for backend, user in _user_by_backend(USERS[container]).items():
        assert re.fullmatch(r"\d+:\d+", user), f"{container} on {backend}: user {user!r} is not uid:gid"
        assert "0" not in user.split(":"), f"{container} on {backend} runs as root ({user})"


@pytest.mark.parametrize(
    ("container", "backend", "image"),
    [
        pytest.param(container, backend, image, id=f"{'/'.join(container)}-{backend}")
        for container, images in CONTAINERS.items()
        for backend, image in images.items()
    ],
)
def test_each_user_is_the_one_its_image_runs_as(container, backend, image):
    assert _user_by_backend(USERS[container])[backend] == _image_user(image)
