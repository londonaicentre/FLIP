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

# Non-secret configuration consumed by ECS tasks. Naming convention is
# /flip/<key>. Secret values (API keys, hashes, DB passwords) live in
# Secrets Manager — SSM only holds plain configuration.
#
# State (and the AWS account it targets) is partitioned per environment, so
# /flip/* without an env segment is unambiguous within a given account.

locals {
  ssm_prefix = "/flip"
}

resource "aws_ssm_parameter" "flip_api_internal_url" {
  name        = "${local.ssm_prefix}/flip_api_internal_url"
  description = "Internal hostname:port for fl-server -> flip-api callbacks (Service Discovery)"
  type        = "String"
  value       = "http://${local.service_discovery_names.flip_api}:${local.api_container_port}/api"
}

resource "aws_ssm_parameter" "flip_model_files_uploads_bucket" {
  name        = "${local.ssm_prefix}/flip_model_files_uploads_bucket"
  description = "S3 URI of the researcher model-files-uploads bucket (browser presigned-PUT target today; flips to presigned POST once PR #438 lands; flip-api reads/deletes)"
  type        = "String"
  value       = local.flip_model_files_uploads_bucket_uri
}

resource "aws_ssm_parameter" "flip_fl_results_bucket" {
  name        = "${local.ssm_prefix}/flip_fl_results_bucket"
  description = "S3 URI of the FL training-results bucket (fl-server writes; researcher downloads via browser presigned-GET)"
  type        = "String"
  value       = local.flip_fl_results_bucket_uri
}

resource "aws_ssm_parameter" "flip_app_bundles_bucket" {
  name        = "${local.ssm_prefix}/flip_app_bundles_bucket"
  description = "S3 URI of the FL app-bundles bucket (server-only; flip-api copies base → destination during FL bundling)"
  type        = "String"
  value       = local.flip_app_bundles_bucket_uri
}

resource "aws_ssm_parameter" "internal_service_key_header" {
  name        = "${local.ssm_prefix}/internal_service_key_header"
  description = "HTTP header name for fl-server -> flip-api auth"
  type        = "String"
  value       = "X-Internal-Service-Key"
}

# Networking values published for cross-account consumers. aicentre-iac's
# network_account_flip module reads these from the FLIP-Prod account to
# back the cross-account TGW VPC attachment (single authoritative value
# avoids tag-collision ambiguity during VPC migrations).

resource "aws_ssm_parameter" "vpc_id" {
  name        = "${local.ssm_prefix}/networking/vpc_id"
  description = "FLIP-Prod VPC ID — consumed cross-account by aicentre-iac's TGW VPC attachment"
  type        = "String"
  value       = module.flip_vpc.vpc_id
}

resource "aws_ssm_parameter" "private_subnet_ids" {
  name        = "${local.ssm_prefix}/networking/private_subnet_ids"
  description = "FLIP-Prod private subnet IDs (comma-separated) — consumed cross-account by aicentre-iac's TGW VPC attachment"
  type        = "StringList"
  value       = join(",", module.flip_vpc.private_subnets)
}
