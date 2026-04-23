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
# ECS Cluster
############################

resource "aws_ecs_cluster" "flip" {
  name = var.ecs_cluster_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = var.ecs_cluster_name
  }
}

resource "aws_ecs_cluster_capacity_providers" "flip" {
  cluster_name       = aws_ecs_cluster.flip.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE"
  }
}

############################
# CloudWatch Log Groups
############################

resource "aws_cloudwatch_log_group" "ecs_flip_api" {
  name              = "/ecs/flip-api"
  retention_in_days = 90
}

############################
# FL Service Log Groups (per network)
############################

locals {
  fl_networks      = jsondecode(var.net_endpoints)
  fl_network_names = [for k, _ in local.fl_networks : k]
}

resource "aws_cloudwatch_log_group" "ecs_fl_api" {
  for_each = local.fl_networks

  name              = "/ecs/fl-api-${each.key}"
  retention_in_days = 90
}

resource "aws_cloudwatch_log_group" "ecs_fl_server" {
  for_each = local.fl_networks

  name              = "/ecs/fl-server-${each.key}"
  retention_in_days = 90
}
