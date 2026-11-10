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

"""Static guard: every ECS service must ignore changes to ``desired_count`` (FLIP#1596).

Staging is stopped outside a deploy, its end to end check and a hold window, and the stop is
``aws ecs update-service --desired-count 0`` rather than a Terraform change (a destroy would have
to recreate RDS, Cognito and the KMS key each cycle). That only works while Terraform does not
own the live count:

* without ``ignore_changes = [desired_count]`` the nightly drift run reports all three services as
  changed every night for as long as staging is down, which is most nights, and the report nobody
  can clear is the report everybody stops reading;
* and the next apply would quietly scale them back up, so the estate comes back without anyone
  asking for it and the hold window stops meaning anything.

``ignore_changes`` suppresses *updates*, not the value used on create, so a fresh estate still
comes up at the configured count. The start path sets it explicitly
(``scripts/staging-power.sh start``).

The services are discovered from source rather than listed, so a fourth service added later is
covered without editing this file.
"""

import re
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent

SERVICE_HEADER = re.compile(r'resource\s+"aws_ecs_service"\s+"([A-Za-z0-9_-]+)"')

# The three services scripts/staging-power.sh scales. Discovery must find at least these, so a
# rename that breaks the scan fails here rather than passing vacuously.
EXPECTED = {
    ("ecs_services.tf", "flip_api"),
    ("ecs_services.tf", "fl_api_net_1"),
    ("ecs_services.tf", "fl_server_net_1"),
}


def _tf_files() -> list[Path]:
    return sorted(p for p in AWS_PROVIDER_DIR.rglob("*.tf") if ".terraform" not in p.parts)


def _ecs_services() -> list[tuple[str, str, str]]:
    """Return ``(relative path, resource name, block body)`` for every ``aws_ecs_service``."""
    found = []
    for path in _tf_files():
        source = strip_comments(path.read_text())
        for name in SERVICE_HEADER.findall(source):
            body = hcl_block(source, f'resource "aws_ecs_service" "{name}"')
            found.append((str(path.relative_to(AWS_PROVIDER_DIR)), name, body))
    return found


def _ignored_attributes(body: str) -> set[str]:
    if not re.search(r"^\s*lifecycle\s*\{", body, re.MULTILINE):
        return set()
    lifecycle = hcl_block(body, "lifecycle")
    match = re.search(r"ignore_changes\s*=\s*\[([^\]]*)\]", lifecycle)
    return {item.strip() for item in match.group(1).split(",") if item.strip()} if match else set()


def test_discovery_finds_the_stoppable_services() -> None:
    found = {(path, name) for path, name, _ in _ecs_services()}
    assert EXPECTED <= found, f"expected aws_ecs_service resources not found: {sorted(EXPECTED - found)}"


@pytest.mark.parametrize(
    ("path", "name", "body"),
    [pytest.param(path, name, body, id=f"{path}:{name}") for path, name, body in _ecs_services()],
)
def test_ecs_service_ignores_desired_count(path: str, name: str, body: str) -> None:
    assert "desired_count" in _ignored_attributes(body), (
        f'{path}: aws_ecs_service "{name}" does not carry lifecycle '
        "{ ignore_changes = [desired_count] }, so a stopped staging estate reads as drift every "
        "night and the next apply scales it back up (FLIP#1596)"
    )
