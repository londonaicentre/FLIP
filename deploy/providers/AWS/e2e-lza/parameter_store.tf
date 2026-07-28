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

# The contract with the networking account. Its LZA-side stack consumes
# these parameters to attach this VPC to the TGW and wire the edge listeners
# at the workload half of the chain — mirroring the /flip/networking/*
# pattern the prod root publishes for aicentre-iac (../parameter_store.tf).
# Names are surfaced in outputs.tf (SsmParameterNames).

locals {
  ssm_prefix = "/flip-e2e/networking"
}

resource "aws_ssm_parameter" "vpc_id" {
  name        = "${local.ssm_prefix}/vpc_id"
  description = "e2e LZA workload VPC ID — consumed cross-account by the networking account's TGW VPC attachment"
  type        = "String"
  value       = module.vpc.vpc_id
}

resource "aws_ssm_parameter" "private_subnet_ids" {
  name        = "${local.ssm_prefix}/private_subnet_ids"
  description = "e2e LZA workload private subnet IDs (comma-separated) — consumed cross-account by the networking account's TGW VPC attachment"
  type        = "StringList"
  value       = join(",", module.vpc.private_subnets)
}

resource "aws_ssm_parameter" "alb_dns_name" {
  name        = "${local.ssm_prefix}/alb_dns_name"
  description = "Internal web ALB DNS name — the networking account's DNS-sync Lambda must resolve this on a cadence to keep the web-tier edge NLB targets current (ALB IPs rotate; README open question 3)"
  type        = "String"
  value       = module.web_alb.dns_name
}

resource "aws_ssm_parameter" "nlb_private_ips" {
  name        = "${local.ssm_prefix}/nlb_private_ips"
  description = "Internal FL NLB static private IPs (comma-separated) — registered as IP targets on the networking account's edge NLB :8002 listener; stable for the NLB's lifetime"
  type        = "StringList"
  value       = join(",", local.fl_nlb_private_ips)
}

resource "aws_ssm_parameter" "web_port" {
  name        = "${local.ssm_prefix}/web_port"
  description = "Workload-side web ingress port (internal ALB listener) — consumed by the networking account's edge configuration"
  type        = "String"
  value       = tostring(local.web_port)
}

resource "aws_ssm_parameter" "fl_port" {
  name        = "${local.ssm_prefix}/fl_port"
  description = "Workload-side FL ingress port (internal NLB listener) — consumed by the networking account's edge configuration"
  type        = "String"
  value       = tostring(local.fl_port)
}
