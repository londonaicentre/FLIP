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
path a ``VOLUME``, and the hub containers are not root: a path added to a task definition but not
to its image fails only on Fargate, as ``Permission denied`` at the first write.
"""

import re
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DIR.parents[2]
SOURCES = {path.name: strip_comments(path.read_text()) for path in sorted(AWS_DIR.glob("*.tf"))}
TASK_DEFINITION = re.compile(r'resource "aws_ecs_task_definition" "(\w+)"')
TASK_DEFINITIONS = [(name, task) for name, source in SOURCES.items() for task in TASK_DEFINITION.findall(source)]

# The image behind each FL scratch-volume map in ecs_tasks.tf. flip-api and the two images that
# mount only /tmp (the SuperLink, which the key-registration one-shot also runs, and the fleet
# proxy) are listed with that one path. The EFS provisioning task runs amazon/aws-cli as root.
IMAGE_VOLUMES = {
    "flip-api/Dockerfile": {"/tmp"},
    "fl-services/flower/superlink/Dockerfile": {"/tmp"},
    "fl-services/flower/fleet-proxy/Dockerfile": {"/tmp"},
}
SCRATCH_MAPS = {
    ("fl_api_scratch_volumes", "nvflare"): "fl-services/nvflare/fl-api-base/Dockerfile",
    ("fl_api_scratch_volumes", "flower"): "fl-services/flower/fl-api-flower/Dockerfile",
    ("fl_server_scratch_volumes", "nvflare"): "fl-services/nvflare/fl-server/Dockerfile",
    ("fl_server_scratch_volumes", "flower"): "fl-services/flower/superlink/Dockerfile",
}


def _declared_volumes(dockerfile: str) -> set[str]:
    text = (REPO_ROOT / dockerfile).read_text().replace("${UNAME}", "flip")
    return {path for line in re.findall(r"^VOLUME \[(.*)\]$", text, re.M) for path in re.findall(r'"([^"]+)"', line)}


@pytest.mark.parametrize(("tf_file", "task"), TASK_DEFINITIONS)
def test_every_container_has_a_read_only_root_filesystem(tf_file, task):
    body = hcl_block(SOURCES[tf_file], f'resource "aws_ecs_task_definition" "{task}"')
    containers = len(re.findall(r"^\s*image\s*=", body, re.M))
    assert containers, f"no container definition found in {task}"
    assert len(re.findall(r"^\s*readonlyRootFilesystem\s*=\s*true$", body, re.M)) == containers


def test_the_task_definitions_are_the_ones_expected():
    assert {task for _, task in TASK_DEFINITIONS} == {
        "flip_api",
        "fl_api_net_1",
        "fl_server_net_1",
        "efs_provision",
        "flower_register_supernode_keys",
    }


@pytest.mark.parametrize(("scratch_map", "backend"), SCRATCH_MAPS)
def test_each_scratch_volume_path_is_a_volume_in_its_image(scratch_map, backend):
    entries = re.search(rf"{backend}\s*=\s*\{{([^}}]*)\}}", hcl_block(SOURCES["ecs_tasks.tf"], f"{scratch_map} ="))
    paths = set(re.findall(r'=\s*"(/[^"]*)"', entries.group(1)))
    assert paths, f"no paths found for {scratch_map}.{backend}"
    assert paths <= _declared_volumes(SCRATCH_MAPS[scratch_map, backend])


@pytest.mark.parametrize("dockerfile", IMAGE_VOLUMES)
def test_the_tmp_only_images_declare_it(dockerfile):
    assert IMAGE_VOLUMES[dockerfile] <= _declared_volumes(dockerfile)
