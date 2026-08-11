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

# The contract with the networking account, mirroring the /flip/networking/*
# pattern the prod root publishes for aicentre-iac (../parameter_store.tf).
# In the LZA the TGW attachment already exists (provisioned with the VPC by
# the "prod" VPC template), so vpc_id/private_subnet_ids are informational —
# the load-bearing values are alb_dns_name (web tier DNS-sync) and
# nlb_private_ips (FL edge targets). Names are surfaced in outputs.tf
# (SsmParameterNames).

locals {
  ssm_prefix = "/flip-e2e/networking"
}

resource "aws_ssm_parameter" "vpc_id" {
  name        = "${local.ssm_prefix}/vpc_id"
  description = "e2e LZA workload VPC ID (LZA-provisioned, TGW attachment pre-exists) — informational for the networking account's edge configuration"
  type        = "String"
  value       = data.aws_vpc.lza_prod.id
}

resource "aws_ssm_parameter" "private_subnet_ids" {
  name        = "${local.ssm_prefix}/private_subnet_ids"
  description = "e2e LZA workload app subnet IDs (comma-separated, LZA-provisioned) — informational for the networking account's edge configuration"
  type        = "StringList"
  value       = join(",", local.app_subnet_ids)
}

resource "aws_ssm_parameter" "alb_dns_name" {
  count       = var.enable_web_leg ? 1 : 0
  name        = "${local.ssm_prefix}/alb_dns_name"
  description = "Internal web ALB DNS name — the networking account's DNS-sync Lambda must resolve this on a cadence to keep the web-tier edge NLB targets current (ALB IPs rotate; README open question 3)"
  type        = "String"
  value       = module.web_alb[0].dns_name
}

resource "aws_ssm_parameter" "nlb_private_ips" {
  name        = "${local.ssm_prefix}/nlb_private_ips"
  description = "Internal FL NLB static private IPs (comma-separated, assigned via subnet_mapping so they are stable by construction; TGW-reachable AZs only — see nlb.tf) — registered as IP targets on the networking account's edge NLB :8002 listener"
  type        = "StringList"
  value       = join(",", local.fl_nlb_published_ips)
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
