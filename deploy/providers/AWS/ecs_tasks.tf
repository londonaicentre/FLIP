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

# ECS task definitions for the three Central Hub services. Each definition
# mirrors the env+volume+port configuration from the corresponding
# compose.production.yml service, adapted for Fargate:
#
#   flip-api         — REST API (container port 8000)
#   fl-api-net-1     — FL network API (container port 8000)
#   fl-server-net-1  — FL server (container port 8002 + gRPC)
#
# Images are tagged with var.docker_image_tag for flip-api and
# var.flip_fl_image_tag for FL services. Env vars come from
# locals.ecs_task_env with secrets sourced from AWS Secrets Manager.
#
# EFS volumes mount the non-root access points from efs.tf so certs,
# keys, startup files and transfer directories survive task restarts.

locals {
  fl_api_image         = "${var.docker_registry}${var.fl_api_name}:${var.flip_fl_image_tag}"
  fl_server_image      = "${var.docker_registry}${var.fl_server_name}:${var.flip_fl_image_tag}"
}

############################
# flip-api
############################

resource "aws_ecs_task_definition" "flip_api" {
  family                   = "flip-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_flip_api_task.arn

  container_definitions = jsonencode([
    {
      name   = "flip-api"
      image  = "${var.docker_registry}flip-api:${var.docker_image_tag}"
      cpu    = 512
      memory = 1024

      portMappings = [
        {
          containerPort = local.api_container_port
          protocol      = "tcp"
        }
      ]

      environment = concat(
        [for k, v in local.ecs_task_env.flip_api : { name = k, value = v }],
        [
          { name = "DB_HOST", value = module.flip_db.db_instance_address },
          { name = "DB_PORT", value = "5432" },
        ],
      )

      secrets = []

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs_flip_api.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "flip-api"
        }
      }
    }
  ])

  tags = {
    Name = "flip-api"
  }
}

############################
# fl-api-net-1
############################

resource "aws_ecs_task_definition" "fl_api_net_1" {
  family                   = "fl-api-net-1"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_fl_api_task.arn

  container_definitions = jsonencode([
    {
      name  = "fl-api-net-1"
      image = local.fl_api_image
      cpu   = 512
      memoryReservation = 1024

      portMappings = [
        {
          containerPort = local.api_container_port
          protocol      = "tcp"
        }
      ]

      environment = [
        for k, v in local.ecs_task_env.fl_api :
        { name = k, value = v }
      ]

      mountPoints = [
        {
          sourceVolume  = "efs-fl-api-net-1-local"
          containerPath = "/app/data/fl-api-net-1/local"
        },
        {
          sourceVolume  = "efs-fl-api-net-1-startup"
          containerPath = "/app/data/fl-api-net-1/startup"
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs_fl_api_net_1.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "fl-api-net-1"
        }
      }
    }
  ])

  volume {
    name = "efs-fl-api-net-1-local"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_api_local"].id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "efs-fl-api-net-1-startup"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_api_startup"].id
        iam             = "ENABLED"
      }
    }
  }

  tags = {
    Name = "fl-api-net-1"
  }
}

############################
# fl-server-net-1
############################

resource "aws_ecs_task_definition" "fl_server_net_1" {
  family                   = "fl-server-net-1"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_fl_server_task.arn

  container_definitions = jsonencode([
    {
      name  = "fl-server-net-1"
      image = local.fl_server_image
      cpu   = 1024
      memoryReservation = 2048

      portMappings = [
        {
          containerPort = 8002
          protocol      = "tcp"
        }
      ]

      environment = [
        for k, v in local.ecs_task_env.fl_server :
        { name = k, value = v }
      ]

      mountPoints = [
        {
          sourceVolume  = "efs-fl-server-net-1-local"
          containerPath = "/app/data/fl-server-net-1/local"
        },
        {
          sourceVolume  = "efs-fl-server-net-1-startup"
          containerPath = "/app/data/fl-server-net-1/startup"
        },
        {
          sourceVolume  = "efs-fl-server-net-1-transfer"
          containerPath = "/app/data/fl-server-net-1/transfer"
        },
        {
          sourceVolume  = "efs-fl-server-net-1-certs"
          containerPath = "/app/data/fl-server-net-1/certs"
        },
        {
          sourceVolume  = "efs-fl-server-net-1-keys"
          containerPath = "/app/data/fl-server-net-1/keys"
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs_fl_server_net_1.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "fl-server-net-1"
        }
      }
    }
  ])

  volume {
    name = "efs-fl-server-net-1-local"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_server_local"].id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "efs-fl-server-net-1-startup"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_server_startup"].id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "efs-fl-server-net-1-transfer"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_server_transfer"].id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "efs-fl-server-net-1-certs"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_server_certs"].id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "efs-fl-server-net-1-keys"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_server_keys"].id
        iam             = "ENABLED"
      }
    }
  }

  tags = {
    Name = "fl-server-net-1"
  }
}
