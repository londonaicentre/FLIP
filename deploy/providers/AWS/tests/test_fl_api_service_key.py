# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#      http://www.apache.org/licenses/LICENSE-2.0
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Static guards on the FL API's callers on ECS: the service key and the security-group source.

Every FL API route but ``/health`` requires ``FL_API_KEY`` (``fl_api/utils/auth.py``). Nothing at runtime
catches a task that lacks it cheaply — flip-api's every FL call 401s, i.e. every training run fails — so
the wiring is asserted here over the ``.tf`` source: the key is one Secrets Manager field that the sender
(flip-api), the receiver (fl-api-net-1) and the Flower key-registration one-shot all read, and the fl-api
security group admits only those callers, never a CIDR.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
SECRET_REF = '"${module.flip_api_secret.secret_arn}:fl_api_key::"'


def _secret_entry(source: str, header: str) -> str:
    """The ``secrets`` list of the one container in ``header``'s task definition."""
    return hcl_block(hcl_block(source, header), "secrets = [")


def test_the_secret_payload_carries_the_key_from_a_sensitive_variable() -> None:
    payload = hcl_block((AWS_PROVIDER_DIR / "main.tf").read_text(), "secret_string = jsonencode(")
    assert re.search(r"^\s*fl_api_key\s*=\s*var\.FL_API_KEY\s*$", payload, re.MULTILINE), payload
    variable = hcl_block((AWS_PROVIDER_DIR / "variables.tf").read_text(), 'variable "FL_API_KEY"')
    assert re.search(r"^\s*sensitive\s*=\s*true\s*$", variable, re.MULTILINE), variable


def test_sender_receiver_and_registration_one_shot_read_the_same_secret_field() -> None:
    """One field, three tasks: a rename on one side would silently 401 every FL call."""
    ecs_tasks = strip_comments((AWS_PROVIDER_DIR / "ecs_tasks.tf").read_text())
    ecs_flower = strip_comments((AWS_PROVIDER_DIR / "ecs_flower.tf").read_text())
    for source, header in (
        (ecs_tasks, 'resource "aws_ecs_task_definition" "flip_api"'),
        (ecs_tasks, 'resource "aws_ecs_task_definition" "fl_api_net_1"'),
        (ecs_flower, 'resource "aws_ecs_task_definition" "flower_register_supernode_keys"'),
    ):
        entry = _secret_entry(source, header)
        assert re.search(r'name\s*=\s*"FL_API_KEY"', entry), header
        assert SECRET_REF in entry, header


def test_fl_api_ingress_comes_from_its_callers_not_a_cidr() -> None:
    """The fl-api SG admits flip-api's SG and (Flower) itself; no ingress rule on it names a CIDR block."""
    source = strip_comments((AWS_PROVIDER_DIR / "ecs_sg.tf").read_text())
    ingress = {
        name: body
        for name, body in re.findall(r'resource "aws_security_group_rule" "(\w+)" \{(.*?)\n\}', source, re.DOTALL)
        if re.search(r"(?<!source_)security_group_id\s*=\s*aws_security_group\.ecs_fl_api\.id", body)
        and re.search(r'type\s*=\s*"ingress"', body)
    }
    assert set(ingress) == {"ecs_fl_api_ingress_flip_api_http", "ecs_fl_api_ingress_self_http"}, sorted(ingress)
    for name, body in ingress.items():
        assert "cidr_blocks" not in body, name
    flip_api_rule = ingress["ecs_fl_api_ingress_flip_api_http"]
    assert re.search(r"source_security_group_id\s*=\s*aws_security_group\.ecs_flip_api\.id", flip_api_rule)
    self_rule = ingress["ecs_fl_api_ingress_self_http"]
    assert re.search(r"self\s*=\s*true", self_rule)
    assert 'var.fl_backend == "flower" ? 1 : 0' in self_rule
