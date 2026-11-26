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

# EFS for fl-api and fl-server provisioned-secret directories. On the EC2 host
# these live at /opt/flip/services/... and are populated by Ansible. On Fargate
# tasks lose local state on every restart, so the cert/admin/transfer
# directories must persist on EFS and be mounted via access points.
#
# Access points use posix_user uid/gid 1001 — never root. This is an
# override, not a match requirement: NFSv4 access points remap every
# reader/writer to 1001 regardless of the calling container's own uid, so the
# FL service Dockerfiles (built non-root at UID=1000/GID=1000/UNAME=flip,
# GHSA-8465 — 1000 to match the ubuntu uid EC2/on-prem trust hosts stage kits
# as) don't need to match 1001 themselves; flare-fl-api already runs as uid
# 1000 against these same access points in prod.

############################
# File system
############################

resource "aws_efs_file_system" "flip_fl" {
  count = var.enable_efs ? 1 : 0

  creation_token = "flip-fl-efs"
  encrypted      = true

  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"

  tags = {
    Name = "flip-fl-efs"
  }
}

############################
# Mount target security group
############################
#
# Empty-by-design in PR 1. PR 2 adds standalone aws_security_group_rule
# resources allowing ingress 2049 from the ecs_fl_api and ecs_fl_server SGs
# (which PR 2 also creates).
#
# No egress rule by design: EFS mount targets terminate NFS traffic at the
# ENI and never initiate outbound connections, so denying egress costs
# nothing. AWS provider 4.x stopped auto-creating the 0.0.0.0/0 egress that
# older docs assume — calling out the omission here so the next reader
# doesn't assume it's an oversight (see feedback_sg_egress.md).

resource "aws_security_group" "efs_mount_target" {
  name        = "efs-mount-target"
  description = "NFS 2049 from ECS fl-api and fl-server tasks (rules added in PR 2); no egress by design"
  vpc_id      = local.vpc_id

  tags = {
    FlipSG = "true"
  }
}

############################
# Mount targets (one per private subnet)
############################

resource "aws_efs_mount_target" "flip_fl" {
  for_each        = var.enable_efs ? toset(local.app_subnet_ids) : toset([])
  file_system_id  = aws_efs_file_system.flip_fl[0].id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs_mount_target.id]
}

############################
# Access points
############################

locals {
  efs_access_points = {
    fl_api_local = {
      path  = "/fl-api-net-1/local"
      perms = "0755"
    }
    fl_api_startup = {
      path  = "/fl-api-net-1/startup"
      perms = "0755"
    }
    fl_server_local = {
      path  = "/fl-server-net-1/local"
      perms = "0755"
    }
    fl_server_startup = {
      path  = "/fl-server-net-1/startup"
      perms = "0755"
    }
    fl_server_transfer = {
      path  = "/fl-server-net-1/transfer"
      perms = "0755"
    }
    # Shared checkpoint-staging volume. The fl-api de-bundles large evaluation
    # checkpoints (e.g. the ~759 MiB Ark+ weights) out of the client app and
    # writes them to SERVER_CHECKPOINT_ROOT/<model_id>/ (see FLIP#695); the
    # fl-server's EvaluationModelLocator reads them back from the same path.
    # This ONE access point is mounted at /app/server-checkpoints on BOTH the
    # fl-api-net-1 (writer) and fl-server-net-1 (reader) tasks — mirroring the
    # single `${FL_JOBS_DIR}/net-1:/app/server-checkpoints` bind shared between
    # the two services in compose.prod.nvflare.yml. Both containers run as
    # uid/gid 1001, which the access point pins, so writer and reader agree on
    # ownership. 0775 lets the group write the per-model subdirs fl-api mkdirs.
    fl_checkpoints = {
      path  = "/fl-server-net-1/server-checkpoints"
      perms = "0775"
    }
    fl_server_certs = {
      path  = "/fl-server-net-1/certificates"
      perms = "0750"
    }
    fl_server_keys = {
      path  = "/fl-server-net-1/keys"
      perms = "0750"
    }
    # Flower shared jobs volume — the ECS analogue of compose's
    # ${FL_JOBS_DIR}/net-1 bind: fl-api de-bundles uploaded apps (and eval
    # checkpoints) into /app/src/<model_id>/, the SuperLink's ServerApp reads
    # them back via the flip-job-dir run-config. Mounted RW on BOTH
    # fl-api-net-1 and fl-server-net-1 when fl_backend=flower; 0775 for the
    # same group-write reason as fl_checkpoints. Unused by FLARE.
    fl_jobs = {
      path  = "/fl-net-1/jobs"
      perms = "0775"
    }
  }
}

