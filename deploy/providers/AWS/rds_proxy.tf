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
# RDS Proxy + IAM database authentication
############################
#
# Why this exists (FLIP#556): flip-api used to read the RDS master password
# from Secrets Manager once at boot and bake it into the SQLAlchemy engine URL.
# The RDS-managed master secret (`rds!db-…`) auto-rotates on AWS's schedule, so
# every rotation left the long-running ECS task holding a stale password and
# took prod DB connectivity down until a manual `force-new-deployment`.
#
# RDS Proxy removes the static credential from the application entirely:
#   - flip-api authenticates to the *proxy* with a short-lived IAM auth token
#     minted per-connection (see flip-api/src/flip_api/db/database.py), gated by
#     the `rds-db:connect` grant on the task role (iam_ecs.tf).
#   - the *proxy* authenticates to RDS using the rotating master secret, which
#     it re-reads natively on rotation — so rotation is a non-event for the app.
#
# Because the proxy→RDS leg uses the secret (not IAM), the Postgres user needs
# no `rds_iam` grant; nothing changes inside the database.
#
# The RDS-managed master secret is encrypted with the AWS-managed
# `aws/secretsmanager` KMS key; the proxy's role needs to decrypt it.
data "aws_kms_alias" "secretsmanager" {
  name = "alias/aws/secretsmanager"
}

locals {
  # The `rds-db:connect` IAM ARN (iam_ecs.tf) needs the proxy's resource id
  # (prx-…), which `aws_db_proxy` does not expose as its own attribute — pull it
  # off the end of the ARN (arn:aws:rds:<region>:<account>:db-proxy:prx-…).
  rds_proxy_resource_id = element(split(":", aws_db_proxy.flip_db.arn), 6)
}

############################
# Proxy security group
############################
#
# Standalone aws_security_group + standalone aws_security_group_rule resources
# (never inline rules) to match ecs_sg.tf and avoid provider plan/apply drift.
# Egress is locked to RDS:5432 only — the proxy talks to nothing else over the
# VPC network (it reaches Secrets Manager/KMS via its IAM role, not the SG).

resource "aws_security_group" "rds_proxy" {
  name        = "rds-proxy"
  description = "RDS Proxy - inbound 5432 from flip-api, outbound 5432 to RDS"
  vpc_id      = module.flip_vpc.vpc_id

  tags = {
    FlipSG = "true"
  }
}

resource "aws_security_group_rule" "rds_proxy_ingress_flip_api" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  description              = "PostgreSQL from ECS flip-api"
  source_security_group_id = aws_security_group.ecs_flip_api.id
  security_group_id        = aws_security_group.rds_proxy.id
}

resource "aws_security_group_rule" "rds_proxy_egress_rds" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  description              = "PostgreSQL to RDS"
  source_security_group_id = module.rds_security_group.security_group.id
  security_group_id        = aws_security_group.rds_proxy.id
}

# Let the proxy's connections into RDS. This is flip-api's only path to the
# database — the direct ECS→RDS ingress rule was removed (see main.tf).
resource "aws_security_group_rule" "rds_ingress_proxy" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  description              = "PostgreSQL from RDS Proxy"
  source_security_group_id = aws_security_group.rds_proxy.id
  security_group_id        = module.rds_security_group.security_group.id
}

############################
# Proxy IAM role
############################
#
# The proxy assumes this role to read the master secret from Secrets Manager
# and decrypt it. Scoped to exactly the one secret + the secretsmanager KMS key.

data "aws_iam_policy_document" "rds_proxy_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "rds_proxy" {
  name               = "flip-rds-proxy-role"
  assume_role_policy = data.aws_iam_policy_document.rds_proxy_assume.json
}

data "aws_iam_policy_document" "rds_proxy" {
  statement {
    sid       = "GetMasterUserSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [module.flip_db.db_instance_master_user_secret_arn]
  }

  statement {
    sid       = "DecryptMasterUserSecret"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.secretsmanager.target_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.AWS_REGION}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "rds_proxy" {
  name   = "flip-rds-proxy-secret-access"
  role   = aws_iam_role.rds_proxy.id
  policy = data.aws_iam_policy_document.rds_proxy.json
}

############################
# Proxy
############################

resource "aws_db_proxy" "flip_db" {
  name                   = "flip-database-proxy"
  engine_family          = "POSTGRESQL"
  require_tls            = true
  idle_client_timeout    = 1800
  role_arn               = aws_iam_role.rds_proxy.arn
  vpc_subnet_ids         = module.flip_vpc.private_subnets
  vpc_security_group_ids = [aws_security_group.rds_proxy.id]

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "REQUIRED"
    secret_arn  = module.flip_db.db_instance_master_user_secret_arn
  }
}

resource "aws_db_proxy_default_target_group" "flip_db" {
  db_proxy_name = aws_db_proxy.flip_db.name

  connection_pool_config {
    max_connections_percent      = 100
    max_idle_connections_percent = 50
  }
}

resource "aws_db_proxy_target" "flip_db" {
  db_instance_identifier = module.flip_db.db_instance_identifier
  db_proxy_name          = aws_db_proxy.flip_db.name
  target_group_name      = aws_db_proxy_default_target_group.flip_db.name
}
