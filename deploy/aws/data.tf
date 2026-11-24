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

############################
# Shared Terraform data sources
############################
#
# Centralises data sources referenced across multiple .tf files in this
# module so their declarations are explicit, not implicit cross-file
# dependencies.

# Canonical user ID of the AWS account running this Terraform — used to
# grant FULL_CONTROL on log-destination S3 buckets (CloudFront logs,
# server access logs) via legacy S3 ACL grants.
data "aws_canonical_user_id" "current" {}

# Account ID of the caller running this Terraform is declared at
# iam_ecs.tf:21 (kept there for state-locality with the IAM resources
# that consume it) and referenced elsewhere as
# `data.aws_caller_identity.current.account_id`.
