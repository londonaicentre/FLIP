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
  fl_api_image    = "${var.docker_registry}${var.fl_api_name}:${var.flip_fl_image_tag}"
  fl_server_image = "${var.docker_registry}${var.fl_server_name}:${var.flip_fl_image_tag}"
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
          # Connect through RDS Proxy (IAM auth) rather than directly to the
          # RDS instance — see FLIP#556 and rds_proxy.tf.
          { name = "DB_HOST", value = aws_db_proxy.flip_db.endpoint },
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
  count                    = var.enable_efs ? 1 : 0
  family                   = "fl-api-net-1"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  # 4 GiB: the fl-api de-bundle stages large eval checkpoints (e.g. the ~759 MiB
  # Ark+ weights) via a buffered download, so 1 GiB OOM-killed the task (FLIP#695).
  cpu                = "1024"
  memory             = "4096"
  execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn      = aws_iam_role.ecs_fl_api_task.arn

  container_definitions = jsonencode([
    {
      name              = "fl-api-net-1"
      image             = local.fl_api_image
      cpu               = 1024
      memoryReservation = 4096

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

      # Container paths must match what the fl-api image expects (mirrors
      # compose.production.nvflare.yml: /app/admin/{local,startup}). NVFLARE
      # initialises a Workspace from FL_ADMIN_DIRECTORY/startup at boot - if
      # the dir is missing the lifespan startup raises and the task crashes.
      mountPoints = [
        {
          sourceVolume  = "efs-fl-api-net-1-local"
          containerPath = "/app/admin/local"
        },
        {
          sourceVolume  = "efs-fl-api-net-1-startup"
          containerPath = "/app/admin/startup"
        },
        # Writer side of the shared checkpoint-staging volume (SERVER_CHECKPOINT_ROOT).
        # fl-api de-bundles large eval checkpoints here; fl-server-net-1 mounts the
        # SAME EFS access point at the same path and reads them back (FLIP#695).
        {
          sourceVolume  = "efs-fl-api-net-1-checkpoints"
          containerPath = "/app/server-checkpoints"
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

      # Liveness probe (FLIP#593 pt.1): hit the session-independent /health/
      # endpoint. If the app process is dead or wedged the check fails and ECS
      # replaces the task — catching the wedged-but-alive case that removing
      # uvicorn --reload (entrypoint) does not. The image is python-slim with no
      # curl, but the venv python is on PATH. startPeriod covers the NVFLARE
      # workspace/admin-session init at boot.
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:${local.api_container_port}/health/', timeout=4).getcode()==200 else 1)\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 120
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

  # Shared checkpoint-staging volume — same access point the fl-server mounts, so
  # a checkpoint fl-api writes to /app/server-checkpoints/<model_id>/ is visible
  # to the fl-server's EvaluationModelLocator (FLIP#695).
  volume {
    name = "efs-fl-api-net-1-checkpoints"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_checkpoints"].id
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
  count                    = var.enable_efs ? 1 : 0
  family                   = "fl-server-net-1"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  # 8 GiB: the fl-server's EvaluationModelLocator torch-loads the staged eval
  # checkpoint(s) into memory (the ~759 MiB Ark+ weights; multimodel loads two),
  # which OOM'd the 2 GiB task (FLIP#695).
  cpu                = "2048"
  memory             = "8192"
  execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn      = aws_iam_role.ecs_fl_server_task.arn

  container_definitions = jsonencode([
    {
      name              = "fl-server-net-1"
      image             = local.fl_server_image
      cpu               = 2048
      memoryReservation = 8192

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

      # INTERNAL_SERVICE_KEY is sourced from the FLIP_API Secrets Manager
      # secret rather than passed as a plain env, so the raw key never lands
      # in the task definition JSON or CloudFormation describe output. The
      # JSON-key syntax (`:internal_service_key::`) extracts that single
      # field from the multi-field secret payload.
      secrets = [
        {
          name      = "INTERNAL_SERVICE_KEY"
          valueFrom = "${module.flip_api_secret.secret_arn}:internal_service_key::"
        },
      ]

      # Container paths must match what the fl-server image expects (mirrors
      # compose.production.nvflare.yml: /app/{local,startup,transfer}). The
      # entrypoint chmods scripts in /app/startup and reads
      # /app/local/log_config.template.json - wrong paths -> crash loop.
      # certs and keys live inside /app/local (NVFLARE puts them under
      # site-1/ssl-key, ssl-cert), so no separate mount is needed.
      mountPoints = [
        {
          sourceVolume  = "efs-fl-server-net-1-local"
          containerPath = "/app/local"
        },
        {
          sourceVolume  = "efs-fl-server-net-1-startup"
          containerPath = "/app/startup"
        },
        {
          sourceVolume  = "efs-fl-server-net-1-transfer"
          containerPath = "/app/transfer"
        },
        # Reader side of the shared checkpoint-staging volume (SERVER_CHECKPOINT_ROOT).
        # Same EFS access point as fl-api-net-1's writer mount — the fl-server's
        # EvaluationModelLocator loads the staged checkpoint from here (FLIP#695).
        {
          sourceVolume  = "efs-fl-server-net-1-checkpoints"
          containerPath = "/app/server-checkpoints"
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

  # Shared checkpoint-staging volume — the SAME access point fl-api-net-1 writes
  # to. transit-encrypted NFS; both tasks are pinned to uid/gid 1001 by the
  # access point so the reader sees the writer's files (FLIP#695).
  volume {
    name = "efs-fl-server-net-1-checkpoints"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.flip_fl[0].id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.flip_fl["fl_checkpoints"].id
        iam             = "ENABLED"
      }
    }
  }

  # NVFLARE keeps SSL key + cert under /app/local/site-1/ssl-* (i.e. inside
  # the local volume), so no separate certs/keys mounts are needed. The
  # corresponding fl_server_certs / fl_server_keys access points in efs.tf
  # are left in place to avoid a destroy on this hot path - drop them in a
  # follow-up cleanup PR once the cutover is stable.

  tags = {
    Name = "fl-server-net-1"
  }
}
