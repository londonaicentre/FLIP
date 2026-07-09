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

# Three task roles + one execution role. Per-service task roles enforce least
# privilege at the IAM layer: even if fl-server is compromised via an untrusted
# FL client connection, its role cannot read AES_KEY_BASE64, trust API key
# hashes, or any other flip-api-only secret because those ARNs are absent
# from its policy.

data "aws_caller_identity" "current" {}

############################
# Trust policy (shared)
############################

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

############################
# Execution role (shared by all three services)
############################
#
# The execution role pulls images from ECR, writes container logs to
# CloudWatch, and resolves task definition `secrets` references. It does NOT
# read application secrets at runtime — that is the task role's job.

resource "aws_iam_role" "ecs_task_execution" {
  name               = "ecs-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow the execution role to resolve `secrets` references in task definitions
# to specific Secrets Manager + SSM ARNs. Scoped to FLIP resources only —
# never Resource = "*".
data "aws_iam_policy_document" "ecs_task_execution_secrets" {
  statement {
    sid       = "ReadFlipApiSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [module.flip_api_secret.secret_arn]
  }

  statement {
    sid     = "ReadFlipSsmParameters"
    actions = ["ssm:GetParameters", "ssm:GetParameter"]
    resources = [
      "arn:aws:ssm:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:parameter/flip/*",
    ]
  }

  statement {
    sid = "KmsDecryptFlipKey"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:DescribeKey",
    ]
    resources = [aws_kms_key.flip_app_key.arn]
  }
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name   = "flip-ecs-task-execution-secrets"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.ecs_task_execution_secrets.json
}

# ECR pull-through cache import (FLIP#749, LZA only). On the LZA account the
# task images come from in-account pull-through caches instead of the public
# registries — the account has no internet egress. Two cache rules exist
# (created out-of-band, not managed here): `ghcr/` mirroring
# ghcr.io/londonaicentre via a read-only PAT in the
# `ecr-pullthroughcache/ghcr` secret, and a credential-less `ecr-public/` rule
# for the EFS-provision utility image. The first pull of a new tag triggers a
# service-side upstream import, which the pulling principal must be allowed to
# perform (plus repository creation on a cache miss); already-cached pulls need
# only the managed execution-role policy above.
data "aws_iam_policy_document" "ecs_task_execution_ecr_pull_through" {
  count = var.lza_managed_network ? 1 : 0

  statement {
    sid = "EcrPullThroughCacheImport"
    actions = [
      "ecr:BatchImportUpstreamImage",
      "ecr:CreateRepository",
    ]
    resources = [
      "arn:aws:ecr:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:repository/ghcr/*",
      "arn:aws:ecr:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:repository/ecr-public/*",
    ]
  }
}

resource "aws_iam_role_policy" "ecs_task_execution_ecr_pull_through" {
  count  = var.lza_managed_network ? 1 : 0
  name   = "flip-ecs-task-execution-ecr-pull-through"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.ecs_task_execution_ecr_pull_through[0].json
}

############################
# flip-api task role
############################
#
# flip-api needs full access to flip-api-only secrets, the user pool for
# Cognito admin operations, the verified SES identity for emails, and the
# FLIP S3 buckets for model file IO.

