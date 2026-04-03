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
# flip-api Task Definition
############################

resource "aws_ecs_task_definition" "flip_api" {
  family                   = "flip-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_flip_api_cpu
  memory                   = var.ecs_flip_api_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([{
    name  = "flip-api"
    image = "ghcr.io/londonaicentre/flip-api:${var.docker_image_tag}"

    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "ENV", value = "production" },
      { name = "AWS_REGION", value = var.AWS_REGION },
      { name = "POSTGRES_USER", value = var.POSTGRES_USER },
      { name = "DB_PORT", value = tostring(var.DB_PORT) },
      { name = "POSTGRES_DB", value = var.POSTGRES_DB },
      # flip-api reads the POSTGRES_SECRET_ARN at startup to fetch the DB password from Secrets Manager
      { name = "POSTGRES_SECRET_ARN", value = module.flip_db.db_instance_master_user_secret_arn },
      # flip-api reads the AWS_SECRET_NAME at startup to fetch AES key, trust endpoints, and CA cert
      { name = "AWS_SECRET_NAME", value = "FLIP_API" },
      { name = "TRUST_CA_BUNDLE", value = "/etc/ssl/trust/trust-ca.crt" },
      { name = "FL_BACKEND", value = var.fl_backend }
    ]

    # Parameter Store references – resolved by ECS agent before the container starts
    secrets = [
      {
        name      = "DB_HOST"
        valueFrom = aws_ssm_parameter.db_host.arn
      },
      {
        name      = "AWS_COGNITO_USER_POOL_ID"
        valueFrom = aws_ssm_parameter.cognito_user_pool_id.arn
      },
      {
        name      = "AWS_COGNITO_APP_CLIENT_ID"
        valueFrom = aws_ssm_parameter.cognito_app_client_id.arn
      },
      {
        name      = "AWS_SES_ADMIN_EMAIL_ADDRESS"
        valueFrom = aws_ssm_parameter.ses_admin_email.arn
      },
      {
        name      = "AWS_SES_SENDER_EMAIL_ADDRESS"
        valueFrom = aws_ssm_parameter.ses_sender_email.arn
      },
      {
        name      = "UPLOADED_MODEL_FILES_BUCKET"
        valueFrom = aws_ssm_parameter.s3_flip_bucket.arn
      },
      {
        name      = "SCANNED_MODEL_FILES_BUCKET"
        valueFrom = aws_ssm_parameter.s3_flip_bucket.arn
      },
      {
        name      = "UPLOADED_FEDERATED_DATA_BUCKET"
        valueFrom = aws_ssm_parameter.s3_flip_bucket.arn
      },
      {
        name      = "FL_APP_BASE_BUCKET"
        valueFrom = aws_ssm_parameter.s3_aicentre_bucket.arn
      },
      {
        name      = "FL_APP_DESTINATION_BUCKET"
        valueFrom = aws_ssm_parameter.s3_flip_bucket.arn
      },
      {
        name      = "PRIVATE_API_KEY_HEADER"
        valueFrom = aws_ssm_parameter.private_api_key_header.arn
      },
      {
        name      = "PRIVATE_API_KEY"
        valueFrom = aws_ssm_parameter.private_api_key.arn
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs_flip_api.name
        "awslogs-region"        = var.AWS_REGION
        "awslogs-stream-prefix" = "flip-api"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/api/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    essential = true
  }])

  tags = {
    Service = "flip-api"
  }
}

############################
# trust-api Task Definition
############################

