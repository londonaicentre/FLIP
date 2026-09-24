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

"""Static guards on ``/flip/ci/host_aws_public_key``, the parameter Terraform CI reads its keypair from.

Every CI workflow writes this parameter to ``~/.ssh/host-aws.pub`` before planning, because both
``aws_key_pair`` resources read their key with ``file()`` and ``public_key`` is ForceNew — a runner
without the file plans a keypair replacement that ripples into the bastion. The parameter used to be
published by a make target; it is now declared, so the CI bootstrap is code. Three things have to
stay true, and none is reachable from a runtime test:

* **Its value is the keypair's own public key**, not a variable or a file. That is what makes the
  loop stable — CI reads the parameter, the keypair reads the file CI wrote, and the parameter is
  written back with the same bytes.
* **It adopts an existing parameter.** The self-contained accounts already hold one from the retired
  make target, and CI applies stag on every merge to ``develop``: without ``overwrite = true`` that
  first apply fails ``ParameterAlreadyExists``.
* **It refuses to publish when the two keypairs disagree.** CI can supply only one file, so a
  mismatch would silently plan a replacement of whichever keypair differs.

Assertions compare whole normalised expressions, not substrings, so an inverted condition or a
swapped reference fails rather than passing on containment.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
PARAMETER_STORE_TF = AWS_PROVIDER_DIR / "parameter_store.tf"
WORKFLOWS_DIR = AWS_PROVIDER_DIR.parents[2] / ".github" / "workflows"
RESOURCE = 'resource "aws_ssm_parameter" "ci_host_aws_public_key"'


def _normalise(text: str) -> str:
    """Drop trailing comments and collapse whitespace so layout cannot change meaning.

    Args:
        text (str): Terraform source text.

    Returns:
        str: The text with comments removed and whitespace collapsed.
    """
    return re.sub(r"\s+", " ", re.sub(r"#.*$", "", text, flags=re.M)).strip()


def _argument(block: str, name: str) -> str:
    """Return the normalised value of a single-line top-level argument.

    Args:
        block (str): An HCL block body.
        name (str): The argument name.

    Returns:
        str: The assigned expression.
    """
    match = re.search(rf"^\s*{name}\s*=\s*(.+)$", block, re.M)
    assert match is not None, f"{name} is not set"
    return _normalise(match.group(1))


def _parameter() -> str:
    """The parameter's resource block, whole-line comments stripped.

    Returns:
        str: The brace-balanced block body.
    """
    return hcl_block(strip_comments(PARAMETER_STORE_TF.read_text()), RESOURCE)


def test_name_is_the_one_the_workflows_read() -> None:
    """The workflows read a literal path, so the declared name has to resolve to it."""
    assert _argument(_parameter(), "name") == '"${local.ssm_prefix}/ci/host_aws_public_key"'
    for workflow in ("terraform_plan.yml", "terraform_apply.yml", "terraform_drift.yml"):
        assert "--name /flip/ci/host_aws_public_key" in (WORKFLOWS_DIR / workflow).read_text(), (
            f"{workflow} no longer reads /flip/ci/host_aws_public_key"
        )


def test_value_is_the_keypairs_own_public_key() -> None:
    """Not a variable or a file — the resource attribute is what keeps the loop stable."""
    value = _argument(_parameter(), "value")
    assert value == "aws_key_pair.host_key.public_key", (
        f"the parameter must publish the key the keypair resource holds, so CI reproduces it byte for byte: {value}"
    )


def test_it_adopts_an_existing_parameter() -> None:
    """The self-contained accounts already hold one; CI applies stag on every merge to develop."""
    assert _argument(_parameter(), "overwrite") == "true", (
        "overwrite must be true, or the first apply in an account that already has the parameter "
        "fails ParameterAlreadyExists"
    )


def test_it_refuses_when_the_two_keypairs_disagree() -> None:
    """CI can supply one file, so both keypairs must hold the same key."""
    precondition = hcl_block(hcl_block(_parameter(), "lifecycle"), "precondition")
    condition = _argument(precondition, "condition")
    assert condition == "aws_key_pair.flip_keypair.public_key == aws_key_pair.host_key.public_key", (
        f"the precondition must compare the two keypairs for equality: {condition}"
    )


def test_the_retired_make_target_is_gone() -> None:
    """Two writers for one parameter is drift waiting to happen."""
    makefile = (AWS_PROVIDER_DIR / "Makefile").read_text()
    assert "seed-ci-keypair-param" not in makefile, (
        "the make target was retired when Terraform took ownership of the parameter; putting it back "
        "gives the parameter a second writer outside state"
    )
