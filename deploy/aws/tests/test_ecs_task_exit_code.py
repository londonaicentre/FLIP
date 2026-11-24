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

"""The one-shot ECS tasks ``terraform apply`` runs are judged by their own container (FLIP#1447).

Two ``null_resource`` provisioners run a Fargate task and read its exit code:
``provision_efs_certs`` and ``flower_register_supernode_keys``. GuardDuty Runtime Monitoring adds
a sidecar (``aws-guardduty-agent-*``) to every Fargate task, and ``describe-tasks`` often lists it
first, so ``containers[0]`` can be the agent's exit code. A failed task then passes the apply when
the agent exits 0, and a good apply fails when the agent does not.

Each check now selects the container by the name its task definition gives it. Asserted here two
ways: over the Terraform source, and by running each provisioner's command (its interpolations
filled in) against a stub ``aws`` that lists the sidecar first.
"""

import re
import subprocess
from pathlib import Path

import pytest
from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent

# (file, provisioner block header, task definition resource whose container is the one judged)
CHECKS = {
    "efs": (
        "ecs_efs_provision.tf",
        'resource "null_resource" "provision_efs_certs"',
        "efs_provision",
    ),
    "flower": (
        "ecs_flower.tf",
        'resource "null_resource" "flower_register_supernode_keys"',
        "flower_register_supernode_keys",
    ),
}

HEREDOC = re.compile(r"command\s*=\s*<<-EOT\n(.*?)\n\s*EOT", re.S)
INTERPOLATION = re.compile(r"\$\{([^}]*)\}")
SIDECAR = "aws-guardduty-agent-a1b2c3"

# Answers describe-tasks the way GuardDuty leaves it: the agent listed first. The stub reads the
# JMESPath query it is given and answers it for that fixture, so a check reading containers[0]
# gets the agent's code and one selecting by name gets the task's.
STUB_AWS = """#!/usr/bin/env bash
case "$1 $2" in
  "ecs run-task") echo "arn:aws:ecs:eu-west-2:000000000000:task/flip/0123" ;;
  "ecs wait") exit 0 ;;
  "ecs describe-tasks")
    [ -n "${DESCRIBE_FAILS:-}" ] && exit 255
    query=""
    while [ $# -gt 0 ]; do [ "$1" = "--query" ] && query="$2"; shift; done
    case "$query" in
      *"containers[0].exitCode"*) echo "${AGENT_EXIT}" ;;
      *"containers[?name=='${TASK_CONTAINER}'].exitCode | [0]"*) echo "${TASK_EXIT}" ;;
      *) echo "None" ;;
    esac ;;
  *) echo "unexpected aws call: $*" >&2; exit 2 ;;
esac
"""


def _source(name: str) -> str:
    return strip_comments((AWS_PROVIDER_DIR / name).read_text())


def _command(check: str) -> str:
    file, header, _ = CHECKS[check]
    found = HEREDOC.search(hcl_block(_source(file), header))
    assert found, f"no local-exec command heredoc in {header}"
    return found.group(1)


def _container_name(check: str) -> str:
    """The container name the task definition declares (its first container_definitions entry)."""
    file, _, task_definition = CHECKS[check]
    body = hcl_block(_source(file), f'resource "aws_ecs_task_definition" "{task_definition}"')
    found = re.search(r'^\s*name\s*=\s*"([^"]+)"', body, re.M)
    assert found, f"no container name in aws_ecs_task_definition.{task_definition}"
    return found.group(1)


def test_no_terraform_file_reads_the_first_containers_exit_code():
    offenders = [p.name for p in AWS_PROVIDER_DIR.glob("*.tf") if "containers[0]" in strip_comments(p.read_text())]
    assert not offenders, f"containers[0] can be the GuardDuty sidecar: {offenders}"


@pytest.mark.parametrize("check", CHECKS)
def test_the_check_names_its_container_from_the_task_definition(check):
    _, _, task_definition = CHECKS[check]
    command = _command(check)
    assert (
        f"CONTAINER='${{jsondecode(aws_ecs_task_definition.{task_definition}[0].container_definitions)[0].name}}'"
        in command
    )
    assert "--query \"tasks[0].containers[?name=='$CONTAINER'].exitCode | [0]\"" in command


def _run(check: str, tmp_path: Path, **fixture: str) -> subprocess.CompletedProcess:
    """Run the provisioner's command with its interpolations filled in, against the stub aws."""
    container = _container_name(check)

    def fill(match: re.Match) -> str:
        return container if match.group(1).startswith("jsondecode(") else "x"

    script = INTERPOLATION.sub(fill, _command(check))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(STUB_AWS)
    aws.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "TASK_CONTAINER": container, "AGENT_EXIT": "0", **fixture}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30)


@pytest.mark.parametrize("check", CHECKS)
def test_a_task_that_succeeds_passes_whatever_the_sidecar_exits(check, tmp_path):
    result = _run(check, tmp_path, TASK_EXIT="0", AGENT_EXIT="137")

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("check", CHECKS)
def test_a_task_that_fails_fails_the_apply_though_the_sidecar_exits_0(check, tmp_path):
    result = _run(check, tmp_path, TASK_EXIT="1", AGENT_EXIT="0")

    assert result.returncode == 1
    assert "exit code 1" in result.stdout


@pytest.mark.parametrize("check", CHECKS)
def test_no_exit_code_for_the_container_fails_and_says_so(check, tmp_path):
    result = _run(check, tmp_path, TASK_EXIT="None")

    assert result.returncode == 1
    assert "no exit code" in result.stdout
    assert _container_name(check) in result.stdout


@pytest.mark.parametrize("check", CHECKS)
def test_an_unreadable_task_fails_the_apply(check, tmp_path):
    result = _run(check, tmp_path, TASK_EXIT="0", DESCRIBE_FAILS="1")

    assert result.returncode == 1
    assert "Could not read" in result.stdout
