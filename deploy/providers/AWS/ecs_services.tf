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

# ECS Fargate services — one per Central Hub component. Each service:
#   - Runs the corresponding task definition from ecs_tasks.tf
#   - Registers with AWS Cloud Map (Service Discovery) under flip.local
#   - Runs in private subnets (outbound Internet via NAT, AWS APIs via VPC endpoints)
#   - Assigns a public IP only if explicitly enabled (never by default)

############################
# Task-definition revision tracking (FLIP#751)
############################
# `make deploy-centralhub` rolls services forward by registering new task-definition
# revisions via the AWS CLI, outside Terraform. Each data source below looks up the
# latest ACTIVE revision of its family, and every service tracks
#   max(<Terraform-owned revision>, <latest ACTIVE revision>)
# (the provider-documented pattern for externally-updated task definitions), so an
# unrelated `terraform apply` no longer repoints services at the older Terraform
# revision — i.e. it does not roll back a CLI-deployed image. When Terraform itself
# changes a task-definition config (new revision registered at apply), its revision
# wins the max() and goes live with the env-file bootstrap image tag — re-run
# `make deploy-centralhub` afterwards to roll the sha-pinned image forward again.
# On first creation the reads are deferred to apply (they reference managed resources
# with pending changes), so a fresh environment bootstraps cleanly.
#
# Latest ACTIVE is not necessarily what a service RUNS — the two invariants that keep
# them converged: `make rollback-centralhub` DEREGISTERS the revision it rolls away
# from, and a failed deploy deregisters the revision it just registered. Without
# that, the next apply would re-adopt the abandoned (bad/unvalidated) revision.
# Also note `make apply` applies the saved plan.tfplan: a plan generated BEFORE a
# `make deploy-centralhub` snapshots the older revision, and applying it rolls the
# image back — re-run `make plan` after any CLI deploy.

data "aws_ecs_task_definition" "flip_api" {
  task_definition = aws_ecs_task_definition.flip_api.family
}

data "aws_ecs_task_definition" "fl_api_net_1" {
  count           = var.enable_efs ? 1 : 0
  task_definition = aws_ecs_task_definition.fl_api_net_1[0].family
}

data "aws_ecs_task_definition" "fl_server_net_1" {
  count           = var.enable_efs ? 1 : 0
  task_definition = aws_ecs_task_definition.fl_server_net_1[0].family
}

############################
# flip-api
############################

resource "aws_ecs_service" "flip_api" {
  count = var.enable_service_discovery ? 1 : 0

  name    = "flip-api"
  cluster = aws_ecs_cluster.flip.id
  # Track whichever revision is newer: Terraform's or the live one deployed by
  # `make deploy-centralhub` (see the revision-tracking block above, FLIP#751).
  task_definition = "${aws_ecs_task_definition.flip_api.family}:${max(
    aws_ecs_task_definition.flip_api.revision,
    data.aws_ecs_task_definition.flip_api.revision,
  )}"
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = var.ecs_exec_enabled

  network_configuration {
    subnets          = local.app_subnet_ids
    security_groups  = [aws_security_group.ecs_flip_api.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.flip_api[0].arn
  }

  # Register each running task's ENI IP with the ALB target group so
  # /api/* on the public ALB reaches the Fargate task. Without this block
  # the ECS service stays invisible to the ALB and the listener rule
  # falls back to its default action (or stale legacy target).
  load_balancer {
    target_group_arn = aws_lb_target_group.ecs_flip_api.arn
    container_name   = "flip-api"
    container_port   = local.api_container_port
  }

  health_check_grace_period_seconds  = 120
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [
    aws_ecs_task_definition.flip_api,
    aws_lb_listener_rule.api_routing,
  ]
}

############################
# fl-api-net-1
############################

resource "aws_ecs_service" "fl_api_net_1" {
  count = var.enable_service_discovery ? 1 : 0

  name    = "fl-api-net-1"
  cluster = aws_ecs_cluster.flip.id
  # Track whichever revision is newer: Terraform's or the live one deployed by
  # `make deploy-centralhub` (see the revision-tracking block above, FLIP#751).
  task_definition = "${aws_ecs_task_definition.fl_api_net_1[0].family}:${max(
    aws_ecs_task_definition.fl_api_net_1[0].revision,
    data.aws_ecs_task_definition.fl_api_net_1[0].revision,
  )}"
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = var.ecs_exec_enabled

  network_configuration {
    subnets          = local.app_subnet_ids
    security_groups  = [aws_security_group.ecs_fl_api.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.fl_api[0].arn
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [
    aws_ecs_task_definition.fl_api_net_1[0],
    null_resource.provision_efs_certs[0],
  ]
}

############################
# fl-server-net-1
############################

resource "aws_ecs_service" "fl_server_net_1" {
  count = var.enable_service_discovery ? 1 : 0

  name    = "fl-server-net-1"
  cluster = aws_ecs_cluster.flip.id
  # Track whichever revision is newer: Terraform's or the live one deployed by
  # `make deploy-centralhub` (see the revision-tracking block above, FLIP#751).
  task_definition = "${aws_ecs_task_definition.fl_server_net_1[0].family}:${max(
    aws_ecs_task_definition.fl_server_net_1[0].revision,
    data.aws_ecs_task_definition.fl_server_net_1[0].revision,
  )}"
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = var.ecs_exec_enabled

  network_configuration {
    subnets          = local.app_subnet_ids
    security_groups  = [aws_security_group.ecs_fl_server.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.fl_server[0].arn
  }

  # Register the running task's ENI IP with the NLB target group so FL
  # clients reaching fl.<env>.flip.aicentre.co.uk:8002 hit the Fargate task
  # over gRPC. NLB on TCP forwards the gRPC stream untouched.
  # Skipped on LZA (FLIP#749): there is no NLB there yet — the service still
  # deploys, it just has no inbound FL path until the WP2 ingress decision.
  dynamic "load_balancer" {
    for_each = var.lza_managed_network ? [] : [1]
    content {
      target_group_arn = aws_lb_target_group.ecs_fl_server_tcp[0].arn
      container_name   = "fl-server-net-1"
      # Backend-dependent container port (Flower: SuperLink Fleet 9092); the
      # NLB listener port trusts connect to stays var.FL_SERVER_PORT.
      container_port = local.fl_server_container_port
    }
  }

  # The grace period is only valid on services with a load balancer — ECS
  # rejects it outright when the block above is skipped on LZA.
  health_check_grace_period_seconds  = var.lza_managed_network ? null : 120
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [
    aws_ecs_task_definition.fl_server_net_1[0],
    null_resource.provision_efs_certs[0],
  ]
}