resource "aws_efs_access_point" "flip_fl" {
  for_each       = var.enable_efs ? local.efs_access_points : {}
  file_system_id = aws_efs_file_system.flip_fl[0].id

  posix_user {
    uid = 1001
    gid = 1001
  }

  root_directory {
    path = each.value.path
    creation_info {
      owner_uid   = 1001
      owner_gid   = 1001
      permissions = each.value.perms
    }
  }

  tags = {
    Name = "flip-fl-${each.key}"
  }
}

############################
# File system policy
############################
#
# Without a file system policy EFS applies its default, which grants every
# client that can reach a mount target full access — including root — whoever
# they are. The security group already limits that to the fl-api and fl-server
# task SGs, but nothing in the file system itself says so. This policy is the
# second layer (FLIP-SR-075, #1743):
#
#   1. non-TLS NFS connections are denied outright, and
#   2. mounting is allowed only to the hub's own three FL task roles, and only
#      through one of the access points declared above.
#
# Every mount of this file system already sets `transit_encryption = "ENABLED"`
# and `authorization_config { iam = "ENABLED" }` (fl-api-net-1 and
# fl-server-net-1 in ecs_tasks.tf, efs-provision-certs in
# ecs_efs_provision.tf, flower-register-supernode-keys in ecs_flower.tf), so
# every mount presents its task role as the NFS client identity and satisfies
# both statements unchanged — no task definition changes, no re-registration,
# no quiesce.
#
# Shape notes, both load-bearing:
#
#   * The Allow names `Principal = { AWS = "*" }` and scopes with the
#     `aws:PrincipalArn` condition rather than listing the role ARNs as
#     principals. A resource-based policy that grants to a *role ARN* principal
#     is still subject to an implicit deny in that role's permissions boundary,
#     and these roles carry the platform boundary
#     (`var.iam_permissions_boundary_name`, AGENTS.md "Terraform CI"), whose
#     contents this repository does not own. Granting through
#     `aws:PrincipalArn` with a wildcard principal is not limited by a
#     boundary's implicit deny, so the live mounts cannot be broken by a
#     boundary that happens to omit `elasticfilesystem:*`.
#   * `elasticfilesystem:ClientRootAccess` is granted to nobody: the access
#     points pin every client to uid/gid 1001, so no mount needs root. It is
#     left as an implicit deny (not an explicit one) so that a break-glass
#     identity policy remains possible without first editing this file.
#
# The task roles need no identity-policy grant of their own: within one account
# an Allow in the file system policy is sufficient (ecs-fl-server-task-role in
# particular still grants ECS Exec and nothing else).

resource "aws_efs_file_system_policy" "flip_fl" {
  count          = var.enable_efs ? 1 : 0
  file_system_id = aws_efs_file_system.flip_fl[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyUnencryptedTransport"
        Effect    = "Deny"
        Principal = { AWS = "*" }
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
          "elasticfilesystem:ClientRootAccess",
        ]
        Resource = aws_efs_file_system.flip_fl[0].arn
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        Sid       = "AllowHubFlTaskRolesThroughAccessPoints"
        Effect    = "Allow"
        Principal = { AWS = "*" }
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite",
        ]
        Resource = aws_efs_file_system.flip_fl[0].arn
        Condition = {
          StringEquals = {
            "aws:PrincipalArn" = [
              aws_iam_role.ecs_fl_api_task.arn,
              aws_iam_role.ecs_fl_server_task.arn,
              aws_iam_role.ecs_fl_kit_provision_task.arn,
            ]
            "elasticfilesystem:AccessPointArn" = [for ap in aws_efs_access_point.flip_fl : ap.arn]
          }
        }
      },
    ]
  })
}

############################
# SG ingress: NFS from ECS tasks
############################

resource "aws_security_group_rule" "efs_ingress_ecs_fl_api" {
  count                    = var.enable_efs ? 1 : 0
  type                     = "ingress"
  description              = "NFS from ecs_fl_api tasks"
  from_port                = 2049
  to_port                  = 2049
  protocol                 = "tcp"
  security_group_id        = aws_security_group.efs_mount_target.id
  source_security_group_id = aws_security_group.ecs_fl_api.id
}

resource "aws_security_group_rule" "efs_ingress_ecs_fl_server" {
  count                    = var.enable_efs ? 1 : 0
  type                     = "ingress"
  description              = "NFS from ecs_fl_server tasks"
  from_port                = 2049
  to_port                  = 2049
  protocol                 = "tcp"
  security_group_id        = aws_security_group.efs_mount_target.id
  source_security_group_id = aws_security_group.ecs_fl_server.id
}
