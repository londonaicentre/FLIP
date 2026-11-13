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
# compose.prod.yml service, adapted for Fargate:
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
  # A release apply pins `v<X.Y.Z>@sha256:…` per repository (FLIP#1283), so the
  # two FL images carry different tags. fl_api_image_tag empty = "same as the
  # server", which is every non-digest case.
  fl_api_tag      = var.fl_api_image_tag != "" ? var.fl_api_image_tag : var.flip_fl_image_tag
  fl_api_image    = "${var.docker_registry}${var.fl_api_name}:${local.fl_api_tag}"
  fl_server_image = "${var.docker_registry}${var.fl_server_name}:${var.flip_fl_image_tag}"
  # Flower's fleet proxy (FLIP#1517), built and tagged with the SuperLink by
  # fl-docker-build-flower.yml. Its own tag when CI resolved one (a release pins its
  # own digest); otherwise the server's, minus any digest, which is that repository's.
  fl_fleet_proxy_tag   = var.fl_fleet_proxy_image_tag != "" ? var.fl_fleet_proxy_image_tag : split("@", var.flip_fl_image_tag)[0]
  fl_fleet_proxy_image = "${var.docker_registry}flower-fleet-proxy:${local.fl_fleet_proxy_tag}"

  # FL backend shape (#566): the same task FAMILIES (fl-api-net-1,
  # fl-server-net-1 — deploy-centralhub addresses services by family) serve
  # both backends; var.fl_backend switches image (via var.fl_*_name from
  # fl_backend.mk), container ports, command, env map and mounts. FLARE
  # mirrors compose.prod.nvflare.yml; Flower mirrors
  # compose.prod.flower.yml. Both differ from them in the read-only root
  # filesystem and what it takes (the scratch volumes below, FLIP#1795).
  # flwr>=1.40 serves every SuperLink API — Fleet, Control, Runtime — on control_port;
  # trusts reach only the fleet proxy sidecar on its own port (FLIP#1517).
  flower_fleet_proxy_port       = 9092
  flower_superlink_control_port = 8000

  # The container the NLB target group binds: the proxy on Flower, the server on NVFLARE.
  fl_server_lb_container = var.fl_backend == "flower" ? "fleet-proxy" : "fl-server-net-1"

  # Container port the NLB target group forwards to. The EXTERNAL listener
  # port stays var.FL_SERVER_PORT for both backends — compose maps
  # ${FL_SERVER_PORT}:9092 the same way for Flower.
  fl_server_container_port = var.fl_backend == "flower" ? local.flower_fleet_proxy_port : var.FL_SERVER_PORT

  # EFS volumes per backend and task, name → access-point key (efs.tf).
  # Flower shares one RW jobs volume (/app/src — uploaded bundles +
  # checkpoints, the ECS analogue of compose's ${FL_JOBS_DIR}/net-1 bind)
  # between fl-api and fl-server, and mounts the SuperLink certs read-only
  # on both (fl-api only reads ca.crt). The fl_server_certs / fl_server_keys
  # access points were FLARE leftovers kept for state continuity — Flower
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

  # Read-only root filesystems (Security Hub ECS.5, FLIP#1795). Every container sets
  # readonlyRootFilesystem and writes only to its EFS mounts and to empty task volumes
  # (Fargate ephemeral storage), here volume name → container path for the FL tasks.
  # Each path is a VOLUME in its image: without that Fargate mounts the volume
  # root-owned 0755 and a non-root container cannot write it. Each container's
  # `user` (Security Hub ECS.20, FLIP#1796) is therefore its image's own uid:gid.
  #
  # The services give the read-only root up while var.ecs_exec_enabled is on: ECS
  # Exec starts an SSM agent in every container of the task, and it has to write
  # there. A debugging apply therefore fails ECS.5 until the variable is off again.
  service_readonly_root = !var.ecs_exec_enabled
  fl_api_scratch_volumes = {
    nvflare = { "tmp" = "/tmp", "admin-transfer" = "/app/admin/transfer" }
    flower  = { "tmp" = "/tmp", "flwr-config" = "/home/app/.flwr" }
  }
  fl_server_scratch_volumes = {
    nvflare = { "tmp" = "/tmp", "workspace" = "/workspace", "home-cache" = "/home/flip/.cache" }
    flower  = { "tmp" = "/tmp" }
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
      # The image's own user, flip (flip-api/Dockerfile).
      user = "1000:1000"

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

      # The key flip-api presents on every FL API call; the fl-api task below reads the same
      # field, so the two cannot drift. Never plain env: the raw key would land in the task
      # definition JSON.
      secrets = [
        {
          name      = "FL_API_KEY"
          valueFrom = "${module.flip_api_secret.secret_arn}:fl_api_key::"
        },
      ]

      # Read-only root filesystem (ECS.5, FLIP#1795): /tmp is the one path the API
      # writes, and a VOLUME in its image (see local.fl_api_scratch_volumes for why).
      readonlyRootFilesystem = local.service_readonly_root
      mountPoints = [
        { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false },
      ]

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

  volume {
    name = "tmp"
  }

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
      # Both backends' images run as 1000:1000 (flip in nvflare/fl-api-base, app in flower/fl-api-flower).
      user = "1000:1000"

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

      # Every FL API route but /health requires this key (fl_api/utils/auth.py); flip-api's
      # task reads the same Secrets Manager field.
      secrets = [
        {
          name      = "FL_API_KEY"
          valueFrom = "${module.flip_api_secret.secret_arn}:fl_api_key::"
        },
      ]

      # Container paths must match what the fl-api image expects. FLARE
      # (compose.prod.nvflare.yml): /app/admin/{local,startup} — FLARE
      # initialises a Workspace from FL_ADMIN_DIRECTORY/startup at boot; if the
      # dir is missing the lifespan startup raises and the task crashes. The
      # checkpoints mount is the writer side of the shared staging volume
      # (SERVER_CHECKPOINT_ROOT; fl-server reads it back — FLIP#695).
      # Flower (compose.prod.flower.yml): SuperLink certs read-only
      # (only ca.crt is read) + the shared RW jobs volume at FLOWER_SRC_ROOT.
      readonlyRootFilesystem = local.service_readonly_root
      mountPoints = concat(
        [for name, path in local.fl_api_scratch_volumes[var.fl_backend] :
        { sourceVolume = name, containerPath = path, readOnly = false }],
        var.fl_backend == "flower" ? [
          {
            sourceVolume  = "efs-fl-net-1-certs"
            containerPath = "/certs"
            readOnly      = true
          },
          {
            sourceVolume  = "efs-fl-net-1-jobs"
            readOnly      = false
            containerPath = "/app/src"
          },
          ] : [
          {
            sourceVolume  = "efs-fl-api-net-1-local"
            readOnly      = false
            containerPath = "/app/admin/local"
          },
          {
            sourceVolume  = "efs-fl-api-net-1-startup"
            readOnly      = false
            containerPath = "/app/admin/startup"
          },
          {
            sourceVolume  = "efs-fl-api-net-1-checkpoints"
            readOnly      = false
            containerPath = "/app/server-checkpoints"
          },
        ],
      )

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
      # is on PATH. startPeriod covers the FLARE workspace/admin-session
      # init at boot. Endpoint per backend: FLARE fl-api exposes the
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

  dynamic "volume" {
    for_each = local.fl_api_scratch_volumes[var.fl_backend]
    content {
      name = volume.key
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

  container_definitions = jsonencode(concat([
    {
      name  = "fl-server-net-1"
      image = local.fl_server_image
      cpu   = 2048
      # The Flower fleet proxy below reserves the remaining 128 MiB of the task.
      memoryReservation = var.fl_backend == "flower" ? 8064 : 8192
      # The image's own user: flip in FLARE's (nvflare/fl-base), flwr/base's app in the SuperLink.
      user = var.fl_backend == "flower" ? "49999:49999" : "1000:1000"

      # Flower-only settings, null (dropped by the ECS API) for FLARE. The
      # SuperLink image's entrypoint takes the TLS + SuperNode-auth flags as
      # its command (mirroring the compose overlay); initProcessEnabled
      # mirrors compose's `init: true` (the SuperLink forks per-run
      # processes that need reaping).
      command = var.fl_backend == "flower" ? [
        "--ssl-ca-certfile", "/certs/ca.crt",
        "--ssl-certfile", "/certs/server.pem",
        "--ssl-keyfile", "/certs/server.key",
        "--enable-supernode-auth",
        # flwr>=1.37: the Control API is HTTP on port 8000, loopback-only
        # unless --host says otherwise; fl-api reaches it over Cloud Map.
        "--host", "0.0.0.0",
      ] : null
      linuxParameters = var.fl_backend == "flower" ? { initProcessEnabled = true } : null

      # FLARE serves client gRPC + admin multiplexed on one port. Flower's
      # SuperLink serves its HTTP APIs on 8000 (fl-api submits runs to the
      # Control API there and reads /health); the NLB's 9092 is the fleet
      # proxy's, below.
      portMappings = var.fl_backend == "flower" ? [
        { containerPort = local.flower_superlink_control_port, protocol = "tcp" },
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
      # FLARE: the kit's {local,startup,transfer} under /workspace (FL_WORKSPACE,
      # see the entrypoint), not compose.prod.nvflare.yml's /app; wrong paths ->
      # crash loop. The scratch volumes come first: the kit mounts nest inside
      # /workspace.
      # certs and keys live inside local/ (FLARE puts them under
      # site-1/ssl-key, ssl-cert), so no separate mount is needed. The
      # checkpoints mount is the reader side of the shared staging volume
      # (SERVER_CHECKPOINT_ROOT — FLIP#695).
      # Flower (compose.prod.flower.yml): SuperLink TLS material
      # read-only at /certs (referenced by the command flags below) + the
      # shared RW jobs volume at /app/src (uploaded bundles + eval
      # checkpoints, read via the flip-job-dir run-config).
      readonlyRootFilesystem = local.service_readonly_root
      mountPoints = concat(
        [for name, path in local.fl_server_scratch_volumes[var.fl_backend] :
        { sourceVolume = name, containerPath = path, readOnly = false }],
        var.fl_backend == "flower" ? [
          {
            sourceVolume  = "efs-fl-net-1-certs"
            containerPath = "/certs"
            readOnly      = true
          },
          {
            sourceVolume  = "efs-fl-net-1-jobs"
            readOnly      = false
            containerPath = "/app/src"
          },
          ] : [
          {
            sourceVolume  = "efs-fl-server-net-1-local"
            readOnly      = false
            containerPath = "/workspace/local"
          },
          {
            sourceVolume  = "efs-fl-server-net-1-startup"
            readOnly      = false
            containerPath = "/workspace/startup"
          },
          {
            sourceVolume  = "efs-fl-server-net-1-transfer"
            readOnly      = false
            containerPath = "/workspace/transfer"
          },
          {
            sourceVolume  = "efs-fl-server-net-1-checkpoints"
            readOnly      = false
            containerPath = "/app/server-checkpoints"
          },
        ],
      )

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs_fl_server_net_1.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "fl-server-net-1"
        }
      }
    }
    ],
    # Flower only (FLIP#1517): the SuperLink's one trust-facing listener. It shares the
    # task's network namespace (awsvpc), takes the NLB's 9092 and forwards only the
    # Fleet API routes to the SuperLink on localhost:8000, so a trust can never reach
    # the Control or Runtime API there. The image's defaults are this shape; see
    # fl-services/flower/fleet-proxy. NVFLARE has no sidecar.
    var.fl_backend == "flower" ? [
      {
        name              = "fleet-proxy"
        image             = local.fl_fleet_proxy_image
        essential         = true
        memoryReservation = 128
        # The image's own USER, the SuperLink's uid:gid.
        user = "49999:49999"

        portMappings = [
          { containerPort = local.flower_fleet_proxy_port, protocol = "tcp" },
        ]

        # The image's own check: a Fleet request must get through to the SuperLink. A
        # proxy that cannot reach or verify it answers every SuperNode 503 while the
        # NLB's TCP check still passes. Generous retries, as a failing check replaces
        # the whole task, SuperLink and any run in flight included.
        healthCheck = {
          command     = ["CMD", "/usr/local/bin/fleet-proxy-healthcheck"]
          interval    = 30
          timeout     = 10
          retries     = 5
          startPeriod = 120
        }

        # The SuperLink's own server certificate, so trust kits and SANs are unchanged.
        # /tmp holds everything nginx writes (its rendered config, pid and temp files).
        readonlyRootFilesystem = local.service_readonly_root
        mountPoints = [
          { sourceVolume = "fleet-proxy-tmp", containerPath = "/tmp", readOnly = false },
          {
            sourceVolume  = "efs-fl-net-1-certs"
            containerPath = "/certs"
            readOnly      = true
          },
        ]

        logConfiguration = {
          logDriver = "awslogs"
          options = {
            awslogs-group         = aws_cloudwatch_log_group.ecs_fl_server_net_1.name
            awslogs-region        = var.AWS_REGION
            awslogs-stream-prefix = "fleet-proxy"
          }
        }
      },
  ] : []))

  # Volume set per backend (local.fl_server_task_volumes). Shared-volume
  # notes: the FLARE checkpoints volume is the SAME access point
  # fl-api-net-1 writes to (both tasks pinned to uid/gid 1001 by the access
  # point, so reader sees writer's files — FLIP#695); the Flower jobs volume
  # is likewise shared RW with fl-api-net-1. FLARE keeps SSL key + cert
  # under /workspace/local/site-1/ssl-* (inside the local volume) so it needs no
  # certs mount; Flower mounts the fl_server_certs access point that was
  # previously a FLARE leftover.
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

  dynamic "volume" {
    for_each = merge(
      local.fl_server_scratch_volumes[var.fl_backend],
      var.fl_backend == "flower" ? { "fleet-proxy-tmp" = "/tmp" } : {},
    )
    content {
      name = volume.key
    }
  }

  tags = {
    Name = "fl-server-net-1"
  }
}
