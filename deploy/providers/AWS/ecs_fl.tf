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
# EFS for FL persistent storage
#
# Provisioned throughput mode is used for consistent low-latency access
# across all FL networks. EFS access points enforce root-directory
# isolation per FL service (admin, data, startup).
############################

resource "aws_efs_file_system" "fl_data" {
  creation_token = "flip-fl-data-efs"

  performance_mode                = "generalPurpose"
  throughput_mode                 = "provisioned"
  provisioned_throughput_in_mibps = var.efs_provisioned_throughput

  tags = {
    Name = "flip-fl-data-efs"
  }
}

resource "aws_security_group" "efs_fl_data" {
  name        = "efs-fl-data-sg"
  vpc_id      = module.flip_vpc.vpc_id
  description = "EFS for FL data: NFSv4 ingress from FL ECS tasks"

  ingress {
    description     = "NFSv4 from fl-api tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_fl_api.id]
  }

  ingress {
    description     = "NFSv4 from fl-server tasks"
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_fl_server.id]
  }

  tags = {
    Name = "efs-fl-data-sg"
  }
}

resource "aws_efs_mount_target" "fl_data" {
  count = length(module.flip_vpc.private_subnets)

  file_system_id  = aws_efs_file_system.fl_data.id
  subnet_id       = module.flip_vpc.private_subnets[count.index]
  security_groups = [aws_security_group.efs_fl_data.id]
}

# FL admin directory access point (mounted at /app/admin by fl-api)
resource "aws_efs_access_point" "fl_admin" {
  file_system_id = aws_efs_file_system.fl_data.id

  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/admin"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "755"
    }
  }
}

# FL data directory access point (mounted at /app/data by fl-server)
resource "aws_efs_access_point" "fl_data_ap" {
  file_system_id = aws_efs_file_system.fl_data.id

  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/data"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "755"
    }
  }
}

# FL local config access point (mounted at /app/local by fl-server)
resource "aws_efs_access_point" "fl_local" {
  file_system_id = aws_efs_file_system.fl_data.id

  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/local"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "755"
    }
  }
}

# FL startup/provisioned kit access point (mounted at /app/startup by fl-server)
resource "aws_efs_access_point" "fl_startup" {
  file_system_id = aws_efs_file_system.fl_data.id

  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/startup"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "755"
    }
  }
}

############################
# Service Discovery
#
# Private DNS namespace flip.local resolves FL API and FL Server endpoints
# per network. flip-api resolves fl-api-<network>.flip.local:8000 to reach
# the FL API for each network.
############################

resource "aws_service_discovery_private_dns_namespace" "flip" {
  name        = "flip.local"
  description = "Service discovery namespace for FLIP services"
  vpc         = module.flip_vpc.vpc_id
}

resource "aws_service_discovery_service" "fl_api" {
  for_each = local.fl_networks

  name = "fl-api-${each.key}"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.flip.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

}

resource "aws_service_discovery_service" "fl_server" {
  for_each = local.fl_networks

  name = "fl-server-${each.key}"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.flip.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

}

############################
# FL Service Security Groups
#
# fl-api: ingress from flip-api on port 8000 (internal, not exposed via ALB)
# fl-server: ingress from NLB on port 8002 (FL client traffic)
############################

