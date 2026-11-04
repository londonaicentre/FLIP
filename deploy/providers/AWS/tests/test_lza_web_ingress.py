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

"""Static guards on the LZA web-ingress shape (FLIP#749).

The web leg is CloudFront (networking account) -> relay NLB -> firewall/TGW -> the
workload account's INTERNAL FL NLB on :443 -> flip-api. The NLB's per-subnet IPs are
assigned via subnet_mapping, so the networking side registers them once; there is no
ALB (its IPs rotate and needed a target-sync Lambda). Nothing in the runtime suites can
see this wiring, so the invariants are asserted over the ``.tf`` source:

* the internal NLB carries a ``web-listener`` on ``var.ALB_HTTPS_PORT`` forwarding
  to the flip-api target group, which is TCP and health-checked on ``/api/health``;
* the flip-api ECS service registers with that target group;
* no ALB module or listener rule comes back;
* the SSM handoff no longer publishes an ALB DNS name (the networking relay must
  not be able to fall back to resolving a rotating address);
* every resource made unconditional by FLIP#1479 has a moved block from its old [0].
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
FL_INGRESS_LZA_TF = (AWS_PROVIDER_DIR / "fl_ingress_lza.tf").read_text()
MAIN_TF = (AWS_PROVIDER_DIR / "main.tf").read_text()
ECS_SERVICES_TF = (AWS_PROVIDER_DIR / "ecs_services.tf").read_text()
ALL_TF = "\n".join(p.read_text() for p in AWS_PROVIDER_DIR.glob("*.tf"))

EXPECTED_ECS_TARGET_GROUP = "aws_lb_target_group.ecs_flip_api.arn"


def test_internal_nlb_has_web_listener_on_https_port():
    nlb = strip_comments(hcl_block(FL_INGRESS_LZA_TF, 'module "fl_server_internal_nlb"'))
    listener = hcl_block(nlb, '"web-listener"')
    assert re.search(r"port\s*=\s*var\.ALB_HTTPS_PORT", listener)
    assert re.search(r'protocol\s*=\s*var\.manage_dns \? "TLS" : "TCP"', listener)
    assert EXPECTED_ECS_TARGET_GROUP in listener


def test_flip_api_target_group_is_tcp_with_http_health_check():
    tg = strip_comments(hcl_block(MAIN_TF, 'resource "aws_lb_target_group" "ecs_flip_api"'))
    assert not re.search(r"^\s*count\s*=", tg, re.M), "the flip-api TG must not be gated"
    assert re.search(r'protocol\s*=\s*"TCP"', tg)
    assert re.search(r'name\s*=\s*"ecs-flip-api-lza"', tg), "the name the live states hold (ForceNew)"
    assert re.search(r'target_type\s*=\s*"ip"', tg)
    health = hcl_block(tg, "health_check")
    assert re.search(r'protocol\s*=\s*"HTTP"', health)
    assert re.search(r'path\s*=\s*"/api/health"', health)
    assert re.search(r'matcher\s*=\s*"200"', health)


def test_ecs_flip_api_registers_with_nlb_target_group():
    service = strip_comments(hcl_block(ECS_SERVICES_TF, 'resource "aws_ecs_service" "flip_api"'))
    lb = hcl_block(service, "load_balancer")
    assert EXPECTED_ECS_TARGET_GROUP in lb


def test_no_alb_or_public_nlb_comes_back():
    stripped = strip_comments(ALL_TF)
    for header in ('module "alb"', 'module "fl_server_nlb"', 'resource "aws_lb_listener_rule" "api_routing"'):
        assert header not in stripped, header
    assert "lza_managed_network" not in stripped


def test_nlb_security_group_admits_web_port_from_networking_ingress():
    sg = strip_comments(hcl_block(FL_INGRESS_LZA_TF, 'module "fl_internal_nlb_security_group"'))
    assert re.search(r"port\s*=\s*var\.ALB_HTTPS_PORT", sg)
    assert sg.count("var.networking_ingress_cidrs") == 2, "both the FL and the web rule admit the relay path"


def test_flip_api_task_admits_the_internal_nlb():
    rule = strip_comments(
        hcl_block(FL_INGRESS_LZA_TF, 'resource "aws_security_group_rule" "ecs_flip_api_ingress_internal_nlb"')
    )
    assert "module.fl_internal_nlb_security_group.security_group.id" in rule
    assert "aws_security_group.ecs_flip_api.id" in rule
    assert re.search(r"from_port\s*=\s*local\.api_container_port", rule)


def test_ssm_handoff_publishes_nlb_dns_not_alb_dns():
    stripped = strip_comments(ALL_TF)
    assert "/flip/networking/alb_dns_name" not in stripped
    assert "/flip/networking/web_nlb_dns_name" in stripped
    assert 'resource "aws_security_group_rule" "alb_ingress_web_from_networking"' not in stripped


#: Addresses FLIP#1479 made unconditional: each was ``<address>[0]`` in both LZA states.
UNGATED = (
    "module.fl_internal_nlb_security_group",
    "aws_ssm_parameter.lza_fl_nlb_private_ips",
    "aws_ssm_parameter.lza_fl_port",
    "aws_ssm_parameter.lza_web_nlb_dns_name",
    "aws_ssm_parameter.lza_web_port",
    "aws_security_group_rule.ecs_fl_server_ingress_internal_nlb",
    "aws_security_group_rule.ecs_flip_api_ingress_internal_nlb",
    "aws_route53_zone.fl_server_bare_name",
    "aws_route53_record.fl_server_bare_name_apex",
    "aws_iam_role_policy.ecs_task_execution_ecr_pull_through",
)


def test_each_ungated_address_moves_from_its_old_index():
    """Without the moved block, dropping the count plans a destroy and recreate of a live LZA resource."""
    stripped = strip_comments(ALL_TF)
    for address in UNGATED:
        assert re.search(
            r"moved\s*\{\s*from\s*=\s*" + re.escape(address) + r"\[0\]\s*to\s*=\s*" + re.escape(address) + r"\s*\}",
            stripped,
        ), address
