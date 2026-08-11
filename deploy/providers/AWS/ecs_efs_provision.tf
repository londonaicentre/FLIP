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
  # NVFLARE participant kits are produced by `nvflare provision` and uploaded
  # to the AI Centre bucket - NOT the FLIP bucket. The path mirrors the
  # source-of-truth used by site.yml's central-hub kit sync (lines 168-169).
  # Bucket: aicentre_bucket; layout:
  #   fl-flare-participant-kits/{date}/net-1/services/fl-server-net-1/{startup,local}/...
  #   fl-flare-participant-kits/{date}/net-1/services/flip-fl-api-net-1/{startup,local}/...
  # NB: the fl-api kit dir on S3 keeps the docker-prefix `flip-fl-api-net-1`.
  fl_provision_base_s3 = "s3://${aws_s3_bucket.aicentre_bucket.id}/fl-flare-participant-kits/${var.flare_kit_date}/net-1/services"

  # Flower creds are produced by `make -C fl-services/flower provision` (with
  # FLOWER_EXTRA_SERVER_SANS covering the Cloud Map + public FL hostnames)
  # and uploaded by its `upload-creds-to-s3` target. Layout:
  #   fl-flower-creds/{date}/net-1/{certificates,keys}/...
  flower_creds_base_s3 = "s3://${aws_s3_bucket.aicentre_bucket.id}/fl-flower-creds/${var.flower_creds_date}/net-1"
}

resource "null_resource" "provision_efs_certs" {
  count = var.enable_efs ? 1 : 0

  triggers = {
    s3_source = var.fl_backend == "flower" ? local.flower_creds_base_s3 : local.fl_provision_base_s3
    task_def  = aws_ecs_task_definition.efs_provision[0].arn
  }

  lifecycle {
    precondition {
      condition     = var.fl_backend != "flower" || var.flower_creds_date != ""
      error_message = "fl_backend=flower needs FLOWER_CREDS_DATE set (S3 path fl-flower-creds/<date>/ — provision + upload the creds first)."
    }
  }

  provisioner "local-exec" {
    command     = <<-EOT
      TASK_ARN=$(aws ecs run-task \
        --cluster ${aws_ecs_cluster.flip.name} \
        --task-definition ${aws_ecs_task_definition.efs_provision[0].arn} \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[${join(",", module.flip_vpc.private_subnets)}],securityGroups=[${aws_security_group.ecs_fl_server.id}],assignPublicIp=DISABLED}" \
        --count 1 \
        --region ${var.AWS_REGION} \
        --no-cli-pager \
        --query 'tasks[0].taskArn' \
        --output text)

      echo "Provisioning task ARN: $TASK_ARN"

      aws ecs wait tasks-stopped \
        --cluster ${aws_ecs_cluster.flip.name} \
        --tasks "$TASK_ARN" \
        --region ${var.AWS_REGION}

      EXIT_CODE=$(aws ecs describe-tasks \
        --cluster ${aws_ecs_cluster.flip.name} \
        --tasks "$TASK_ARN" \
        --region ${var.AWS_REGION} \
        --query 'tasks[0].containers[0].exitCode' \
        --output text)

      if [ "$EXIT_CODE" != "0" ]; then
        echo "EFS provisioning task failed with exit code $EXIT_CODE"
        exit 1
      fi

      echo "EFS provisioning task completed successfully."
    EOT
    interpreter = ["bash", "-c"]
  }
}

