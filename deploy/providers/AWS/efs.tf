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
# Access points use posix_user uid/gid 1001 — never root. The flip-fl-base
# Dockerfiles run as a non-root user that must match this uid.

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
# (which PR 2 also creates). Until then, the file system is unreachable —
# which is the intended state during PR 1.
#
# No egress rule by design: EFS mount targets terminate NFS traffic at the
# ENI and never initiate outbound connections, so denying egress costs
# nothing. AWS provider 4.x stopped auto-creating the 0.0.0.0/0 egress that
# older docs assume — calling out the omission here so the next reader
# doesn't assume it's an oversight (see feedback_sg_egress.md).

resource "aws_security_group" "efs_mount_target" {
  name        = "efs-mount-target"
  description = "NFS 2049 from ECS fl-api and fl-server tasks (rules added in PR 2); no egress by design"
  vpc_id      = module.flip_vpc.vpc_id
}

############################
# Mount targets (one per private subnet)
############################

resource "aws_efs_mount_target" "flip_fl" {
  for_each        = var.enable_efs ? toset(module.flip_vpc.private_subnets) : toset([])
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
    fl_server_certs = {
      path  = "/fl-server-net-1/certificates"
      perms = "0750"
    }
    fl_server_keys = {
      path  = "/fl-server-net-1/keys"
      perms = "0750"
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