resource "aws_iam_role" "ecs_flip_api_task" {
  name               = "ecs-flip-api-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "ecs_flip_api_task" {
  statement {
    sid       = "ReadFlipApiSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [module.flip_api_secret.secret_arn]
  }

  # ECS Exec transport (`aws ecs execute-command`, gated service-side by
  # var.ecs_exec_enabled). Without these the ExecuteCommandAgent reports
  # RUNNING but its SSM control channel never opens and every exec fails
  # TargetNotConnected — needed e.g. for hub-side register_trust against the
  # ECS hub (README "Registering trusts against the ECS hub"; FLIP#938).
  # ssmmessages has no resource-level scoping.
  statement {
    sid = "EcsExecSsmMessages"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }

  # Mint IAM auth tokens to connect through RDS Proxy (FLIP#556). Scoped to the
  # one proxy + the single DB user flip-api connects as — the proxy itself uses
  # the master secret to reach RDS, so no static DB password lives in the app.
  statement {
    sid     = "RdsProxyIamConnect"
    actions = ["rds-db:connect"]
    resources = [
      "arn:aws:rds-db:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:dbuser:${local.rds_proxy_resource_id}/${var.POSTGRES_USER}",
    ]
  }

  statement {
    sid = "CognitoUserPool"
    actions = [
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminDeleteUser",
      "cognito-idp:AdminGetUser",
      "cognito-idp:AdminInitiateAuth",
      "cognito-idp:AdminRespondToAuthChallenge",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:AdminUserGlobalSignOut",
      "cognito-idp:DescribeUserPool",
      "cognito-idp:DescribeUserPoolClient",
      "cognito-idp:ListUsers",
    ]
    resources = [module.cognito.user_pool_arn]
  }

  statement {
    sid       = "SesSend"
    actions   = ["ses:SendEmail", "ses:SendRawEmail", "ses:SendTemplatedEmail"]
    resources = [module.ses.sender_identity_arn]
  }

  statement {
    sid = "S3FlipBuckets"
    # s3:CopyObject is not a real IAM action — AWS implements server-side copy
    # via s3:GetObject on the source + s3:PutObject on the destination, both
    # already granted below — so we don't list CopyObject explicitly.
    actions = [
      "s3:DeleteObject",
      "s3:GetBucketLocation",
      "s3:GetObject",
      "s3:HeadObject",
      "s3:ListBucket",
      "s3:PutObject",
    ]
    resources = [
      module.flip_model_files_uploads_bucket.bucket_arn,
      "${module.flip_model_files_uploads_bucket.bucket_arn}/*",
      module.flip_fl_results_bucket.bucket_arn,
      "${module.flip_fl_results_bucket.bucket_arn}/*",
      module.flip_app_bundles_bucket.bucket_arn,
      "${module.flip_app_bundles_bucket.bucket_arn}/*",
      aws_s3_bucket.aicentre_bucket.arn,
      "${aws_s3_bucket.aicentre_bucket.arn}/*",
    ]
  }

  statement {
    sid = "KmsDecryptFlipKey"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:DescribeKey",
    ]
    resources = [aws_kms_key.flip_app_key.arn]
  }

  # Ephemeral SSM parameter handoff for register_trust (S-1 fix).
  # `register-trusts.sh` mints a one-off parameter under this prefix before
  # spawning the ECS task; the task writes the kit JSON here as a
  # SecureString (encrypted with the AWS-managed `alias/aws/ssm`, no extra
  # KMS grant needed); the deploy script reads + deletes it immediately
  # after. Scope is intentionally narrow — only the ephemeral prefix.
  statement {
    sid = "SsmTrustKitEphemeralWrite"
    actions = [
      "ssm:PutParameter",
      "ssm:DeleteParameter",
    ]
    resources = ["arn:aws:ssm:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:parameter/flip/trust-kits/ephemeral/*"]
  }

  # Runtime read of the FL kit-slot pool names (resolve_fl_kit_slot_names): read at
  # boot seeding and re-read when a registration finds the pool exhausted, so the
  # pool grows on `make apply-fl-kit-slots` with no restart. Scoped to the single
  # parameter — flip-api needs no other /flip/* reads at runtime.
  statement {
    sid       = "SsmFlKitSlotNamesRead"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.fl_kit_slot_names.arn]
  }
}

resource "aws_iam_role_policy" "ecs_flip_api_task" {
  name   = "flip-api-task-policy"
  role   = aws_iam_role.ecs_flip_api_task.id
  policy = data.aws_iam_policy_document.ecs_flip_api_task.json
}

