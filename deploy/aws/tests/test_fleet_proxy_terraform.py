# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""On ECS a trust reaches only Flower's fleet proxy, and NVFLARE's shape is unchanged (FLIP#1517).

flwr>=1.40 serves the SuperLink's unauthenticated Control API and the ServerApp's Runtime API on
the same port as the Fleet API. On a Flower estate the NLB therefore binds the fleet-proxy sidecar,
the SuperLink maps no trust-facing port, and its 8000 is open to the fl-api's security group alone,
never the whole VPC. NVFLARE keeps every one of these as it
was; the count added to the VPC-wide rule carries a `moved` block so its state plans no change.

Source-level assertions, like the rest of this suite: the CI plan runs against an NVFLARE estate,
so a regression on the Flower branch would not show in any plan CI makes.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_DIR = Path(__file__).resolve().parents[1]
ECS_TASKS = strip_comments((AWS_DIR / "ecs_tasks.tf").read_text())
ECS_SERVICES = strip_comments((AWS_DIR / "ecs_services.tf").read_text())
ECS_SG = strip_comments((AWS_DIR / "ecs_sg.tf").read_text())


def _fl_server_containers() -> str:
    return hcl_block(ECS_TASKS, 'resource "aws_ecs_task_definition" "fl_server_net_1"')


def test_the_nlb_binds_the_proxy_on_flower_and_the_server_on_nvflare():
    assert re.search(
        r'fl_server_lb_container\s*=\s*var\.fl_backend == "flower" \? "fleet-proxy" : "fl-server-net-1"', ECS_TASKS
    )
    service = hcl_block(ECS_SERVICES, 'resource "aws_ecs_service" "fl_server_net_1"')
    assert "container_name   = local.fl_server_lb_container" in service
    assert "container_port = local.fl_server_container_port" in service
    assert re.search(
        r'fl_server_container_port\s*=\s*var\.fl_backend == "flower" \? local\.flower_fleet_proxy_port', ECS_TASKS
    )


def test_the_superlink_maps_no_trust_facing_port():
    task = _fl_server_containers()
    flower_ports = re.search(r'portMappings = var\.fl_backend == "flower" \? \[(.*?)\] : \[', task, re.DOTALL)
    assert flower_ports, "the SuperLink's Flower portMappings were not found"
    assert "flower_fleet_proxy_port" not in flower_ports.group(1)
    assert "flower_superlink_control_port" in flower_ports.group(1)


def test_the_proxy_sidecar_exists_on_flower_only():
    task = _fl_server_containers()
    assert "jsonencode(concat([" in task
    sidecar = re.search(
        r'var\.fl_backend == "flower" \? \[\s*\{\s*name\s*=\s*"fleet-proxy"(.*?)\] : \[\]\)\)', task, re.DOTALL
    )
    assert sidecar, 'the fleet-proxy sidecar must be the `var.fl_backend == "flower" ? [...] : []` list'
    body = sidecar.group(1)
    assert "local.fl_fleet_proxy_image" in body
    assert "containerPort = local.flower_fleet_proxy_port" in body
    assert '"/usr/local/bin/fleet-proxy-healthcheck"' in body


def test_port_8000_is_open_to_the_vpc_on_nvflare_only():
    rule = hcl_block(ECS_SG, 'resource "aws_security_group_rule" "ecs_fl_server_ingress_vpc_http"')
    assert 'count             = var.fl_backend == "flower" ? 0 : 1' in rule
    assert "local.vpc_cidr_block" in rule
    # Without it every NVFLARE state plans a destroy-and-recreate of the rule.
    assert re.search(
        r"moved \{\s*from = aws_security_group_rule\.ecs_fl_server_ingress_vpc_http\s*"
        r"to   = aws_security_group_rule\.ecs_fl_server_ingress_vpc_http\[0\]\s*\}",
        ECS_SG,
    )
    flower_8000 = hcl_block(ECS_SG, 'resource "aws_security_group_rule" "ecs_fl_server_ingress_fl_api_flower_exec"')
    assert "source_security_group_id = aws_security_group.ecs_fl_api.id" in flower_8000
    assert "cidr_blocks" not in flower_8000
