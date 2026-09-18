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

On an LZA estate the web leg is CloudFront (networking account) -> relay NLB ->
firewall/TGW -> the workload account's INTERNAL FL NLB on :443 -> flip-api. The
NLB's per-subnet IPs are assigned via subnet_mapping, so the networking side
registers them once; the ALB (whose IPs rotate and needed a target-sync Lambda)
is gated off on LZA. Nothing in the runtime suites can see this wiring, so the
invariants are asserted over the ``.tf`` source:

* the internal NLB carries a ``web-listener`` on ``var.ALB_HTTPS_PORT`` forwarding
  to the flip-api target group, which is TCP on LZA and health-checked on
  ``/api/health`` in both modes;
* the flip-api ECS service registers with that one target group (shared by the
  ALB and the internal NLB — exactly one of the two exists per environment);
* the ALB module and its listener rule are gated off on LZA;
* the SSM handoff no longer publishes an ALB DNS name (the networking relay must
  not be able to fall back to resolving a rotating address).
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


def test_flip_api_target_group_is_tcp_on_lza_with_http_health_check():
    tg = strip_comments(hcl_block(MAIN_TF, 'resource "aws_lb_target_group" "ecs_flip_api"'))
    assert not re.search(r"^\s*count\s*=", tg, re.M), "the flip-api TG serves both front doors — it must not be gated"
    assert re.search(r'protocol\s*=\s*var\.lza_managed_network \? "TCP" : "HTTP"', tg)
    assert re.search(r'target_type\s*=\s*"ip"', tg)
    health = hcl_block(tg, "health_check")
    assert re.search(r'protocol\s*=\s*"HTTP"', health)
    assert re.search(r'path\s*=\s*"/api/health"', health)
    assert re.search(r'matcher\s*=\s*"200"', health)


def test_ecs_flip_api_registers_with_nlb_target_group_on_lza():
    service = strip_comments(hcl_block(ECS_SERVICES_TF, 'resource "aws_ecs_service" "flip_api"'))
    lb = hcl_block(service, "load_balancer")
    assert EXPECTED_ECS_TARGET_GROUP in lb


def test_alb_and_its_routing_are_gated_off_on_lza():
    alb = strip_comments(hcl_block(MAIN_TF, 'module "alb"'))
    assert re.search(r"create\s*=\s*!var\.lza_managed_network", alb)
    rule = strip_comments(hcl_block(MAIN_TF, 'resource "aws_lb_listener_rule" "api_routing"'))
    assert re.search(r"count\s*=\s*var\.lza_managed_network \? 0 : 1", rule)


def test_nlb_security_group_admits_web_port_from_networking_ingress():
    sg = strip_comments(hcl_block(FL_INGRESS_LZA_TF, 'module "fl_internal_nlb_security_group"'))
    assert re.search(r"port\s*=\s*var\.ALB_HTTPS_PORT", sg)
    assert sg.count("var.networking_ingress_cidrs") == 2, "both the FL and the web rule admit the relay path"


def test_flip_api_task_admits_the_internal_nlb():
    rule = strip_comments(
        hcl_block(FL_INGRESS_LZA_TF, 'resource "aws_security_group_rule" "ecs_flip_api_ingress_internal_nlb"')
    )
    assert "module.fl_internal_nlb_security_group[0].security_group.id" in rule
    assert "aws_security_group.ecs_flip_api.id" in rule
    assert re.search(r"from_port\s*=\s*local\.api_container_port", rule)


def test_ssm_handoff_publishes_nlb_dns_not_alb_dns():
    stripped = strip_comments(ALL_TF)
    assert "/flip/networking/alb_dns_name" not in stripped
    assert "/flip/networking/web_nlb_dns_name" in stripped
    assert 'resource "aws_security_group_rule" "alb_ingress_web_from_networking"' not in stripped


def test_lza_only_resources_are_count_gated():
    for header in (
        'resource "aws_security_group_rule" "ecs_flip_api_ingress_internal_nlb"',
        'resource "aws_ssm_parameter" "lza_web_nlb_dns_name"',
    ):
        block = strip_comments(hcl_block(FL_INGRESS_LZA_TF, header))
        assert re.search(r"count\s*=\s*var\.lza_managed_network \? 1 : 0", block), header
