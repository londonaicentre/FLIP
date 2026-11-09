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

"""Every hub container runs on a read-only root filesystem (Security Hub ECS.5, FLIP#1795).

ECS.5 fails a task definition as soon as one of its containers can write its root filesystem, so
each container definition sets ``readonlyRootFilesystem`` and writes only to its EFS mounts and to
empty task volumes. Fargate mounts such a volume root-owned 0755 unless the image declares the
path a ``VOLUME``, and the service containers are not root: a path added to a task definition but
not to its image fails only on Fargate, as ``Permission denied`` at the first write.

The rest pins what keeps the writes on those volumes: a ``/tmp`` mount on every container, the
environment that points each service at it, and the FLARE kit mounted where ``FL_WORKSPACE`` says.
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
TASKS = SOURCES["ecs_tasks.tf"]
TASK_DEFINITION = re.compile(r'resource "aws_ecs_task_definition" "(\w+)"')
TASK_DEFINITIONS = [(name, task) for name, source in SOURCES.items() for task in TASK_DEFINITION.findall(source)]
SCRATCH_LOOP = re.compile(r"for name, path in local\.(\w+_scratch_volumes)\[var\.fl_backend\]")

# The image behind each FL scratch-volume map in ecs_tasks.tf.
SCRATCH_MAPS = {
    ("fl_api_scratch_volumes", "nvflare"): "fl-services/nvflare/fl-api-base/Dockerfile",
    ("fl_api_scratch_volumes", "flower"): "fl-services/flower/fl-api-flower/Dockerfile",
    ("fl_server_scratch_volumes", "nvflare"): "fl-services/nvflare/fl-server/Dockerfile",
    ("fl_server_scratch_volumes", "flower"): "fl-services/flower/superlink/Dockerfile",
}
# The images whose containers mount /tmp alone. The SuperLink's also runs the key-registration
# one-shot; the EFS provisioning task runs amazon/aws-cli as root, so it needs no VOLUME.
TMP_ONLY_IMAGES = [
    "flip-api/Dockerfile",
    "fl-services/flower/superlink/Dockerfile",
    "fl-services/flower/fleet-proxy/Dockerfile",
]
# (env map in locals.tf, variable, value): what moves each service's writes off the root filesystem.
ENV = [
    ("flip_api =", "UV_CACHE_DIR", "/tmp/uv-cache"),
    ("fl_api =", "UV_NO_SYNC", "1"),
    ("fl_api =", "UV_CACHE_DIR", "/tmp/uv-cache"),
    ("fl_server_flower =", "HOME", "/tmp"),
    ("fl_server_flower =", "WORKING_DIR", "/tmp/runs"),
]
ONE_SHOTS = [("ecs_flower.tf", "flower_register_supernode_keys"), ("ecs_efs_provision.tf", "efs_provision")]


def _task(tf_file: str, task: str) -> str:
    return hcl_block(SOURCES[tf_file], f'resource "aws_ecs_task_definition" "{task}"')


def _scratch_paths(scratch_map: str, backend: str) -> set[str]:
    entries = re.search(rf"{backend}\s*=\s*\{{([^}}]*)\}}", hcl_block(TASKS, f"{scratch_map} =")).group(1)
    return set(re.findall(r'=\s*"(/[^"]*)"', entries))


def _declared_volumes(dockerfile: str) -> set[str]:
    text = (REPO_ROOT / dockerfile).read_text().replace("${UNAME}", "flip")
    return {path for line in re.findall(r"^VOLUME \[(.*)\]$", text, re.M) for path in re.findall(r'"([^"]+)"', line)}


def test_the_task_definitions_are_the_ones_expected():
    assert {task for _, task in TASK_DEFINITIONS} == {
        "flip_api",
        "fl_api_net_1",
        "fl_server_net_1",
        "efs_provision",
        "flower_register_supernode_keys",
    }


@pytest.mark.parametrize(("tf_file", "task"), TASK_DEFINITIONS)
def test_every_container_has_a_read_only_root_filesystem(tf_file, task):
    body = _task(tf_file, task)
    containers = len(re.findall(r"^\s*image\s*=", body, re.M))
    assert containers, f"no container definition found in {task}"
    assert len(re.findall(r"^\s*readonlyRootFilesystem\s*=\s*true$", body, re.M)) == containers


@pytest.mark.parametrize(("tf_file", "task"), TASK_DEFINITIONS)
def test_every_container_mounts_tmp_from_a_volume_of_its_task(tf_file, task):
    body = _task(tf_file, task)
    scratch_maps = SCRATCH_LOOP.findall(body)
    literal = re.findall(r'sourceVolume\s*=\s*"([\w-]+)",\s*containerPath\s*=\s*"/tmp"', body)
    assert len(scratch_maps) + len(literal) == len(re.findall(r"^\s*image\s*=", body, re.M))
    for scratch_map in scratch_maps:
        assert body.count(f"local.{scratch_map}[var.fl_backend]") >= 2, f"{scratch_map} is mounted but not a volume"
        assert all("/tmp" in _scratch_paths(scratch_map, backend) for backend in ("nvflare", "flower"))
    for name in literal:
        assert body.count(f'"{name}"') >= 2, f"{name} is mounted but is not a volume of {task}"


@pytest.mark.parametrize(("scratch_map", "backend"), SCRATCH_MAPS)
def test_each_scratch_volume_path_is_a_volume_in_its_image(scratch_map, backend):
    assert _scratch_paths(scratch_map, backend) <= _declared_volumes(SCRATCH_MAPS[scratch_map, backend])


@pytest.mark.parametrize("dockerfile", TMP_ONLY_IMAGES)
def test_the_tmp_only_images_declare_it(dockerfile):
    assert "/tmp" in _declared_volumes(dockerfile)


@pytest.mark.parametrize(("env_map", "name", "value"), ENV)
def test_the_environment_keeps_writes_on_the_volumes(env_map, name, value):
    env = hcl_block(hcl_block(SOURCES["locals.tf"], "ecs_task_env = {"), env_map)
    assert re.search(rf'^\s*{name}\s*=\s*"{re.escape(value)}"$', env, re.M)


@pytest.mark.parametrize(("tf_file", "task"), ONE_SHOTS)
def test_the_one_shot_tasks_keep_home_on_tmp(tf_file, task):
    assert re.search(r'\{\s*name\s*=\s*"HOME",\s*value\s*=\s*"/tmp"\s*\}', _task(tf_file, task))


def test_the_flare_kit_is_mounted_where_fl_workspace_says():
    env = hcl_block(hcl_block(SOURCES["locals.tf"], "ecs_task_env = {"), "fl_server =")
    workspace = re.search(r'^\s*FL_WORKSPACE\s*=\s*"([^"]+)"$', env, re.M).group(1)
    assert workspace in _scratch_paths("fl_server_scratch_volumes", "nvflare")
    body = _task("ecs_tasks.tf", "fl_server_net_1")
    for part in ("local", "startup", "transfer"):
        mount = re.search(rf'sourceVolume\s*=\s*"efs-fl-server-net-1-{part}"(.*?)\}}', body, re.S).group(1)
        assert f'containerPath = "{workspace}/{part}"' in mount
    entrypoint = (REPO_ROOT / "fl-services/nvflare/fl-server/fl-server/entrypoint.sh").read_text()
    assert 'WORKSPACE="${FL_WORKSPACE:-/app}"' in entrypoint
    assert not re.search(r"/app/(startup|local|transfer|\w*pid\.fl)", entrypoint)
