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
# flip-api ECS Service
# Exposed via ALB on API_PORT (8080) → container port 8000
############################

resource "aws_ecs_service" "flip_api" {
  name            = "flip-api-service"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.flip_api.arn
  desired_count   = var.ecs_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.ecs_flip_api.arn
    container_name   = "flip-api"
    container_port   = 8000
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # Wait for ALB target group and listener rule to exist before creating the service
  depends_on = [
    aws_lb_listener_rule.ecs_flip_api_https,
    aws_lb_listener_rule.ecs_flip_api_api_port
  ]

  lifecycle {
    # Allow ECS to adjust desired_count independently (e.g. via auto-scaling)
    ignore_changes = [desired_count]
  }

  tags = {
    Service = "flip-api"
  }
}

############################
# trust-api ECS Service
# Internal service; the Central Hub reaches it via ECS service discovery or
# the private IP assigned by awsvpc. No ALB integration for now — trust-api
# runs alongside nginx-tls (TLS termination) in the Trust EC2 environment
# and will be ALB-integrated in a follow-up migration phase.
############################

resource "aws_ecs_service" "trust_api" {
  name            = "trust-api-service"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.trust_api.arn
  desired_count   = var.ecs_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = {
    Service = "trust-api"
  }
}

############################
# imaging-api ECS Service
# Internal service; called by trust-api within the VPC.
# No direct ALB or public endpoint.
############################

resource "aws_ecs_service" "imaging_api" {
  name            = "imaging-api-service"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.imaging_api.arn
  desired_count   = var.ecs_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = {
    Service = "imaging-api"
  }
}

############################
# data-access-api ECS Service
# Internal service; called by trust-api within the VPC.
# No direct ALB or public endpoint.
############################

resource "aws_ecs_service" "data_access_api" {
  name            = "data-access-api-service"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.data_access_api.arn
  desired_count   = var.ecs_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }

  tags = {
    Service = "data-access-api"
  }
}

############################
# ALB Target Group for ECS flip-api
# Target type "ip" is required for Fargate awsvpc tasks; ECS registers
# the task ENI IP addresses automatically.
############################

resource "aws_lb_target_group" "ecs_flip_api" {
  name        = "ecs-flip-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = module.flip_vpc.vpc_id
  target_type = "ip"

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = "/api/health"
    port                = "traffic-port"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 3
    unhealthy_threshold = 3
  }

  tags = {
    Service = "flip-api"
  }
}

############################
# ALB Listener Rules for ECS flip-api
#
# Two rules route traffic to ECS:
#   1. HTTPS (443) /api and /api/* paths — priority 50 (overrides EC2 rule at 98)
#   2. API port (8080) catch-all       — priority 10 (only rule on this listener)
#
# The existing EC2 target groups remain for rollback: revert by removing or
# lowering the priority of these rules and restoring the EC2 rules.
############################

resource "aws_lb_listener_rule" "ecs_flip_api_https" {
  listener_arn = module.alb.listeners["https-listener"].arn
  priority     = 50

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ecs_flip_api.arn
  }

  condition {
    path_pattern {
      values = ["/api", "/api/*"]
    }
  }
}

resource "aws_lb_listener_rule" "ecs_flip_api_api_port" {
  listener_arn = module.alb.listeners["api-listener"].arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ecs_flip_api.arn
  }

  condition {
    path_pattern {
      values = ["/*"]
    }
  }
}
