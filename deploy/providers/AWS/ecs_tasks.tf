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

  # FL backend shape (#566): the same task FAMILIES (fl-api-net-1,
  # fl-server-net-1 — deploy-centralhub addresses services by family) serve
  # both backends; var.fl_backend switches image (via var.fl_*_name from
  # fl_backend.mk), container ports, command, env map and mounts. NVFLARE
  # mirrors compose.production.nvflare.yml; Flower mirrors
  # compose.production.flower.yml.
  flower_superlink_fleet_port  = 9092
  flower_superlink_exec_port   = 9093
  flower_superlink_health_port = 9097

  # Container port the NLB target group forwards to. The EXTERNAL listener
  # port stays var.FL_SERVER_PORT for both backends — compose maps
  # ${FL_SERVER_PORT}:9092 the same way for Flower.
  fl_server_container_port = var.fl_backend == "flower" ? local.flower_superlink_fleet_port : var.FL_SERVER_PORT

  # EFS volumes per backend and task, name → access-point key (efs.tf).
  # Flower shares one RW jobs volume (/app/src — uploaded bundles +
  # checkpoints, the ECS analogue of compose's ${FL_JOBS_DIR}/net-1 bind)
  # between fl-api and fl-server, and mounts the SuperLink certs read-only
  # on both (fl-api only reads ca.crt). The fl_server_certs / fl_server_keys
  # access points were NVFLARE leftovers kept for state continuity — Flower
  # now genuinely uses them.
  fl_api_task_volumes = {
    nvflare = {
      "efs-fl-api-net-1-local"       = "fl_api_local"
      "efs-fl-api-net-1-startup"     = "fl_api_startup"
      "efs-fl-api-net-1-checkpoints" = "fl_checkpoints"
    }
    flower = {
      "efs-fl-net-1-certs" = "fl_server_certs"
      "efs-fl-net-1-jobs"  = "fl_jobs"
    }
  }
  fl_server_task_volumes = {
    nvflare = {
      "efs-fl-server-net-1-local"       = "fl_server_local"
      "efs-fl-server-net-1-startup"     = "fl_server_startup"
      "efs-fl-server-net-1-transfer"    = "fl_server_transfer"
      "efs-fl-server-net-1-checkpoints" = "fl_checkpoints"
    }
    flower = {
      "efs-fl-net-1-certs" = "fl_server_certs"
      "efs-fl-net-1-jobs"  = "fl_jobs"
    }
  }
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
        for k, v in(var.fl_backend == "flower" ? local.ecs_task_env.fl_api_flower : local.ecs_task_env.fl_api) :
        { name = k, value = v }
      ]

      # Container paths must match what the fl-api image expects. NVFLARE
      # (compose.production.nvflare.yml): /app/admin/{local,startup} — NVFLARE
      # initialises a Workspace from FL_ADMIN_DIRECTORY/startup at boot; if the
      # dir is missing the lifespan startup raises and the task crashes. The
      # checkpoints mount is the writer side of the shared staging volume
      # (SERVER_CHECKPOINT_ROOT; fl-server reads it back — FLIP#695).
      # Flower (compose.production.flower.yml): SuperLink certs read-only
      # (only ca.crt is read) + the shared RW jobs volume at FLOWER_SRC_ROOT.
      mountPoints = var.fl_backend == "flower" ? [
        {
          sourceVolume  = "efs-fl-net-1-certs"
          containerPath = "/certs"
          readOnly      = true
        },
        {
          sourceVolume  = "efs-fl-net-1-jobs"
          containerPath = "/app/src"
        },
        ] : [
        {
          sourceVolume  = "efs-fl-api-net-1-local"
          containerPath = "/app/admin/local"
        },
        {
          sourceVolume  = "efs-fl-api-net-1-startup"
          containerPath = "/app/admin/startup"
        },
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

      # Liveness probe (FLIP#593 pt.1): if the app process is dead or wedged
      # the check fails and ECS replaces the task — catching the
      # wedged-but-alive case that removing uvicorn --reload (entrypoint)
      # does not. The image is python-slim with no curl, but the venv python
      # is on PATH. startPeriod covers the NVFLARE workspace/admin-session
      # init at boot. Endpoint per backend: NVFLARE fl-api exposes the
      # session-independent /health/; the Flower fl-api image is probed on
      # /openapi.json, mirroring its compose healthcheck.
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:${local.api_container_port}${var.fl_backend == "flower" ? "/openapi.json" : "/health/"}', timeout=4).getcode()==200 else 1)\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 120
      }
    }
  ])

  dynamic "volume" {
    for_each = local.fl_api_task_volumes[var.fl_backend]
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

      # Flower-only settings, null (dropped by the ECS API) for NVFLARE. The
      # SuperLink image's entrypoint takes the TLS + SuperNode-auth flags as
      # its command (mirroring the compose overlay); initProcessEnabled
      # mirrors compose's `init: true` (the SuperLink forks per-run
      # processes that need reaping).
      command = var.fl_backend == "flower" ? [
        "--ssl-ca-certfile", "/certs/ca.crt",
        "--ssl-certfile", "/certs/server.pem",
        "--ssl-keyfile", "/certs/server.key",
        "--enable-supernode-auth",
        "--health-server-address", "0.0.0.0:${local.flower_superlink_health_port}",
      ] : null
      linuxParameters = var.fl_backend == "flower" ? { initProcessEnabled = true } : null

      # NVFLARE serves client gRPC + admin multiplexed on one port. Flower's
      # SuperLink splits Fleet (9092, what the NLB forwards to), Exec (9093,
      # fl-api submits runs here) and health (9097).
      portMappings = var.fl_backend == "flower" ? [
        { containerPort = local.flower_superlink_fleet_port, protocol = "tcp" },
        { containerPort = local.flower_superlink_exec_port, protocol = "tcp" },
        { containerPort = local.flower_superlink_health_port, protocol = "tcp" },
        ] : [
        { containerPort = var.FL_SERVER_PORT, protocol = "tcp" },
      ]

      environment = [
        for k, v in(var.fl_backend == "flower" ? local.ecs_task_env.fl_server_flower : local.ecs_task_env.fl_server) :
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

      # Container paths must match what the fl-server image expects.
      # NVFLARE (compose.production.nvflare.yml): /app/{local,startup,
      # transfer} — the entrypoint chmods scripts in /app/startup and reads
      # /app/local/log_config.template.json; wrong paths -> crash loop.
      # certs and keys live inside /app/local (NVFLARE puts them under
      # site-1/ssl-key, ssl-cert), so no separate mount is needed. The
      # checkpoints mount is the reader side of the shared staging volume
      # (SERVER_CHECKPOINT_ROOT — FLIP#695).
      # Flower (compose.production.flower.yml): SuperLink TLS material
      # read-only at /certs (referenced by the command flags below) + the
      # shared RW jobs volume at /app/src (uploaded bundles + eval
      # checkpoints, read via the flip-job-dir run-config).
      mountPoints = var.fl_backend == "flower" ? [
        {
          sourceVolume  = "efs-fl-net-1-certs"
          containerPath = "/certs"
          readOnly      = true
        },
        {
          sourceVolume  = "efs-fl-net-1-jobs"
          containerPath = "/app/src"
        },
        ] : [
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

  # Volume set per backend (local.fl_server_task_volumes). Shared-volume
  # notes: the NVFLARE checkpoints volume is the SAME access point
  # fl-api-net-1 writes to (both tasks pinned to uid/gid 1001 by the access
  # point, so reader sees writer's files — FLIP#695); the Flower jobs volume
  # is likewise shared RW with fl-api-net-1. NVFLARE keeps SSL key + cert
  # under /app/local/site-1/ssl-* (inside the local volume) so it needs no
  # certs mount; Flower mounts the fl_server_certs access point that was
  # previously an NVFLARE leftover.
  dynamic "volume" {
    for_each = local.fl_server_task_volumes[var.fl_backend]
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
    Name = "fl-server-net-1"
  }
}
