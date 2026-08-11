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

# Flower-only (fl_backend=flower): one-shot task registering the SuperNode
# public keys with the SuperLink after the FL services come up — the ECS
# analogue of compose.production.flower.yml's register-supernode-keys-net-1
# service. The script (/scripts/register-supernode-keys.sh, baked into the
# SuperLink image) reads the per-slot keys from EFS, registers each with the
# SuperLink over the Exec API, and maps the resulting node_ids on the fl-api.
# TRUST_NAMES carries FL_KIT_SLOT_NAMES: the identity being labelled is the
# per-slot SuperNode key, so trusts admin-created later (bound to a slot via
# FLKitSlot.assigned_to_trust_id) still get their node_id mapped.

resource "aws_ecs_task_definition" "flower_register_supernode_keys" {
  count                    = var.enable_efs && var.fl_backend == "flower" ? 1 : 0
  family                   = "flower-register-supernode-keys"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_fl_server_task.arn

  container_definitions = jsonencode([
    {
      name       = "register-supernode-keys"
      image      = local.fl_server_image
      entryPoint = ["/bin/bash", "/scripts/register-supernode-keys.sh"]

      environment = [
        { name = "SUPERLINK_ADDRESS", value = "${local.service_discovery_names.fl_server}:${local.flower_superlink_exec_port}" },
        { name = "FL_API_ADDRESS", value = "http://${local.service_discovery_names.fl_api}:${local.api_container_port}" },
        { name = "TRUST_NAMES", value = var.FL_KIT_SLOT_NAMES },
      ]

      mountPoints = [
        {
          sourceVolume  = "efs-fl-net-1-certs"
          containerPath = "/certs"
          readOnly      = true
        },
        {
          sourceVolume  = "efs-fl-net-1-keys"
          containerPath = "/keys"
          readOnly      = true
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs_fl_server_net_1.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "register-supernode-keys"
        }
      }
    }
  ])

  dynamic "volume" {
    for_each = {
      "efs-fl-net-1-certs" = "fl_server_certs"
      "efs-fl-net-1-keys"  = "fl_server_keys"
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

  tags = {
    Name = "flower-register-supernode-keys"
  }
}

# Runs the one-shot after both FL services exist, waiting for them to be
# stable first (compose expresses this as depends_on fl-server started +
# fl-api healthy). Runs with the fl-api SG so the Flower control-plane SG
# rules in ecs_sg.tf cover its SuperLink Exec (9093) call, and the fl-api's
# VPC HTTP rule covers FL_API_ADDRESS. Re-run on key rotation by bumping
# FLOWER_KIT_DATE (retriggers via the provisioning task's arn) or tainting.
resource "null_resource" "flower_register_supernode_keys" {
  count = var.enable_service_discovery && var.enable_efs && var.fl_backend == "flower" ? 1 : 0

  triggers = {
    task_def   = aws_ecs_task_definition.flower_register_supernode_keys[0].arn
    slot_names = var.FL_KIT_SLOT_NAMES
    creds      = local.flower_creds_base_s3
  }

  provisioner "local-exec" {
    command     = <<-EOT
      aws ecs wait services-stable \
        --cluster ${aws_ecs_cluster.flip.name} \
        --services fl-server-net-1 fl-api-net-1 \
        --region ${var.AWS_REGION}

      TASK_ARN=$(aws ecs run-task \
        --cluster ${aws_ecs_cluster.flip.name} \
        --task-definition ${aws_ecs_task_definition.flower_register_supernode_keys[0].arn} \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[${join(",", module.flip_vpc.private_subnets)}],securityGroups=[${aws_security_group.ecs_fl_api.id}],assignPublicIp=DISABLED}" \
        --count 1 \
        --region ${var.AWS_REGION} \
        --no-cli-pager \
        --query 'tasks[0].taskArn' \
        --output text)

      echo "register-supernode-keys task ARN: $TASK_ARN"

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
        echo "register-supernode-keys task failed with exit code $EXIT_CODE"
        exit 1
      fi

      echo "SuperNode keys registered."
    EOT
    interpreter = ["bash", "-c"]
  }

  depends_on = [
    aws_ecs_service.fl_server_net_1,
    aws_ecs_service.fl_api_net_1,
  ]
}
