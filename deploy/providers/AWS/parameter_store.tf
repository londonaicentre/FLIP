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

resource "aws_ssm_parameter" "uploaded_federated_data_bucket" {
  name        = "${local.ssm_prefix}/uploaded_federated_data_bucket"
  description = "S3 URI prefix for FL training results (must NOT default to s3://default-bucket)"
  type        = "String"
  value       = local.uploaded_federated_data_uri
}

resource "aws_ssm_parameter" "internal_service_key_header" {
  name        = "${local.ssm_prefix}/internal_service_key_header"
  description = "HTTP header name for fl-server -> flip-api auth"
  type        = "String"
  value       = "X-Internal-Service-Key"
}
