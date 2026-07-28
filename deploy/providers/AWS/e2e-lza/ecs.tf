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

# One Fargate cluster, two minimal services from the same echo image:
#
#   e2e-web — http-echo on :5678 behind the internal ALB (alb.tf), replying
#             "e2e-web ok". Stands in for flip-api behind the web tier.
#   e2e-fl  — http-echo on :8002 behind the internal NLB (nlb.tf), replying
#             "e2e-fl ok". Stands in for the FL gRPC server; only TCP
#             reachability is under test.

resource "aws_ecs_cluster" "e2e" {
  name = "${local.name_prefix}-cluster"
}

############################
# Execution role
############################
# ECR pull + CloudWatch logs only (the AWS-managed policy covers exactly
# that). No task role: the echo containers make no AWS API calls.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${local.name_prefix}-ecs-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

############################
# CloudWatch log groups
############################

resource "aws_cloudwatch_log_group" "e2e_web" {
  name              = "/ecs/e2e-web"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "e2e_fl" {
  name              = "/ecs/e2e-fl"
  retention_in_days = 7
}

############################
# Service security groups
############################

module "web_service_security_group" {
  source      = "../modules/secgroup"
  name        = "${local.name_prefix}-web-svc-sg"
  vpc_id      = module.vpc.vpc_id
  description = "e2e-web Fargate tasks - echo port from the internal ALB only"
  ingress_rules = [
    {
      port                     = local.web_container_port
      description              = "http-echo from the internal web ALB"
      source_security_group_id = module.web_alb_security_group.security_group.id
    }
  ]
}

resource "aws_ec2_tag" "web_service_security_group_flip_sg" {
  resource_id = module.web_service_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

# NLB client IP preservation is disabled by default for TCP listeners with
# target_type=ip, so forwarded traffic and health checks both arrive from
# the NLB nodes' VPC-private addresses — one VPC-CIDR rule covers both.
module "fl_service_security_group" {
  source      = "../modules/secgroup"
  name        = "${local.name_prefix}-fl-svc-sg"
  vpc_id      = module.vpc.vpc_id
  description = "e2e-fl Fargate tasks - FL port from the internal NLB nodes"
  ingress_rules = [
    {
      port        = local.fl_port
      description = "FL TCP from the internal NLB nodes (forwarded traffic + health checks)"
      cidr_blocks = [var.vpc_cidr]
    }
  ]
}

resource "aws_ec2_tag" "fl_service_security_group_flip_sg" {
  resource_id = module.fl_service_security_group.security_group.id
  key         = "FlipSG"
  value       = "true"
}

############################
# Task definitions
############################
# Minimal Fargate size (0.25 vCPU / 512 MB), same image, different port and
# reply text. The image is the operator-pushed copy of hashicorp/http-echo
# (ecr.tf, push commands in README.md); `command` overrides its CMD, the
# entrypoint stays /http-echo.

resource "aws_ecs_task_definition" "e2e_web" {
  family                   = "e2e-web"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name    = "e2e-web"
      image   = "${aws_ecr_repository.echo.repository_url}:latest"
      command = ["-listen=:${local.web_container_port}", "-text=e2e-web ok"]

      portMappings = [
        {
          containerPort = local.web_container_port
          protocol      = "tcp"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.e2e_web.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "e2e-web"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "e2e_fl" {
  family                   = "e2e-fl"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
    {
      name    = "e2e-fl"
      image   = "${aws_ecr_repository.echo.repository_url}:latest"
      command = ["-listen=:${local.fl_port}", "-text=e2e-fl ok"]

      portMappings = [
        {
          containerPort = local.fl_port
          protocol      = "tcp"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.e2e_fl.name
          awslogs-region        = var.AWS_REGION
          awslogs-stream-prefix = "e2e-fl"
        }
      }
    }
  ])
}

############################
# Services
############################

resource "aws_ecs_service" "e2e_web" {
  name            = "e2e-web"
  cluster         = aws_ecs_cluster.e2e.id
  task_definition = aws_ecs_task_definition.e2e_web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [module.web_service_security_group.security_group.id]
    assign_public_ip = false
  }

  # Register each running task's ENI IP with the ALB target group — without
  # this block the service stays invisible to the ALB.
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "e2e-web"
    container_port   = local.web_container_port
  }

  health_check_grace_period_seconds = 60

  # ECS rejects a service whose TG is not yet attached to a listener.
  depends_on = [module.web_alb]
}

resource "aws_ecs_service" "e2e_fl" {
  name            = "e2e-fl"
  cluster         = aws_ecs_cluster.e2e.id
  task_definition = aws_ecs_task_definition.e2e_fl.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [module.fl_service_security_group.security_group.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.fl.arn
    container_name   = "e2e-fl"
    container_port   = local.fl_port
  }

  health_check_grace_period_seconds = 60

  depends_on = [module.fl_nlb]
}
