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

"""Static guards on the fl-api bundle-fetch host allow-list in the ECS task environment (FLIP#905).

``BUNDLE_URL_ALLOWED_HOSTS`` is the primary SSRF control in front of the fl-api's server-side bundle
fetch: the one host it may download an app bundle from. It was set in no environment until FLIP#905,
which left the allow-list branch of ``validate_bundle_url`` inert everywhere. Nothing at runtime can
catch a missing or wrong value cheaply — empty means "any public host" and only logs a warning; wrong
400s every bundle download, i.e. every training run — so the wiring is asserted here, over the ``.tf``
source, for both fl-api task-environment maps.

The value is not free-standing. flip-api presigns bundle URLs against ``AWS_ENDPOINT_URL_S3``, and a
regional endpoint makes botocore emit path-style URLs whose host is exactly ``s3.<region>.amazonaws.com``.
So the allow-list must be that endpoint's host, and ``locals.tf`` derives both from one local
(``s3_regional_endpoint_host``) so they cannot drift. This suite asserts that derivation rather than a
literal, because a literal that happened to match today would not survive a change to either side.
"""

import re
from pathlib import Path

from tf_source import hcl_block

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
LOCALS_TF = AWS_PROVIDER_DIR / "locals.tf"
ECS_TASKS_TF = AWS_PROVIDER_DIR / "ecs_tasks.tf"

SHARED_LOCAL = "s3_regional_endpoint_host"


def _attribute(block: str, name: str) -> str:
    """Read one ``name = <expression>`` attribute out of an HCL block body.

    Args:
        block (str): An HCL block body.
        name (str): The attribute name.

    Returns:
        str: The right-hand side, stripped, with any ``${...}`` interpolation left verbatim.
    """
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", block, re.MULTILINE)
    assert match is not None, f"{name} is not set as a single-line attribute — the guard below cannot see it"
    return match.group(1)


def _task_env_map(name: str) -> str:
    """The body of one ``ecs_task_env`` sub-map (``flip_api``, ``fl_api``, ``fl_api_flower``).

    ``flip_api`` is ``merge(local.enforce_mfa_env, { ... })``; the first ``{`` after ``flip_api = merge(``
    opens its literal map, which is the body wanted. The fl-api maps are plain ``name = { ... }``.
    """
    ecs_task_env = hcl_block(LOCALS_TF.read_text(), "ecs_task_env = {")
    return hcl_block(ecs_task_env, f"{name} = merge(" if name == "flip_api" else f"{name} = {{")


def test_shared_local_is_the_regional_s3_endpoint_host() -> None:
    """The one value both consumers derive from is the regional endpoint host, built from AWS_REGION."""
    locals_body = hcl_block(LOCALS_TF.read_text(), "locals {")
    assert _attribute(locals_body, SHARED_LOCAL) == '"s3.${var.AWS_REGION}.amazonaws.com"'


def test_flip_api_presigns_against_the_shared_endpoint() -> None:
    """flip-api's S3 endpoint is the https form of the shared host — the presign origin the fl-api admits."""
    assert _attribute(_task_env_map("flip_api"), "AWS_ENDPOINT_URL_S3") == f'"https://${{local.{SHARED_LOCAL}}}"'


def test_both_fl_api_task_families_allow_list_the_shared_endpoint() -> None:
    """Every fl-api task environment — NVFLARE and Flower — pins bundle fetches to the presign origin.

    ``ecs_tasks.tf`` selects one of the two maps by ``var.fl_backend``, so both must carry the variable
    or one backend ships with the allow-list inert (FLIP#905).
    """
    for family in ("fl_api", "fl_api_flower"):
        assert _attribute(_task_env_map(family), "BUNDLE_URL_ALLOWED_HOSTS") == f"local.{SHARED_LOCAL}", family


def test_fl_api_task_definition_selects_one_of_the_guarded_maps() -> None:
    """The fl-api container's environment comes from exactly the two maps asserted above, by backend."""
    task_definition = hcl_block(ECS_TASKS_TF.read_text(), 'resource "aws_ecs_task_definition" "fl_api_net_1"')
    assert re.search(
        r'var\.fl_backend == "flower" \? local\.ecs_task_env\.fl_api_flower : local\.ecs_task_env\.fl_api\b',
        task_definition,
    ), "fl-api-net-1 no longer selects its environment from ecs_task_env.fl_api / fl_api_flower"