resource "aws_ecs_task_definition" "trust_api" {
  family                   = "trust-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_trust_api_cpu
  memory                   = var.ecs_trust_api_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([{
    name  = "trust-api"
    image = "ghcr.io/londonaicentre/trust-api:${var.docker_image_tag}"

    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "ENV", value = "production" },
      { name = "FL_BACKEND", value = var.fl_backend }
    ]

    secrets = [
      {
        name      = "CENTRAL_HUB_API_URL"
        valueFrom = aws_ssm_parameter.central_hub_api_url.arn
      },
      {
        name      = "DATA_ACCESS_API_URL"
        valueFrom = aws_ssm_parameter.data_access_api_url.arn
      },
      {
        name      = "IMAGING_API_URL"
        valueFrom = aws_ssm_parameter.imaging_api_url.arn
      },
      {
        name      = "PRIVATE_API_KEY_HEADER"
        valueFrom = aws_ssm_parameter.private_api_key_header.arn
      },
      {
        name      = "PRIVATE_API_KEY"
        valueFrom = aws_ssm_parameter.private_api_key.arn
      },
      # trust-api reads AES_KEY_BASE64 directly from FLIP_API secret at runtime
      {
        name      = "AES_KEY_BASE64"
        valueFrom = "${module.flip_api_secret.secret_arn}:aes_key::"
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs_trust_api.name
        "awslogs-region"        = var.AWS_REGION
        "awslogs-stream-prefix" = "trust-api"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    essential = true
  }])

  tags = {
    Service = "trust-api"
  }
}

############################
# imaging-api Task Definition
############################

resource "aws_ecs_task_definition" "imaging_api" {
  family                   = "imaging-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_imaging_api_cpu
  memory                   = var.ecs_imaging_api_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([{
    name  = "imaging-api"
    image = "ghcr.io/londonaicentre/imaging-api:${var.docker_image_tag}"

    # Override the default CMD to use uvicorn directly (production-safe entrypoint)
    command = ["uv", "run", "python", "-m", "uvicorn", "imaging_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "ENV", value = "production" },
      { name = "BASE_IMAGES_DOWNLOAD_DIR", value = "/app/data/images" }
    ]

    secrets = [
      {
        name      = "XNAT_URL"
        valueFrom = aws_ssm_parameter.xnat_url.arn
      },
      {
        name      = "XNAT_PORT"
        valueFrom = aws_ssm_parameter.xnat_port.arn
      },
      {
        name      = "XNAT_SERVICE_USER"
        valueFrom = aws_ssm_parameter.xnat_service_user.arn
      },
      {
        name      = "XNAT_SERVICE_PASSWORD"
        valueFrom = aws_ssm_parameter.xnat_service_password.arn
      },
      {
        name      = "PACS_ID"
        valueFrom = aws_ssm_parameter.pacs_id.arn
      },
      {
        name      = "XNAT_DATABASE_URL"
        valueFrom = aws_ssm_parameter.xnat_database_url.arn
      },
      {
        name      = "DATA_ACCESS_API_URL"
        valueFrom = aws_ssm_parameter.data_access_api_url.arn
      },
      {
        name      = "AES_KEY_BASE64"
        valueFrom = "${module.flip_api_secret.secret_arn}:aes_key::"
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs_imaging_api.name
        "awslogs-region"        = var.AWS_REGION
        "awslogs-stream-prefix" = "imaging-api"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    essential = true
  }])

  tags = {
    Service = "imaging-api"
  }
}

############################
# data-access-api Task Definition
############################

resource "aws_ecs_task_definition" "data_access_api" {
  family                   = "data-access-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.ecs_data_access_api_cpu
  memory                   = var.ecs_data_access_api_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([{
    name  = "data-access-api"
    image = "ghcr.io/londonaicentre/data-access-api:${var.docker_image_tag}"

    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "ENV", value = "production" }
    ]

    secrets = [
      {
        name      = "OMOP_DB_SERVICE_NAME"
        valueFrom = aws_ssm_parameter.omop_db_service_name.arn
      },
      {
        name      = "DATA_ACCESS_POSTGRES_USER"
        valueFrom = aws_ssm_parameter.data_access_postgres_user.arn
      },
      {
        name      = "DATA_ACCESS_POSTGRES_PASSWORD"
        valueFrom = aws_ssm_parameter.data_access_postgres_password.arn
      },
      {
        name      = "OMOP_POSTGRES_DB"
        valueFrom = aws_ssm_parameter.omop_postgres_db.arn
      },
      {
        name      = "AES_KEY_BASE64"
        valueFrom = "${module.flip_api_secret.secret_arn}:aes_key::"
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs_data_access_api.name
        "awslogs-region"        = var.AWS_REGION
        "awslogs-stream-prefix" = "data-access-api"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    essential = true
  }])

  tags = {
    Service = "data-access-api"
  }
}