resource "aws_security_group" "ecs_fl_api" {
  name        = "ecs-fl-api-sg"
  vpc_id      = module.flip_vpc.vpc_id
  description = "fl-api ECS tasks: ingress from flip-api on port 8000"

  tags = {
    Name = "ecs-fl-api-sg"
  }

  ingress {
    description     = "fl-api from flip-api"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_flip_api.id]
  }

  egress {
    description = "NFS to EFS mount targets"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "HTTPS to AWS services and external endpoints via NAT"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "PostgreSQL to RDS"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_security_group" "ecs_fl_server" {
  name        = "ecs-fl-server-sg"
  vpc_id      = module.flip_vpc.vpc_id
  description = "fl-server ECS tasks: ingress from NLB on port 8002"

  tags = {
    Name = "ecs-fl-server-sg"
  }

  ingress {
    description     = "fl-server from NLB"
    from_port       = var.FL_SERVER_PORT
    to_port         = var.FL_SERVER_PORT
    protocol        = "tcp"
    security_groups = [module.fl_server_nlb.security_group_id]
  }

  egress {
    description = "NFS to EFS mount targets"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "HTTPS to AWS services and external endpoints via NAT"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "PostgreSQL to RDS"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

############################
# FL ECS Task Definitions
############################

locals {
  fl_image_tag = var.fl_image_tag != "" ? var.fl_image_tag : var.docker_image_tag
}

resource "aws_ecs_task_definition" "fl_api" {
  for_each = local.fl_networks

  family                   = "fl-api-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.ecs_fl_api_cpu
  memory                   = var.ecs_fl_api_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  volume {
    name = "fl_admin"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.fl_data.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.fl_admin.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "fl-api-${each.key}"
      image     = "ghcr.io/londonaicentre/flare-fl-api:${local.fl_image_tag}"
      essential = true

      linuxParameters = {
        initProcessEnabled = true
      }

      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]

      mountPoints = [
        {
          sourceVolume  = "fl_admin"
          containerPath = "/app/admin"
          readOnly      = false
        }
      ]

      environment = [
        { name = "FL_BACKEND", value = var.fl_backend },
        { name = "FL_ADMIN_DIRECTORY", value = "/app/admin" },
        { name = "INTERNAL_SERVICE_KEY_HEADER", value = "X-Internal-Service-Key" },
      ]

      secrets = [
        { name = "DB_HOST", valueFrom = aws_ssm_parameter.db_host.arn },
        { name = "DB_PORT", valueFrom = aws_ssm_parameter.db_port.arn },
        { name = "POSTGRES_USER", valueFrom = aws_ssm_parameter.db_user.arn },
        { name = "POSTGRES_DB", valueFrom = aws_ssm_parameter.db_name.arn },
        { name = "POSTGRES_PASSWORD", valueFrom = "${module.flip_api_secret.secret_arn}:db_password::" },
        { name = "PRIVATE_API_KEY_HEADER", valueFrom = aws_ssm_parameter.private_api_key_header.arn },
        { name = "PRIVATE_API_KEY", valueFrom = aws_ssm_parameter.private_api_key.arn },
        { name = "CENTRAL_HUB_API_URL", valueFrom = aws_ssm_parameter.central_hub_api_url.arn },
        { name = "AES_KEY_BASE64", valueFrom = "${module.flip_api_secret.secret_arn}:aes_key::" },
        { name = "TRUST_API_KEY_HASHES", valueFrom = "${module.flip_api_secret.secret_arn}:trust_api_key_hashes::" },
        { name = "INTERNAL_SERVICE_KEY", valueFrom = "${module.flip_api_secret.secret_arn}:internal_service_key::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs_fl_api[each.key].name
          "awslogs-region"        = var.AWS_REGION
          "awslogs-stream-prefix" = "fl-api"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5)\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  depends_on = [
    aws_efs_file_system.fl_data,
    aws_efs_access_point.fl_admin
  ]

  tags = {
    Service = "fl-api-${each.key}"
  }
}

resource "aws_ecs_task_definition" "fl_server" {
  for_each = local.fl_networks

  family                   = "fl-server-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.ecs_fl_server_cpu
  memory                   = var.ecs_fl_server_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  volume {
    name = "fl_data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.fl_data.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.fl_data_ap.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "fl_startup"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.fl_data.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.fl_startup.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "fl_local"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.fl_data.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.fl_local.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "fl-server-${each.key}"
      image     = "ghcr.io/londonaicentre/flare-fl-server:${local.fl_image_tag}"
      essential = true

      linuxParameters = {
        initProcessEnabled = true
      }

      portMappings = [
        {
          containerPort = var.FL_SERVER_PORT
          hostPort      = var.FL_SERVER_PORT
          protocol      = "tcp"
        }
      ]

      mountPoints = [
        {
          sourceVolume  = "fl_data"
          containerPath = "/app/data"
          readOnly      = false
        },
        {
          sourceVolume  = "fl_startup"
          containerPath = "/app/startup"
          readOnly      = false
        },
        {
          sourceVolume  = "fl_local"
          containerPath = "/app/local"
          readOnly      = false
        }
      ]

      environment = [
        { name = "FL_BACKEND", value = var.fl_backend },
        { name = "FL_SERVER_PORT", value = tostring(var.FL_SERVER_PORT) },
        { name = "FILE_STORE_PATH", value = "/app/data" },
        { name = "ADMIN_DIRECTORY", value = "/app/admin" },
        { name = "STARTUP_FILE_DIR", value = "/app/startup" },
        { name = "IMAGES_DIR", value = "/app/data/images" },
        { name = "INTERNAL_SERVICE_KEY_HEADER", value = "X-Internal-Service-Key" },
      ]

      secrets = [
        { name = "DB_HOST", valueFrom = aws_ssm_parameter.db_host.arn },
        { name = "DB_PORT", valueFrom = aws_ssm_parameter.db_port.arn },
        { name = "POSTGRES_USER", valueFrom = aws_ssm_parameter.db_user.arn },
        { name = "POSTGRES_DB", valueFrom = aws_ssm_parameter.db_name.arn },
        { name = "POSTGRES_PASSWORD", valueFrom = "${module.flip_api_secret.secret_arn}:db_password::" },
        { name = "PRIVATE_API_KEY_HEADER", valueFrom = aws_ssm_parameter.private_api_key_header.arn },
        { name = "PRIVATE_API_KEY", valueFrom = aws_ssm_parameter.private_api_key.arn },
        { name = "CENTRAL_HUB_API_URL", valueFrom = aws_ssm_parameter.central_hub_api_url.arn },
        { name = "AES_KEY_BASE64", valueFrom = "${module.flip_api_secret.secret_arn}:aes_key::" },
        { name = "TRUST_API_KEY_HASHES", valueFrom = "${module.flip_api_secret.secret_arn}:trust_api_key_hashes::" },
        { name = "INTERNAL_SERVICE_KEY", valueFrom = "${module.flip_api_secret.secret_arn}:internal_service_key::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs_fl_server[each.key].name
          "awslogs-region"        = var.AWS_REGION
          "awslogs-stream-prefix" = "fl-server"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8002/health', timeout=5)\" || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  depends_on = [
    aws_efs_file_system.fl_data,
    aws_efs_access_point.fl_data_ap,
    aws_efs_access_point.fl_startup,
    aws_efs_access_point.fl_local
  ]

  tags = {
    Service = "fl-server-${each.key}"
  }
}

############################
# FL ECS Services
#
# fl-api: registered in service discovery only (not exposed via ALB or NLB).
# fl-server: registered in service discovery + NLB IP target group for FL
# client connectivity.
############################

resource "aws_ecs_service" "fl_api" {
  for_each = local.fl_networks

  name            = "fl-api-${each.key}-service"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.fl_api[each.key].arn
  desired_count   = var.ecs_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_fl_api.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.fl_api[each.key].arn
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = {
    Service = "fl-api-${each.key}"
  }
}

resource "aws_ecs_service" "fl_server" {
  for_each = local.fl_networks

  name            = "fl-server-${each.key}-service"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.fl_server[each.key].arn
  desired_count   = var.ecs_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_fl_server.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.fl_server[each.key].arn
  }

  load_balancer {
    target_group_arn = module.fl_server_nlb.target_groups["ecs-fl-server-tcp"].arn
    container_name   = "fl-server-${each.key}"
    container_port   = var.FL_SERVER_PORT
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [
    aws_service_discovery_service.fl_server,
    module.fl_server_nlb
  ]

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = {
    Service = "fl-server-${each.key}"
  }
}
