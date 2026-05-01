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
# flip-api
############################

resource "aws_ecs_service" "flip_api" {
  name            = "flip-api"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.flip_api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_flip_api.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.flip_api[0].arn
  }

  health_check_grace_period_seconds = 120
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [
    aws_ecs_task_definition.flip_api,
  ]
}

############################
# fl-api-net-1
############################

resource "aws_ecs_service" "fl_api_net_1" {
  name            = "fl-api-net-1"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.fl_api_net_1.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_fl_api.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.fl_api[0].arn
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [
    aws_ecs_task_definition.fl_api_net_1,
    null_resource.provision_efs_certs,
  ]
}

############################
# fl-server-net-1
############################

resource "aws_ecs_service" "fl_server_net_1" {
  name            = "fl-server-net-1"
  cluster         = aws_ecs_cluster.flip.id
  task_definition = aws_ecs_task_definition.fl_server_net_1.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.flip_vpc.private_subnets
    security_groups  = [aws_security_group.ecs_fl_server.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.fl_server[0].arn
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  depends_on = [
    aws_ecs_task_definition.fl_server_net_1,
    null_resource.provision_efs_certs,
  ]
}