resource "aws_ecs_task_definition" "efs_provision" {
  count                    = var.enable_efs ? 1 : 0
  family                   = "efs-provision-certs"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_fl_server_task.arn

  container_definitions = jsonencode([
    {
      name  = "provision-efs-certs"
      image = "amazon/aws-cli:2.22.35"
      # The amazon/aws-cli image's ENTRYPOINT is `aws`, so a command like
      # ["/bin/sh", "-c", ...] would get appended as args to aws and fail
      # with "Found invalid choice '/bin/sh'". Override entryPoint so the
      # shell is the actual entrypoint and the heredoc is its argument.
      entryPoint = ["/bin/sh", "-c"]
      command = var.fl_backend == "flower" ? [
        <<-SCRIPT
        set -e
        S3_BASE=${local.flower_creds_base_s3}
        echo "Syncing Flower creds from $S3_BASE to EFS..."

        # Same wipe-first rationale as the NVFLARE branch below: `aws s3 sync`
        # skips size+mtime-equivalent files, which can leave rotated creds
        # stale. certificates/ carries ca.crt + server.pem/server.key for the
        # SuperLink; keys/ the per-slot SuperNode public keys the
        # register-supernode-keys one-shot (ecs_flower.tf) feeds to it.
        mkdir -p /mnt/flower-certs /mnt/flower-keys /mnt/flower-jobs
        find /mnt/flower-certs -mindepth 1 -delete 2>/dev/null || true
        find /mnt/flower-keys -mindepth 1 -delete 2>/dev/null || true
        aws s3 sync "$S3_BASE/certificates/" /mnt/flower-certs/
        aws s3 sync "$S3_BASE/keys/" /mnt/flower-keys/

        echo "EFS provisioning complete."
        SCRIPT
        ] : [
        <<-SCRIPT
        set -e
        S3_BASE=${local.fl_provision_base_s3}
        echo "Syncing NVFLARE participant kit from $S3_BASE to EFS..."

        # fl-api-net-1: kit dir on S3 keeps the docker-prefix `flip-fl-api-net-1`.
        # NVFLARE participant kits are signed at provision time (signature.json
        # over each file). Editing any file post-sync trips
        # LoadResult.INVALID_SIGNATURE on fl-api startup, so the kit must be
        # regenerated upstream (see fl-services/nvflare/provision/net-1_project_stag.yml)
        # whenever a hostname/SAN changes and re-uploaded to S3 under a new
        # FLARE_KIT_DATE prefix.
        #
        # We wipe the access-point contents first because `aws s3 sync` skips
        # files when source/dest size + mtime "look equivalent", and a fresh
        # kit can have files with sizes coincidentally similar to the
        # previous one - leaving fed_admin.json or signature.json stale and
        # producing INVALID_SIGNATURE at fl-api boot. `find ... -delete` is
        # cheaper than recreating the access point and avoids a DB-level
        # lifecycle on the EFS file system.
        mkdir -p /mnt/fl-api/local /mnt/fl-api/startup
        find /mnt/fl-api/local -mindepth 1 -delete 2>/dev/null || true
        find /mnt/fl-api/startup -mindepth 1 -delete 2>/dev/null || true
        aws s3 sync "$S3_BASE/flip-fl-api-net-1/local/" /mnt/fl-api/local/
        aws s3 sync "$S3_BASE/flip-fl-api-net-1/startup/" /mnt/fl-api/startup/

        # fl-server-net-1: SSL key + cert live inside `local/` (NVFLARE puts
        # them under site-1/ssl-*), so no separate certs/keys mounts are
        # synced here. transfer/ is created empty for runtime use by the
        # NVFLARE server (training jobs write checkpoints there).
        mkdir -p /mnt/fl-server/local /mnt/fl-server/startup /mnt/fl-server/transfer
        find /mnt/fl-server/local -mindepth 1 -delete 2>/dev/null || true
        find /mnt/fl-server/startup -mindepth 1 -delete 2>/dev/null || true
        aws s3 sync "$S3_BASE/fl-server-net-1/local/" /mnt/fl-server/local/
        aws s3 sync "$S3_BASE/fl-server-net-1/startup/" /mnt/fl-server/startup/

        echo "EFS provisioning complete."
        SCRIPT
      ]

      mountPoints = var.fl_backend == "flower" ? [
        {
          sourceVolume  = "efs-flower-certs"
          containerPath = "/mnt/flower-certs"
        },
        {
          sourceVolume  = "efs-flower-keys"
          containerPath = "/mnt/flower-keys"
        },
        {
          sourceVolume  = "efs-flower-jobs"
          containerPath = "/mnt/flower-jobs"
        },
        ] : [
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
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs_fl_server_net_1.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "efs-provision"
        }
      }
    }
  ])

  # Mount the same EFS access points as the runtime services. certs/keys
  # access points still exist in efs.tf for state continuity, but the
  # provisioning task no longer touches them (kit puts SSL files under
  # local/site-1/ssl-*).
  dynamic "volume" {
    for_each = var.fl_backend == "flower" ? {
      "efs-flower-certs" = "fl_server_certs"
      "efs-flower-keys"  = "fl_server_keys"
      "efs-flower-jobs"  = "fl_jobs"
      } : {
      "efs-fl-api-local"       = "fl_api_local"
      "efs-fl-api-startup"     = "fl_api_startup"
      "efs-fl-server-local"    = "fl_server_local"
      "efs-fl-server-startup"  = "fl_server_startup"
      "efs-fl-server-transfer" = "fl_server_transfer"
    }
    content {
      name = volume.key
      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.flip_fl[0].id
        root_directory     = "/"
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = aws_efs_access_point.flip_fl[volume.value].id
          iam             = "ENABLED"
        }
      }
    }
  }
}
