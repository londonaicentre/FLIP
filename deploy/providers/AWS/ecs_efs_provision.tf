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

# One-shot provisioning task that syncs FL certificates and keys from S3
# onto the EFS file system before the fl-api and fl-server ECS services
# start. On EC2 this was done by Ansible (site.yml copying to
# /opt/flip/services/...). On Fargate the EFS access points start empty,
# so we need a bootstrap run that creates the required directory layout.

locals {
  fl_provision_base_s3 = "s3://${aws_s3_bucket.flip_bucket.id}/base-application/${var.fl_backend}/${var.flare_kit_date}"
}

resource "null_resource" "provision_efs_certs" {
  triggers = {
    s3_source = local.fl_provision_base_s3
    task_def  = aws_ecs_task_definition.efs_provision.arn
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws ecs run-task \
        --cluster ${aws_ecs_cluster.flip.name} \
        --task-definition ${aws_ecs_task_definition.efs_provision.arn} \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[${join(",", module.flip_vpc.private_subnets)}],securityGroups=[${aws_security_group.ecs_fl_server.id}],assignPublicIp=DISABLED}" \
        --count 1 \
        --region ${var.AWS_REGION} \
        --no-cli-pager
    EOT
    interpreter = ["bash", "-c"]
  }
}

resource "aws_ecs_task_definition" "efs_provision" {
  family                   = "efs-provision-certs"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_fl_server_task.arn

  container_definitions = jsonencode([
    {
      name    = "provision-efs-certs"
      image   = "amazon/aws-cli:latest"
      command = [
        "/bin/sh", "-c",
        <<-SCRIPT
        set -e
        S3_BASE=${local.fl_provision_base_s3}
        echo "Syncing certs from $S3_BASE to EFS..."

        # fl-api-net-1
        mkdir -p /mnt/fl-api/local /mnt/fl-api/startup
        aws s3 sync "$S3_BASE/fl-api-net-1/local/" /mnt/fl-api/local/ --delete || true
        aws s3 sync "$S3_BASE/fl-api-net-1/startup/" /mnt/fl-api/startup/ --delete || true

        # fl-server-net-1
        mkdir -p /mnt/fl-server/local /mnt/fl-server/startup /mnt/fl-server/transfer /mnt/fl-server/certs /mnt/fl-server/keys
        aws s3 sync "$S3_BASE/fl-server-net-1/local/" /mnt/fl-server/local/ --delete || true
        aws s3 sync "$S3_BASE/fl-server-net-1/startup/" /mnt/fl-server/startup/ --delete || true
        aws s3 sync "$S3_BASE/fl-server-net-1/transfer/" /mnt/fl-server/transfer/ --delete || true
        aws s3 sync "$S3_BASE/fl-server-net-1/certs/" /mnt/fl-server/certs/ --delete || true
        aws s3 sync "$S3_BASE/fl-server-net-1/keys/" /mnt/fl-server/keys/ --delete || true

        echo "EFS provisioning complete."
        SCRIPT
      ]

      mountPoints = [
        {
          sourceVolume  = "efs-fl-api-local"
          containerPath = "/mnt/fl-api/local"
        },
        {
          sourceVolume  = "efs-fl-api-startup"
          containerPath = "/mnt/fl-api/startup"
        },
        {
          sourceVolume  = "efs-fl-server-local"
          containerPath = "/mnt/fl-server/local"
        },
        {
          sourceVolume  = "efs-fl-server-startup"
          containerPath = "/mnt/fl-server/startup"
        },
        {
          sourceVolume  = "efs-fl-server-transfer"
          containerPath = "/mnt/fl-server/transfer"
        },
        {
          sourceVolume  = "efs-fl-server-certs"
          containerPath = "/mnt/fl-server/certs"
        },
        {
          sourceVolume  = "efs-fl-server-keys"
          containerPath = "/mnt/fl-server/keys"
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs_fl_server.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "efs-provision"
        }
      }
    }
  ])

  # Mount the same EFS access points as the runtime services
  dynamic "volume" {
    for_each = {
      "efs-fl-api-local"    = "fl_api_local"
      "efs-fl-api-startup"  = "fl_api_startup"
      "efs-fl-server-local" = "fl_server_local"
      "efs-fl-server-startup" = "fl_server_startup"
      "efs-fl-server-transfer" = "fl_server_transfer"
      "efs-fl-server-certs" = "fl_server_certs"
      "efs-fl-server-keys"  = "fl_server_keys"
    }
    content {
      name = volume.key
      efs_volume_configuration {
        file_system_id = aws_efs_file_system.flip_fl[0].id
        root_directory = "/"
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = aws_efs_access_point.flip_fl[volume.value].id
          iam             = "ENABLED"
        }
      }
    }
  }
}
