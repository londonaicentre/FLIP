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

"""Static guard: a ``modules/secgroup`` group carries ``FlipSG`` on itself, with one owner (FLIP#1392).

The SG drift alarm (``security.tf``) only reports on groups tagged ``FlipSG = "true"``. The module's
groups used to get that tag from standalone ``aws_ec2_tag`` resources while the group declared no
tags, so the group's own tag diff stripped it and the ``aws_ec2_tag`` restored it on alternate
applies: a permanent plan diff, and the alarm blind to those groups half the time.

Every caller of the module is discovered from source rather than listed, so a new group is covered
without editing this file.
"""

import re
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent

MODULE_HEADER = re.compile(r'module\s+"([A-Za-z0-9_-]+)"')
SECGROUP_SOURCE = re.compile(r'^\s*source\s*=\s*"\./modules/secgroup"', re.MULTILINE)
FLIPSG_TAG = re.compile(r'^\s*tags\s*=\s*\{[^}]*\bFlipSG\s*=\s*"true"', re.MULTILINE)
EC2_TAG_HEADER = re.compile(r'resource\s+"aws_ec2_tag"\s+"([A-Za-z0-9_-]+)"')

# The groups this guard exists for; discovery must find at least these, so a rename that breaks
# the scan fails here rather than passing vacuously.
EXPECTED = {
    ("main.tf", "ec2_security_group"),
    ("main.tf", "trust_security_group"),
    ("main.tf", "rds_security_group"),
    ("main.tf", "alb_security_group"),
    ("fl_ingress_lza.tf", "fl_internal_nlb_security_group"),
}


def _tf_files() -> list[Path]:
    return sorted(p for p in AWS_PROVIDER_DIR.rglob("*.tf") if ".terraform" not in p.parts)


def _relative(path: Path) -> str:
    return str(path.relative_to(AWS_PROVIDER_DIR))


def _secgroup_callers() -> list[tuple[str, str, str]]:
    """Return ``(relative path, module name, block body)`` for each call of ``./modules/secgroup``."""
    found = []
    for path in _tf_files():
        source = strip_comments(path.read_text())
        for name in MODULE_HEADER.findall(source):
            body = hcl_block(source, f'module "{name}"')
            if SECGROUP_SOURCE.search(body):
                found.append((_relative(path), name, body))
    return found


def test_discovery_finds_the_known_groups() -> None:
    found = {(path, name) for path, name, _ in _secgroup_callers()}
    assert EXPECTED <= found, f"expected secgroup module calls not found: {sorted(EXPECTED - found)}"


def test_module_sets_tags_on_the_group() -> None:
    source = strip_comments((AWS_PROVIDER_DIR / "modules/secgroup/main.tf").read_text())
    body = hcl_block(source, 'resource "aws_security_group" "security_group"')
    assert re.search(r"^\s*tags\s*=\s*var\.tags\s*$", body, re.MULTILINE), (
        "modules/secgroup: aws_security_group must set `tags = var.tags`; without it the group's own "
        "tag diff strips any tag applied to it from outside"
    )


@pytest.mark.parametrize(
    ("path", "name", "body"),
    [pytest.param(path, name, body, id=f"{path}:{name}") for path, name, body in _secgroup_callers()],
)
def test_secgroup_caller_tags_flipsg(path: str, name: str, body: str) -> None:
    assert FLIPSG_TAG.search(body), (
        f'{path}: module "{name}" does not pass tags = {{ FlipSG = "true" }}, so the SG drift alarm '
        "never reports changes to that group"
    )


def test_no_aws_ec2_tag_targets_a_secgroup_module() -> None:
    callers = {name for _, name, _ in _secgroup_callers()}
    offenders = []
    for path in _tf_files():
        source = strip_comments(path.read_text())
        for name in EC2_TAG_HEADER.findall(source):
            body = hcl_block(source, f'resource "aws_ec2_tag" "{name}"')
            target = re.search(r"resource_id\s*=\s*module\.([A-Za-z0-9_-]+)", body)
            if target and target.group(1) in callers:
                offenders.append(f"{_relative(path)}:{name} -> module.{target.group(1)}")
    assert not offenders, (
        "aws_ec2_tag on a secgroup-module group fights the group's own tags and flips on every apply; "
        f"pass the tag through the module's `tags` instead: {offenders}"
    )
