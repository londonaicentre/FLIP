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
  # checkov:skip=CKV2_AWS_34:non-secret operational config by design (see file header) — SecureString adds KMS coupling for no confidentiality gain
  name        = "${local.ssm_prefix}/flip_api_internal_url"
  description = "Internal hostname:port for fl-server -> flip-api callbacks (Service Discovery)"
  type        = "String"
  value       = "http://${local.service_discovery_names.flip_api}:${local.api_container_port}/api"
}

resource "aws_ssm_parameter" "flip_model_files_uploads_bucket" {
  # checkov:skip=CKV2_AWS_34:non-secret operational config by design (see file header) — SecureString adds KMS coupling for no confidentiality gain
  name        = "${local.ssm_prefix}/flip_model_files_uploads_bucket"
  description = "S3 URI of the researcher model-files-uploads bucket (browser presigned-PUT target today; flips to presigned POST once PR #438 lands; flip-api reads/deletes)"
  type        = "String"
  value       = local.flip_model_files_uploads_bucket_uri
}

resource "aws_ssm_parameter" "flip_fl_results_bucket" {
  # checkov:skip=CKV2_AWS_34:non-secret operational config by design (see file header) — SecureString adds KMS coupling for no confidentiality gain
  name        = "${local.ssm_prefix}/flip_fl_results_bucket"
  description = "S3 URI of the FL training-results bucket (fl-server writes; researcher downloads via browser presigned-GET)"
  type        = "String"
  value       = local.flip_fl_results_bucket_uri
}

resource "aws_ssm_parameter" "flip_app_bundles_bucket" {
  # checkov:skip=CKV2_AWS_34:non-secret operational config by design (see file header) — SecureString adds KMS coupling for no confidentiality gain
  name        = "${local.ssm_prefix}/flip_app_bundles_bucket"
  description = "S3 URI of the FL app-bundles bucket (server-only; flip-api copies base → destination during FL bundling)"
  type        = "String"
  value       = local.flip_app_bundles_bucket_uri
}

# Networking values published for cross-account consumers. aicentre-iac's
# network_account_flip module reads these from the FLIP-Prod account to
# back the cross-account TGW VPC attachment (single authoritative value
# avoids tag-collision ambiguity during VPC migrations).
#
# Gated off on the LZA account (FLIP#749): its TGW attachment is provisioned by
# the accelerator pipeline, not by aicentre-iac, so nothing consumes these
# there — they are the legacy TGW coupling only.

resource "aws_ssm_parameter" "vpc_id" {
  # checkov:skip=CKV2_AWS_34:non-secret networking value read CROSS-ACCOUNT by aicentre-iac — an AWS-managed CMK cannot be decrypted from another account
  count       = var.lza_managed_network ? 0 : 1
  name        = "${local.ssm_prefix}/networking/vpc_id"
  description = "FLIP-Prod VPC ID — consumed cross-account by aicentre-iac's TGW VPC attachment"
  type        = "String"
  value       = module.flip_vpc.vpc_id
}

resource "aws_ssm_parameter" "private_subnet_ids" {
  # checkov:skip=CKV2_AWS_34:non-secret networking value read CROSS-ACCOUNT by aicentre-iac — an AWS-managed CMK cannot be decrypted from another account
  count       = var.lza_managed_network ? 0 : 1
  name        = "${local.ssm_prefix}/networking/private_subnet_ids"
  description = "FLIP-Prod private subnet IDs (comma-separated) — consumed cross-account by aicentre-iac's TGW VPC attachment"
  type        = "StringList"
  value       = join(",", module.flip_vpc.private_subnets)
}

# State migration for the counts added above (FLIP#749): keeps existing legacy
# states aligned without a manual `terraform state mv`. Safe to remove once
# every live state file has been migrated.
moved {
  from = aws_ssm_parameter.vpc_id
  to   = aws_ssm_parameter.vpc_id[0]
}

moved {
  from = aws_ssm_parameter.private_subnet_ids
  to   = aws_ssm_parameter.private_subnet_ids[0]
}

# FL kit-slot pool names — flip-api's runtime source in production, read at boot
# seeding and re-read when a trust registration finds the pool exhausted
# (resolve_fl_kit_slot_names, reconcile-on-miss). Growing the pool is an env-file
# edit + `make apply-fl-kit-slots` (targeted apply of this parameter): no
# task-definition change, no flip-api restart. The value stays the JSON-list
# *string* from the env file (flip-api json.loads it). Not a secret — just the
# roster of pre-provisioned kit slots, hence SSM per the /flip convention above.
resource "aws_ssm_parameter" "fl_kit_slot_names" {
  # checkov:skip=CKV2_AWS_34:non-secret operational config by design (see file header and the comment above) — SecureString adds KMS coupling for no confidentiality gain
  name        = "${local.ssm_prefix}/fl_kit_slot_names"
  description = "JSON list of FL kit-slot names seeding/reconciling flip-api's fl_kit_slot pool (register_trust claims from it)"
  type        = "String"
  value       = var.FL_KIT_SLOT_NAMES
}

# The EC2 keypair's public key, for the Terraform CI workflows (FLIP#962,
# FLIP#1199). Both aws_key_pair resources read their key with file() from
# ~/.ssh/host-aws.pub, and public_key is ForceNew — a runner without that file
# would plan a replacement of both keypairs that ripples into
# aws_instance.ec2_instance. So each workflow reads this parameter and writes
# the file before Terraform runs.
#
# Declared here rather than published by a make target so the whole CI
# bootstrap is code. The loop is stable: CI reads the parameter, writes the
# file, the keypair reads the file, and this resource writes the same bytes
# back — no diff. On a fresh account it is created by the first laptop apply,
# which the bootstrap needs anyway; CI cannot run before it exists.
#
# overwrite = true adopts the parameter the retired `make seed-ci-keypair-param`
# already created in the self-contained accounts. Without it, the first apply
# there fails with ParameterAlreadyExists — and CI applies stag on every merge
# to develop. The value it writes is the one CI has just read from it, so
# adoption changes nothing.
#
# The precondition keeps the check that make target used to make: CI can only
# supply ONE file, so the two keypairs must hold the same key, or CI would
# silently plan a replacement of whichever one differs.
resource "aws_ssm_parameter" "ci_host_aws_public_key" {
  # checkov:skip=CKV2_AWS_34:a PUBLIC key, non-secret by definition — SecureString adds KMS coupling for no confidentiality gain
  name        = "${local.ssm_prefix}/ci/host_aws_public_key"
  description = "EC2 keypair public key, read by the Terraform CI workflows (FLIP#962)"
  type        = "String"
  value       = aws_key_pair.host_key.public_key
  overwrite   = true

  lifecycle {
    precondition {
      condition     = aws_key_pair.flip_keypair.public_key == aws_key_pair.host_key.public_key
      error_message = "aws_key_pair.flip_keypair and aws_key_pair.host_key hold different public keys. Both are read from ~/.ssh/host-aws.pub and CI can only supply one file, so reconcile them from a laptop before enabling Terraform CI."
    }
  }
}
