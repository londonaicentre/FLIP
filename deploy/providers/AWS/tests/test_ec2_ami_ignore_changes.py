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

"""Static guard: an EC2 host launched from a floating AMI must ignore changes to ``ami`` (FLIP#1281).

The hosts read their image from Canonical's ``.../stable/current/.../ami-id`` SSM parameter, which
moves with every Ubuntu release, and ``ami`` forces replacement. Without ``ignore_changes = [ami]``
each new image replaces the host on the next apply — an unattended one in CI included — leaving it
unprovisioned (``site.yml`` is not re-run) and, on the trust host, deleting the root volume that holds
the trust's data. A deliberate replacement still launches from the current image, because
``ignore_changes`` suppresses updates, not the value used on create.

Every ``aws_instance`` whose ``ami`` comes from an ``aws_ssm_parameter`` is discovered from source
rather than listed, so a new host added on the same pattern is covered without editing this file.
"""

import re
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent

INSTANCE_HEADER = re.compile(r'resource\s+"aws_instance"\s+"([A-Za-z0-9_-]+)"')

# The hosts this guard exists for; discovery must find at least these, so a rename that
# breaks the scan fails here rather than passing vacuously.
EXPECTED = {("main.tf", "ec2_instance"), ("modules/trust_ec2/main.tf", "trust_host")}


def _tf_files() -> list[Path]:
    return sorted(p for p in AWS_PROVIDER_DIR.rglob("*.tf") if ".terraform" not in p.parts)


def _floating_ami_instances() -> list[tuple[str, str, str]]:
    """Return ``(relative path, resource name, block body)`` for each SSM-sourced ``aws_instance``."""
    found = []
    for path in _tf_files():
        source = strip_comments(path.read_text())
        for name in INSTANCE_HEADER.findall(source):
            body = hcl_block(source, f'resource "aws_instance" "{name}"')
            if re.search(r"^\s*ami\s*=\s*data\.aws_ssm_parameter\.", body, re.MULTILINE):
                found.append((str(path.relative_to(AWS_PROVIDER_DIR)), name, body))
    return found


def _ignored_attributes(body: str) -> set[str]:
    if not re.search(r"^\s*lifecycle\s*\{", body, re.MULTILINE):
        return set()
    lifecycle = hcl_block(body, "lifecycle")
    match = re.search(r"ignore_changes\s*=\s*\[([^\]]*)\]", lifecycle)
    return {item.strip() for item in match.group(1).split(",") if item.strip()} if match else set()


def test_discovery_finds_the_known_hosts() -> None:
    found = {(path, name) for path, name, _ in _floating_ami_instances()}
    assert EXPECTED <= found, f"expected SSM-sourced aws_instance resources not found: {sorted(EXPECTED - found)}"


@pytest.mark.parametrize(
    ("path", "name", "body"),
    [pytest.param(path, name, body, id=f"{path}:{name}") for path, name, body in _floating_ami_instances()],
)
def test_floating_ami_instance_ignores_ami(path: str, name: str, body: str) -> None:
    assert "ami" in _ignored_attributes(body), (
        f'{path}: aws_instance "{name}" reads a floating AMI but does not carry '
        "lifecycle { ignore_changes = [ami] }, so every new Canonical image replaces it on the next apply"
    )
