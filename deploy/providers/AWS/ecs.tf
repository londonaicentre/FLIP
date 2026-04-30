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
  name = "flip-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# FARGATE only. FARGATE_SPOT is intentionally omitted: a capacity provider that
# no service references is dead config and was flagged in the v1 review.
resource "aws_ecs_cluster_capacity_providers" "flip" {
  cluster_name       = aws_ecs_cluster.flip.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }
}

############################
# CloudWatch log groups
############################

resource "aws_cloudwatch_log_group" "ecs_flip_api" {
  name              = "/ecs/flip-api"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "ecs_fl_api_net_1" {
  name              = "/ecs/fl-api-net-1"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "ecs_fl_server_net_1" {
  name              = "/ecs/fl-server-net-1"
  retention_in_days = 7
}
