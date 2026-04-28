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

    linuxParameters = {
      initProcessEnabled = true
    }

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
      { name = "FL_BACKEND", value = var.fl_backend },
      { name = "TRUST_API_KEY_HEADER", value = "Authorization" },
      { name = "INTERNAL_SERVICE_KEY_HEADER", value = "X-Internal-Service-Key" },
      { name = "NET_ENDPOINTS", value = var.net_endpoints },
      { name = "TRUST_NAMES", value = var.trust_names },
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
        valueFrom = aws_ssm_parameter.s3_model_files_uploaded.arn
      },
      {
        name      = "SCANNED_MODEL_FILES_BUCKET"
        valueFrom = aws_ssm_parameter.s3_model_files_uploaded.arn
      },
      {
        name      = "UPLOADED_FEDERATED_DATA_BUCKET"
        valueFrom = aws_ssm_parameter.s3_uploaded_federated_data.arn
      },
      {
        name      = "FL_APP_BASE_BUCKET"
        valueFrom = aws_ssm_parameter.s3_aicentre_bucket.arn
      },
      {
        name      = "FL_APP_DESTINATION_BUCKET"
        valueFrom = aws_ssm_parameter.s3_app_destination_bucket.arn
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
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=5)\" || exit 1"]
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

# Trust service task definitions are not deployed on ECS.
# trust-api, imaging-api, and data-access-api run via Docker Compose on
# EC2 (or on-premises) and are provisioned by Ansible — not Terraform.