############################
# fl-api task role
############################
#
# fl-api is internal-only and orchestrates FL training jobs against fl-server.
# It does not read application secrets and does not need S3, Cognito, or SES.
# CloudWatch Logs is granted via the execution role (LogConfiguration writes
# come from the agent, not the task role). The only task-role grant is the
# ECS Exec transport (see EcsExecSsmMessages on flip-api) — without it
# `aws ecs execute-command` into fl-api fails TargetNotConnected, so there
# is no way to inspect a stuck FL run (e.g. read `flwr ls`/`flwr log`
# against the SuperLink) on ECS.

data "aws_iam_policy_document" "ecs_fl_api_task" {
  statement {
    sid = "EcsExecSsmMessages"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "ecs_fl_api_task" {
  name               = "ecs-fl-api-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "ecs_fl_api_task" {
  name   = "fl-api-task-policy"
  role   = aws_iam_role.ecs_fl_api_task.id
  policy = data.aws_iam_policy_document.ecs_fl_api_task.json
}

############################
# fl-server task role
############################
#
# fl-server is reachable from untrusted FL clients via the NLB. Its role is
# minimal: read its INTERNAL_SERVICE_KEY from the FLIP_API secret (so it can
# call back to flip-api on /api/model/{id}/status) and write training results
# to the dedicated flip-fl-results bucket. Crucially, it has NO access to
# AES_KEY_BASE64, the model-files-uploads or app-bundles
# buckets, or any flip-api-only data. The secret is shared today (single
# FLIP_API secret) so the execution role's GetSecretValue covers fetch; the
# task role here only needs to expose ListSecretVersionIds for runtime
# introspection if needed — kept empty until PR 2 wires actual runtime calls.

resource "aws_iam_role" "ecs_fl_server_task" {
  name               = "ecs-fl-server-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "ecs_fl_server_task" {
  # ECS Exec transport (see EcsExecSsmMessages on flip-api) — without it
  # `aws ecs execute-command` into fl-server fails TargetNotConnected, so a
  # stuck SuperLink/ServerApp can't be inspected live on ECS.
  statement {
    sid = "EcsExecSsmMessages"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }

  # The whole flip-fl-results bucket is dedicated to FL training output, so the
  # prefix-scoped condition that used to constrain access to
  # `${flip_bucket}/uploaded_federated_data/*` is no longer needed — bucket-wide
  # scope is now the same least-privilege boundary.
  statement {
    sid     = "S3FlResults"
    actions = ["s3:PutObject", "s3:GetObject", "s3:HeadObject", "s3:DeleteObject"]
    resources = [
      "${module.flip_fl_results_bucket.bucket_arn}/*",
    ]
  }

  statement {
    sid       = "S3ListFlResultsBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [module.flip_fl_results_bucket.bucket_arn]
  }

  # Read access to the FL participant kits on the AICENTRE bucket — the
  # NVFLARE kit prefix and the Flower creds prefix (#566). Used by the
  # one-shot efs-provision-certs task (which runs under this role and syncs
  # the backend's kit/creds into EFS at boot). Runtime fl-server never reads
  # from these prefixes - the data lives on EFS by the time the service
  # starts.
  statement {
    sid     = "S3ReadFlKit"
    actions = ["s3:GetObject", "s3:HeadObject"]
    resources = [
      "${aws_s3_bucket.aicentre_bucket.arn}/fl-flare-participant-kits/*",
      "${aws_s3_bucket.aicentre_bucket.arn}/fl-flower-participant-kits/*",
    ]
  }

  statement {
    sid       = "S3ListAicentreBucketForKit"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.aicentre_bucket.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "fl-flare-participant-kits/*", "fl-flare-participant-kits",
        "fl-flower-participant-kits/*", "fl-flower-participant-kits",
      ]
    }
  }

  statement {
    sid = "KmsDecryptFlipKey"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:DescribeKey",
    ]
    resources = [aws_kms_key.flip_app_key.arn]
  }
}

resource "aws_iam_role_policy" "ecs_fl_server_task" {
  name   = "fl-server-task-policy"
  role   = aws_iam_role.ecs_fl_server_task.id
  policy = data.aws_iam_policy_document.ecs_fl_server_task.json
}
